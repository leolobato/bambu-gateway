"""TCP-JPEG camera proxy for Bambu A1/P1-family printers.

Holds one upstream TCP+TLS connection per printer and fans the decoded JPEG
frames out to N HTTP `multipart/x-mixed-replace` subscribers. The printer's
camera service only accepts one concurrent viewer, so multiplexing in the
gateway is required when more than one browser tab is watching.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from collections.abc import AsyncIterator
from typing import Literal

logger = logging.getLogger(__name__)

DEFAULT_PORT = 6000
DRAIN_GRACE_SECONDS = 5.0
RETRY_DELAY_SECONDS = 2.0


def build_auth_packet(access_code: str) -> bytes:
    """Build the 80-byte LAN binary auth packet.

    Layout (verified against panda-be-free / iOS `BambuTCPJPEGFeed.swift`):
      [0..3]   = 0x40 0x00 0x00 0x00     magic / packet type
      [4..7]   = 0x00 0x30 0x00 0x00     length marker (LE 0x3000)
      [8..15]  = 0
      [16..19] = "bblp"                  username
      [20..47] = 0
      [48..79] = access code (UTF-8, ≤32 bytes, zero-padded)
    """
    packet = bytearray(80)
    packet[0] = 0x40
    packet[5] = 0x30
    packet[16:20] = b"bblp"
    code_bytes = access_code.encode("utf-8")[:32]
    packet[48:48 + len(code_bytes)] = code_bytes
    return bytes(packet)


HEADER_SIZE = 16
"""Bambu TCP-JPEG frame header is fixed-size: 4 bytes LE length + 12 unused."""


class FrameParser:
    """Streaming parser for Bambu's `[16-byte header][JPEG]` framing.

    Accepts arbitrary byte chunks (TCP doesn't preserve message boundaries)
    and returns the list of complete JPEG payloads decoded so far.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._expected_len: int | None = None

    def feed(self, data: bytes) -> list[bytes]:
        """Append `data` and return any whole JPEG payloads now available."""
        self._buffer += data
        out: list[bytes] = []
        while True:
            if self._expected_len is None:
                if len(self._buffer) < HEADER_SIZE:
                    break
                length = (
                    self._buffer[0]
                    | (self._buffer[1] << 8)
                    | (self._buffer[2] << 16)
                    | (self._buffer[3] << 24)
                )
                self._expected_len = length
                del self._buffer[:HEADER_SIZE]
            assert self._expected_len is not None
            if len(self._buffer) < self._expected_len:
                break
            jpeg = bytes(self._buffer[:self._expected_len])
            del self._buffer[:self._expected_len]
            self._expected_len = None
            out.append(jpeg)
        return out


CameraState = Literal["idle", "connecting", "streaming", "failed", "stopped"]


class CameraProxy:
    """One-per-printer fan-out from a single upstream TCP-JPEG connection.

    Subscribers attach via :meth:`subscribe` and receive every JPEG frame
    delivered after they attached. New subscribers also receive the most
    recent cached frame immediately so the UI doesn't see a black tile
    while waiting for the next decode.

    Upstream connection lifecycle:
      - First subscriber → connect (TLS), send auth, read frames in a loop.
      - On error/EOF → set state=failed, sleep `retry_delay`, retry while
        any subscribers remain.
      - Last subscriber leaves → schedule a `drain_grace` timer; on expiry
        cancel the upstream task. New subscriber within the window cancels
        the drain so quick tab refreshes don't re-handshake.
    """

    def __init__(
        self,
        ip: str,
        access_code: str,
        *,
        port: int = DEFAULT_PORT,
        use_tls: bool = True,
        queue_maxsize: int = 2,
        drain_grace: float = DRAIN_GRACE_SECONDS,
        retry_delay: float = RETRY_DELAY_SECONDS,
    ) -> None:
        self._ip = ip
        self._access_code = access_code
        self._port = port
        self._use_tls = use_tls
        self._queue_maxsize = queue_maxsize
        self._drain_grace = drain_grace
        self._retry_delay = retry_delay

        self._subscribers: set[asyncio.Queue[bytes]] = set()
        self._latest_frame: bytes | None = None

        self._state: CameraState = "idle"
        self._error: str | None = None
        self._last_frame_at: float | None = None

        self._upstream_task: asyncio.Task | None = None
        self._drain_task: asyncio.Task | None = None
        self._stopped = False

    # ------------------------------------------------------------------
    # Public surface

    @property
    def state(self) -> CameraState:
        return self._state

    def status(self) -> dict:
        return {
            "state": self._state,
            "error": self._error,
            "last_frame_at": self._last_frame_at,
        }

    async def subscribe(self) -> AsyncIterator[bytes]:
        """Yield JPEG frames as they arrive. Cleans up on cancellation/exit."""
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=self._queue_maxsize)
        self._subscribers.add(queue)
        self._cancel_drain()
        self._ensure_upstream()
        try:
            if self._latest_frame is not None:
                queue.put_nowait(self._latest_frame)
            while True:
                frame = await queue.get()
                yield frame
        finally:
            self._subscribers.discard(queue)
            if not self._subscribers:
                self._schedule_drain()

    async def stop(self) -> None:
        """Cancel everything and mark the proxy permanently stopped."""
        self._stopped = True
        await self._cancel_drain_async()
        if self._upstream_task is not None and not self._upstream_task.done():
            self._upstream_task.cancel()
            try:
                await self._upstream_task
            except (asyncio.CancelledError, Exception):
                pass
        self._upstream_task = None
        # Yield to the event loop so any pending async-generator finalizers
        # (subscribe()'s finally block) can run, then cancel any drain task
        # they may have created.
        await asyncio.sleep(0)
        await self._cancel_drain_async()

    # ------------------------------------------------------------------
    # Internal

    def _publish(self, frame: bytes) -> None:
        """Cache the frame as latest and push to all subscribers.

        If a subscriber's queue is full, drop its oldest pending frame so
        a slow client can't stall the upstream loop or other subscribers.
        """
        self._latest_frame = frame
        loop = asyncio.get_running_loop()
        self._last_frame_at = loop.time()
        for q in self._subscribers:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(frame)

    def _ensure_upstream(self) -> None:
        if self._stopped:
            return
        if self._upstream_task is not None and not self._upstream_task.done():
            return
        self._upstream_task = asyncio.create_task(self._run_upstream())

    def _schedule_drain(self) -> None:
        # No upstream running → nothing to drain.
        if self._upstream_task is None or self._upstream_task.done():
            return
        if self._drain_task is not None and not self._drain_task.done():
            return
        self._drain_task = asyncio.create_task(self._drain_after_grace())

    def _cancel_drain(self) -> None:
        # Sync cancel is safe even if drain has already started: drain calls
        # `_upstream_task.cancel()` BEFORE awaiting it, so the upstream task is
        # cancelled regardless of when this cancel lands. New subscribers then
        # create a fresh upstream task via _ensure_upstream.
        if self._drain_task is not None and not self._drain_task.done():
            self._drain_task.cancel()
        self._drain_task = None

    async def _cancel_drain_async(self) -> None:
        """Cancel the drain task and await it so no pending-task warnings occur."""
        task = self._drain_task
        self._drain_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _drain_after_grace(self) -> None:
        try:
            await asyncio.sleep(self._drain_grace)
        except asyncio.CancelledError:
            return
        if self._subscribers:
            return
        if self._upstream_task is not None and not self._upstream_task.done():
            self._upstream_task.cancel()
            try:
                await self._upstream_task
            except (asyncio.CancelledError, Exception):
                pass
        self._upstream_task = None
        self._state = "idle"

    async def _run_upstream(self) -> None:
        """Connect, auth, read frames. Retry forever while subscribers exist."""
        while not self._stopped:
            self._state = "connecting"
            self._error = None
            try:
                await self._stream_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — surface any error to status
                self._state = "failed"
                self._error = str(exc) or exc.__class__.__name__
                logger.warning("Camera upstream failed for %s: %s", self._ip, self._error)
            if self._stopped or not self._subscribers:
                return
            try:
                await asyncio.sleep(self._retry_delay)
            except asyncio.CancelledError:
                return

    async def _stream_once(self) -> None:
        ssl_context: ssl.SSLContext | None = None
        if self._use_tls:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            # Bambu printers serve self-signed certs — no CA chain to verify against.
            ssl_context.verify_mode = ssl.CERT_NONE

        reader, writer = await asyncio.open_connection(
            host=self._ip, port=self._port, ssl=ssl_context,
        )
        try:
            writer.write(build_auth_packet(self._access_code))
            await writer.drain()

            parser = FrameParser()
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    raise ConnectionError("stream ended")
                for jpeg in parser.feed(chunk):
                    if self._state != "streaming":
                        self._state = "streaming"
                    self._publish(jpeg)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
