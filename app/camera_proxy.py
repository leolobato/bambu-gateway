"""TCP-JPEG camera proxy for Bambu A1/P1-family printers.

Holds one upstream TCP+TLS connection per printer and fans the decoded JPEG
frames out to N HTTP `multipart/x-mixed-replace` subscribers. The printer's
camera service only accepts one concurrent viewer, so multiplexing in the
gateway is required when more than one browser tab is watching.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Literal


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


CameraState = Literal["idle", "connecting", "streaming", "failed"]


class CameraProxy:
    """One-per-printer fan-out from a single upstream TCP-JPEG connection.

    Subscribers attach via :meth:`subscribe` and receive every JPEG frame
    delivered after they attached. New subscribers also receive the most
    recent cached frame immediately so the UI doesn't see a black tile
    while waiting for the next decode.

    The upstream connection is started lazily on the first subscriber and
    stopped 5s after the last subscriber leaves (Task 4).
    """

    def __init__(
        self,
        ip: str,
        access_code: str,
        queue_maxsize: int = 2,
    ) -> None:
        self._ip = ip
        self._access_code = access_code
        self._queue_maxsize = queue_maxsize

        self._subscribers: set[asyncio.Queue[bytes]] = set()
        self._latest_frame: bytes | None = None

        self._state: CameraState = "idle"
        self._error: str | None = None
        self._last_frame_at: float | None = None

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
        try:
            if self._latest_frame is not None:
                queue.put_nowait(self._latest_frame)
            while True:
                frame = await queue.get()
                yield frame
        finally:
            self._subscribers.discard(queue)

    # ------------------------------------------------------------------
    # Internal — used by the upstream loop (Task 4) and unit tests.

    def _publish(self, frame: bytes) -> None:
        """Cache the frame as latest and push to all subscribers.

        If a subscriber's queue is full, drop its oldest pending frame so
        a slow client can't stall the upstream loop or other subscribers.
        """
        self._latest_frame = frame
        loop = asyncio.get_event_loop()
        self._last_frame_at = loop.time()
        for q in self._subscribers:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(frame)
