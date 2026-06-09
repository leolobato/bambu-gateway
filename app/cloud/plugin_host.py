"""Async subprocess manager + JSONL RPC client for the C++ host binary."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any, Mapping

logger = logging.getLogger("bambu.cloud.host")


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
        import os

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
        )
        self._reader_task = asyncio.create_task(
            self._read_loop(), name="bambu-cloud-host-reader"
        )
        asyncio.create_task(
            self._drain_stderr(), name="bambu-cloud-host-stderr"
        )

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
            self._pending.pop(rid, None)
            raise PluginHostError(
                f"RPC {method} timed out after {timeout:g}s"
            ) from None

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            async for raw in self._proc.stdout:
                if not raw:
                    break
                try:
                    msg = json.loads(raw.decode("utf-8").rstrip("\n"))
                except json.JSONDecodeError as exc:
                    logger.error("malformed frame from host: %s", exc)
                    continue
                rid = msg.get("id")
                fut = self._pending.pop(rid, None) if rid is not None else None
                if fut is None:
                    logger.warning("unmatched response id=%r", rid)
                    continue
                if "error" in msg:
                    err = msg["error"]
                    fut.set_exception(
                        PluginHostError(
                            err.get("message", "unknown host error")
                        )
                    )
                else:
                    fut.set_result(msg.get("result"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("host read loop crashed: %s", exc)
        finally:
            # Subprocess closed stdout — fail any remaining waiters.
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(
                        PluginHostError("host stdout closed")
                    )
            self._pending.clear()

    async def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        async for raw in self._proc.stderr:
            if not raw:
                break
            logger.info("host: %s", raw.decode("utf-8", errors="replace").rstrip())
