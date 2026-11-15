"""Async subprocess manager + JSONL RPC client for the C++ host binary."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from contextlib import suppress
from typing import Any, Mapping

logger = logging.getLogger("bambu.cloud.host")

# Substrings that mark a plugin stderr line as a failure worth surfacing at
# WARNING. Everything else — including the ~1Hz install_device_cert keepalive
# and the send_message rc=0 confirmations — is routine chatter logged at DEBUG.
_STDERR_FAILURE_MARKERS = ("skipped", "missing", "error", "fail", "null", "rc=-")


def _stderr_is_failure(line: str) -> bool:
    low = line.lower()
    return any(marker in low for marker in _STDERR_FAILURE_MARKERS)


class PluginHostError(RuntimeError):
    """Raised when the host subprocess returns an error or dies."""


class PluginHost:
    """Manages a child process speaking JSONL RPC over stdin/stdout.

    Usage::

        async with PluginHost(cmd=["/usr/local/bin/bambu_cloud_host"]) as host:
            await host.call("init_plugin", {})
            await host.call("change_user", {"canonical_login": "..."})

    Concurrent ``call()``s are safe; responses are routed to the right awaiter
    by request id.
    """

    def __init__(
        self,
        *,
        cmd: list[str],
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._cmd = list(cmd)
        self._env = dict(env) if env else None
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._closed = False
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "PluginHost":
        await self.start()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.stop()

    async def start(self) -> None:
        full_env = os.environ.copy()
        if self._env:
            full_env.update(self._env)
        self._proc = await asyncio.create_subprocess_exec(
            *self._cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=full_env,
            # One bridge.poll_events response can carry several full
            # push_all payloads on a single JSONL line; asyncio's default
            # 64 KiB readline limit would kill the reader.
            limit=16 * 1024 * 1024,
            # Own process group: the Bambu plugin forks a helper child, which
            # stop() must take down too, so a restart() doesn't leave an
            # orphan holding a second plugin/cloud connection.
            start_new_session=True,
        )
        self._reader_task = asyncio.create_task(
            self._read_loop(), name="bambu-cloud-host-reader"
        )
        asyncio.create_task(
            self._drain_stderr(), name="bambu-cloud-host-stderr"
        )

    @property
    def is_healthy(self) -> bool:
        """True while the subprocess runs AND its stdout is being read.

        Either one failing makes every ``call()`` fail, and nothing else
        notices — callers like the EventPump just retry forever.
        """
        return (
            not self._closed
            and self._proc is not None
            and self._proc.returncode is None
            and self._reader_task is not None
            and not self._reader_task.done()
        )

    async def restart(self) -> None:
        """Replace a dead/wedged subprocess with a fresh one, in place.

        The object identity is kept on purpose: the EventPump, cloud printer
        clients and keepalive all hold this instance. The new process has no
        plugin state, so the caller must re-run the boot sequence.
        """
        await self.stop()
        self._closed = False
        await self.start()

    async def stop(self) -> None:
        self._closed = True
        if self._proc and self._proc.returncode is None:
            with suppress(ProcessLookupError):
                if self._proc.stdin and not self._proc.stdin.is_closing():
                    self._proc.stdin.close()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("host did not exit; killing")
                self._proc.kill()
                await self._proc.wait()
        if self._proc is not None:
            # Reap helper children left in the host's process group.
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(self._proc.pid, signal.SIGKILL)
        if self._reader_task:
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader_task
        # Fail any still-pending requests.
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(
                    PluginHostError("host shutting down")
                )
        self._pending.clear()

    async def call(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float | None = 60.0,
    ) -> Any:
        if self._closed or self._proc is None:
            raise PluginHostError("host not running")
        if self._proc.returncode is not None:
            raise PluginHostError(
                f"host died (exit code {self._proc.returncode})"
            )
        if self._reader_task is not None and self._reader_task.done():
            # The reader can die while the process lives (oversized frame,
            # unexpected I/O error); without this guard the future below
            # would never resolve.
            raise PluginHostError("host reader terminated")

        async with self._lock:
            self._next_id += 1
            rid = self._next_id

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut

        request = {"id": rid, "method": method, "params": params}
        line = (json.dumps(request) + "\n").encode("utf-8")
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.write(line)
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            self._pending.pop(rid, None)
            raise PluginHostError(f"host stdin closed: {exc}") from exc

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise PluginHostError(
                f"RPC {method} timed out after {timeout:g}s"
            ) from None
        finally:
            # Covers timeout AND caller cancellation: a late reply for this id
            # then finds no waiter instead of a cancelled future.
            self._pending.pop(rid, None)

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        reason = "stdout closed (EOF)"
        try:
            async for raw in self._proc.stdout:
                if not raw:
                    break
                # One bad frame must never end the loop: a dead reader with a
                # live process freezes every cloud report until a restart.
                try:
                    self._dispatch_frame(raw)
                except Exception:
                    logger.exception(
                        "dropping unprocessable frame from host: %r", raw[:200]
                    )
        except asyncio.CancelledError:
            reason = "cancelled"
            raise
        except Exception as exc:
            reason = f"crashed: {exc!r}"
            logger.exception("host read loop crashed: %s", exc)
        finally:
            if not self._closed:
                logger.error("host reader stopped: %s", reason)
            # Subprocess closed stdout — fail any remaining waiters.
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(
                        PluginHostError("host stdout closed")
                    )
            self._pending.clear()

    def _dispatch_frame(self, raw: bytes) -> None:
        try:
            msg = json.loads(raw.decode("utf-8").rstrip("\n"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.error("malformed frame from host: %s", exc)
            return
        if not isinstance(msg, dict):
            logger.error("non-object frame from host: %r", raw[:200])
            return
        rid = msg.get("id")
        fut = self._pending.pop(rid, None) if rid is not None else None
        if fut is None:
            logger.warning("unmatched response id=%r", rid)
            return
        if fut.done():
            # The caller gave up (timeout/cancel) before the reply landed.
            return
        if "error" in msg:
            err = msg["error"]
            message = (
                err.get("message", "unknown host error")
                if isinstance(err, dict) else str(err)
            )
            fut.set_exception(PluginHostError(message))
        else:
            fut.set_result(msg.get("result"))

    async def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        async for raw in self._proc.stderr:
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip()
            # Routine plugin chatter (the per-second install_device_cert
            # keepalive, send_message rc=0) is noise at INFO — keep it at DEBUG
            # and only surface a line when it signals a failure.
            if _stderr_is_failure(line):
                logger.warning("host: %s", line)
            else:
                logger.debug("host: %s", line)
