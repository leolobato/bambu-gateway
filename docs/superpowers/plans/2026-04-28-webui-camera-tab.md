# Web UI Camera Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `Camera` tab to the React web UI that mirrors the iOS Camera tab for A1/P1-family printers, including a printer picker, chamber-light toggle, and a live MJPEG feed with status, retry, and fullscreen.

**Architecture:** A new `CameraProxy` (asyncio) per printer holds **one** TCP-JPEG connection to the printer (the printer accepts one viewer at a time) and fans JPEG frames out to N HTTP `multipart/x-mixed-replace` subscribers. The browser renders the feed with a plain `<img>`; a 2s status poll drives the UI's connecting/streaming/failed overlays. Lazy-start on first subscriber, 5s grace-stop on last unsubscribe.

**Tech Stack:**
- Backend: Python 3.11+ asyncio, FastAPI `StreamingResponse`, stdlib `ssl`/`asyncio.open_connection`, pytest.
- Frontend: React 18, TanStack Query, Tailwind, Vite (`web/`).

Spec: `docs/superpowers/specs/2026-04-28-webui-camera-tab-design.md`.

---

## File Structure

**Created:**
- `app/camera_proxy.py` — `CameraProxy` class (one per printer): TLS upstream, frame parser, subscriber fan-out, lifecycle (lazy start, drain on idle).
- `tests/test_camera_proxy.py` — unit tests for auth packet, frame parser, subscribe/unsubscribe lifecycle, drain timing.
- `tests/test_camera_endpoints.py` — integration tests: `GET /camera/stream.mjpg` and `GET /camera/status` against a fake asyncio TCP server.
- `web/src/lib/api/camera.ts` — typed client for `/camera/status` + URL helper for `/stream.mjpg`.
- `web/src/routes/camera.tsx` — Camera route component.
- `web/src/components/camera/camera-feed.tsx` — feed tile (status dot, `<img>`, overlays, fullscreen).
- `web/src/components/camera/chamber-light-toggle.tsx` — pill button.

**Modified:**
- `app/printer_service.py` — add `_proxies` dict, `get_camera_proxy()`, integrate with `sync_printers()` and `stop()`.
- `app/main.py` — register two new GET routes, expose `/api/printers/{id}/camera/...`.
- `app/models.py` — add `CameraStatusResponse` Pydantic model.
- `web/src/lib/api/printer-commands.ts` — add `setChamberLight()`.
- `web/src/App.tsx` — add `{ path: 'camera', element: <CameraRoute /> }` route.
- `web/src/components/app-shell.tsx` — add `Camera` tab between Dashboard and Print.

---

## Task 1: Camera proxy skeleton with auth packet

**Files:**
- Create: `app/camera_proxy.py`
- Create: `tests/test_camera_proxy.py`

- [ ] **Step 1: Write the failing test for the auth packet builder**

```python
# tests/test_camera_proxy.py
"""Tests for app/camera_proxy.py — TCP-JPEG proxy."""

from __future__ import annotations

from app.camera_proxy import build_auth_packet


def test_buildAuthPacket_layoutMatchesIOS():
    """Auth packet must be byte-identical to the iOS BambuTCPJPEGFeed.swift handshake."""
    packet = build_auth_packet(access_code="12345678")

    assert len(packet) == 80
    # Magic header (bytes 0-3)
    assert packet[0:4] == bytes([0x40, 0x00, 0x00, 0x00])
    # Length marker, little-endian 0x3000 (bytes 4-7)
    assert packet[4:8] == bytes([0x00, 0x30, 0x00, 0x00])
    # Bytes 8..15 are zero
    assert packet[8:16] == bytes(8)
    # Username "bblp" (bytes 16-19)
    assert packet[16:20] == b"bblp"
    # Bytes 20..47 are zero
    assert packet[20:48] == bytes(28)
    # Access code (bytes 48..55, then zero-padded to 80)
    assert packet[48:56] == b"12345678"
    assert packet[56:80] == bytes(24)


def test_buildAuthPacket_truncatesLongAccessCodeTo32Bytes():
    long_code = "x" * 50
    packet = build_auth_packet(access_code=long_code)
    assert len(packet) == 80
    assert packet[48:80] == b"x" * 32  # truncated to 32 bytes
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `.venv/bin/pytest tests/test_camera_proxy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.camera_proxy'`

- [ ] **Step 3: Implement `build_auth_packet`**

```python
# app/camera_proxy.py
"""TCP-JPEG camera proxy for Bambu A1/P1-family printers.

Holds one upstream TCP+TLS connection per printer and fans the decoded JPEG
frames out to N HTTP `multipart/x-mixed-replace` subscribers. The printer's
camera service only accepts one concurrent viewer, so multiplexing in the
gateway is required when more than one browser tab is watching.
"""

from __future__ import annotations


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
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `.venv/bin/pytest tests/test_camera_proxy.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/camera_proxy.py tests/test_camera_proxy.py
git commit -m "Add Bambu LAN camera auth packet builder"
```

---

## Task 2: JPEG frame parser

**Files:**
- Modify: `app/camera_proxy.py`
- Modify: `tests/test_camera_proxy.py`

The Bambu camera sends `[16-byte header][JPEG]` records back-to-back. The first 4 bytes of the header are the JPEG length (little-endian, unsigned 32-bit). The next 12 bytes are unused. We need a pure parser that accepts arbitrary chunks and emits whole JPEGs.

- [ ] **Step 1: Write the failing test for the parser**

Append to `tests/test_camera_proxy.py`:

```python
from app.camera_proxy import FrameParser


def _frame(jpeg: bytes) -> bytes:
    """Wrap a JPEG payload in the printer's 16-byte frame header."""
    n = len(jpeg)
    header = bytes([n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF, (n >> 24) & 0xFF])
    header += bytes(12)  # unused
    return header + jpeg


def test_frameParser_singleChunkSingleFrame_emitsJpeg():
    parser = FrameParser()
    jpeg = b"\xff\xd8" + b"A" * 100 + b"\xff\xd9"
    out = parser.feed(_frame(jpeg))
    assert out == [jpeg]


def test_frameParser_splitAcrossChunks_reassembles():
    parser = FrameParser()
    jpeg = b"\xff\xd8" + b"B" * 50 + b"\xff\xd9"
    payload = _frame(jpeg)
    # Feed 5 bytes at a time.
    out: list[bytes] = []
    for i in range(0, len(payload), 5):
        out.extend(parser.feed(payload[i:i + 5]))
    assert out == [jpeg]


def test_frameParser_multipleFramesInOneChunk_emitsAll():
    parser = FrameParser()
    a = b"\xff\xd8" + b"A" * 10 + b"\xff\xd9"
    b = b"\xff\xd8" + b"B" * 20 + b"\xff\xd9"
    out = parser.feed(_frame(a) + _frame(b))
    assert out == [a, b]


def test_frameParser_partialThenComplete_emitsOnceComplete():
    parser = FrameParser()
    jpeg = b"\xff\xd8" + b"C" * 30 + b"\xff\xd9"
    payload = _frame(jpeg)
    # Feed everything except the last byte first.
    assert parser.feed(payload[:-1]) == []
    # Feed the last byte; now it should emit.
    assert parser.feed(payload[-1:]) == [jpeg]
```

- [ ] **Step 2: Run the new tests to confirm they fail**

Run: `.venv/bin/pytest tests/test_camera_proxy.py -v -k frameParser`
Expected: FAIL with `ImportError: cannot import name 'FrameParser'`.

- [ ] **Step 3: Implement `FrameParser`**

Add to `app/camera_proxy.py` (after `build_auth_packet`):

```python
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
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `.venv/bin/pytest tests/test_camera_proxy.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add app/camera_proxy.py tests/test_camera_proxy.py
git commit -m "Parse Bambu TCP-JPEG frame stream"
```

---

## Task 3: CameraProxy state + subscribe/unsubscribe

This task implements the public-facing `CameraProxy` API without yet running an upstream connection. We isolate fan-out logic so it can be unit-tested by manually pushing frames.

**Files:**
- Modify: `app/camera_proxy.py`
- Modify: `tests/test_camera_proxy.py`

- [ ] **Step 1: Write the failing tests for subscribe/publish**

Append to `tests/test_camera_proxy.py`:

```python
import asyncio
import pytest

from app.camera_proxy import CameraProxy


@pytest.mark.asyncio
async def test_cameraProxy_subscribePublish_deliversFrames():
    proxy = CameraProxy(ip="127.0.0.1", access_code="x")
    received: list[bytes] = []

    async def consumer():
        async for frame in proxy.subscribe():
            received.append(frame)
            if len(received) == 2:
                break

    task = asyncio.create_task(consumer())
    # Let consumer subscribe.
    await asyncio.sleep(0)
    proxy._publish(b"frame-A")
    proxy._publish(b"frame-B")
    await asyncio.wait_for(task, timeout=1.0)
    assert received == [b"frame-A", b"frame-B"]


@pytest.mark.asyncio
async def test_cameraProxy_secondSubscriber_getsCachedLatestFrame():
    proxy = CameraProxy(ip="127.0.0.1", access_code="x")
    proxy._publish(b"latest")

    received: list[bytes] = []

    async def consumer():
        async for frame in proxy.subscribe():
            received.append(frame)
            break

    task = asyncio.create_task(consumer())
    await asyncio.wait_for(task, timeout=1.0)
    assert received == [b"latest"]


@pytest.mark.asyncio
async def test_cameraProxy_slowConsumer_dropsOldFramesNotNewest():
    proxy = CameraProxy(ip="127.0.0.1", access_code="x", queue_maxsize=2)
    received: list[bytes] = []

    async def slow_consumer():
        # Read three frames, but with no awaits between publishes the queue
        # fills and old frames must be dropped — the newest frame must arrive.
        async for frame in proxy.subscribe():
            received.append(frame)
            if len(received) == 3:
                break

    task = asyncio.create_task(slow_consumer())
    await asyncio.sleep(0)  # let it subscribe
    for i in range(10):
        proxy._publish(f"f{i}".encode())
    await asyncio.wait_for(task, timeout=1.0)
    assert received[-1] == b"f9"
    assert len(received) == 3
```

Add to `pytest.ini` if needed: confirm `asyncio_mode = auto` is set (run the tests; if pytest-asyncio is missing or not in auto mode, the tests will fail with a clear marker error and we'll fix it as part of this task).

- [ ] **Step 2: Run the tests; check for asyncio config issues**

Run: `.venv/bin/pytest tests/test_camera_proxy.py -v -k cameraProxy_`
Expected: FAIL with `ImportError: cannot import name 'CameraProxy'` (or asyncio fixture errors if pytest-asyncio is not configured — handle in step 3 if so).

If the failure is about `@pytest.mark.asyncio`, check `pytest.ini` for `asyncio_mode`:

```bash
cat pytest.ini
```

If `asyncio_mode = auto` is missing, add it under `[pytest]`. If pytest-asyncio isn't installed:

```bash
.venv/bin/pip install pytest-asyncio
echo "pytest-asyncio" >> requirements.txt  # only if it's a project dep we want pinned; else use a tests-extras file. For this repo, append to requirements.txt.
```

- [ ] **Step 3: Implement `CameraProxy` (no upstream yet)**

Append to `app/camera_proxy.py`:

```python
import asyncio
from collections.abc import AsyncIterator
from typing import Literal

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
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `.venv/bin/pytest tests/test_camera_proxy.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add app/camera_proxy.py tests/test_camera_proxy.py pytest.ini requirements.txt
git commit -m "Add CameraProxy fan-out with latest-frame cache"
```

---

## Task 4: Upstream loop + lazy start + drain

This task wires the proxy to a real TCP connection. We test it against an asyncio TCP server that simulates the printer's auth handshake and emits two synthetic frames.

**Files:**
- Modify: `app/camera_proxy.py`
- Modify: `tests/test_camera_proxy.py`

- [ ] **Step 1: Write the failing tests for upstream + lifecycle**

Append to `tests/test_camera_proxy.py`:

```python
async def _start_fake_camera_server(
    frames: list[bytes],
    *,
    expect_auth: bool = True,
) -> tuple[asyncio.AbstractServer, int]:
    """Start a localhost TCP server that mimics the Bambu camera handshake.

    Reads (and ignores) an 80-byte auth packet, then writes each frame in
    `frames` framed with the 16-byte length header. Returns (server, port).
    """
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        if expect_auth:
            await reader.readexactly(80)
        for jpeg in frames:
            n = len(jpeg)
            header = bytes([n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF, (n >> 24) & 0xFF]) + bytes(12)
            writer.write(header + jpeg)
            await writer.drain()
        await writer.drain()
        # Keep the connection open so the proxy stays in `streaming` until torn down.
        try:
            while True:
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, host="127.0.0.1", port=0)
    port = server.sockets[0].getsockname()[1]
    return server, port


@pytest.mark.asyncio
async def test_cameraProxy_subscribe_startsUpstream_yieldsFrames():
    jpeg_a = b"\xff\xd8frame-A\xff\xd9"
    jpeg_b = b"\xff\xd8frame-B\xff\xd9"
    server, port = await _start_fake_camera_server([jpeg_a, jpeg_b])

    try:
        proxy = CameraProxy(
            ip="127.0.0.1",
            access_code="abcd",
            port=port,
            use_tls=False,  # plain TCP for the fake server
            retry_delay=0.05,
        )

        received: list[bytes] = []

        async def consumer():
            async for frame in proxy.subscribe():
                received.append(frame)
                if len(received) == 2:
                    break

        await asyncio.wait_for(consumer(), timeout=2.0)
        assert received == [jpeg_a, jpeg_b]
        assert proxy.state == "streaming"
        assert proxy.status()["last_frame_at"] is not None
    finally:
        server.close()
        await server.wait_closed()
        await proxy.stop()


@pytest.mark.asyncio
async def test_cameraProxy_lastSubscriberLeaves_drainsAndStopsUpstream():
    jpeg = b"\xff\xd8x\xff\xd9"
    server, port = await _start_fake_camera_server([jpeg])

    try:
        proxy = CameraProxy(
            ip="127.0.0.1",
            access_code="abcd",
            port=port,
            use_tls=False,
            drain_grace=0.1,  # short grace for fast tests
            retry_delay=0.05,
        )

        async def quick_consumer():
            async for _ in proxy.subscribe():
                return

        await asyncio.wait_for(quick_consumer(), timeout=2.0)
        # Subscriber set is now empty; drain should fire after grace.
        await asyncio.sleep(0.3)
        assert proxy.state in {"idle", "stopped"}
        assert proxy._upstream_task is None or proxy._upstream_task.done()
    finally:
        server.close()
        await server.wait_closed()
        await proxy.stop()


@pytest.mark.asyncio
async def test_cameraProxy_unreachable_setsFailed():
    # Pick a port that nothing is listening on.
    proxy = CameraProxy(
        ip="127.0.0.1",
        access_code="abcd",
        port=1,  # almost certainly closed
        use_tls=False,
        retry_delay=0.05,
    )

    received: list[bytes] = []

    async def consumer():
        async for frame in proxy.subscribe():
            received.append(frame)

    task = asyncio.create_task(consumer())
    # Wait long enough for at least one connect attempt to fail.
    await asyncio.sleep(0.3)
    assert proxy.state == "failed"
    assert proxy.status()["error"] is not None
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await proxy.stop()
```

- [ ] **Step 2: Run the new tests to confirm they fail**

Run: `.venv/bin/pytest tests/test_camera_proxy.py -v -k upstream_or_lastSubscriber_or_unreachable`
Expected: FAIL — `CameraProxy.__init__()` doesn't accept `port`/`use_tls`/`drain_grace`/`retry_delay`, no `stop()` method, no upstream loop.

- [ ] **Step 3: Implement upstream + lifecycle**

Update `app/camera_proxy.py`. Replace the existing `CameraProxy` class with this version (preserves Task 3 behavior, adds upstream + drain):

```python
import logging
import ssl

logger = logging.getLogger(__name__)

DEFAULT_PORT = 6000
DRAIN_GRACE_SECONDS = 5.0
RETRY_DELAY_SECONDS = 2.0


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
        self._cancel_drain()
        if self._upstream_task is not None and not self._upstream_task.done():
            self._upstream_task.cancel()
            try:
                await self._upstream_task
            except (asyncio.CancelledError, Exception):
                pass
        self._upstream_task = None

    # ------------------------------------------------------------------
    # Internal

    def _publish(self, frame: bytes) -> None:
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

    def _ensure_upstream(self) -> None:
        if self._stopped:
            return
        if self._upstream_task is not None and not self._upstream_task.done():
            return
        self._upstream_task = asyncio.create_task(self._run_upstream())

    def _schedule_drain(self) -> None:
        if self._drain_task is not None and not self._drain_task.done():
            return
        self._drain_task = asyncio.create_task(self._drain_after_grace())

    def _cancel_drain(self) -> None:
        if self._drain_task is not None and not self._drain_task.done():
            self._drain_task.cancel()
        self._drain_task = None

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
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `.venv/bin/pytest tests/test_camera_proxy.py -v`
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add app/camera_proxy.py tests/test_camera_proxy.py
git commit -m "Connect Bambu camera proxy with lazy upstream and drain"
```

---

## Task 5: Wire proxies into PrinterService

**Files:**
- Modify: `app/printer_service.py`
- Modify: `tests/test_camera_and_light.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_camera_and_light.py`:

```python
@pytest.mark.asyncio
async def test_getCameraProxy_tcpJpegPrinter_returnsProxy():
    cfg = PrinterConfig(
        serial="01PXXX",
        ip="10.0.0.99",
        access_code="abcd",
        name="A1 Mini",
        machine_model="GM020",  # A1 Mini = tcp_jpeg
    )
    svc = PrinterService([cfg])
    try:
        proxy = svc.get_camera_proxy("01PXXX")
        assert proxy is not None
        # Same instance returned on repeat calls.
        assert svc.get_camera_proxy("01PXXX") is proxy
    finally:
        await svc.stop_async()


@pytest.mark.asyncio
async def test_getCameraProxy_rtspsPrinter_returnsNone():
    cfg = PrinterConfig(
        serial="X1S001",
        ip="10.0.0.50",
        access_code="abcd",
        name="X1C",
        machine_model="GM001",  # X1C = rtsps
    )
    svc = PrinterService([cfg])
    try:
        assert svc.get_camera_proxy("X1S001") is None
    finally:
        await svc.stop_async()


@pytest.mark.asyncio
async def test_getCameraProxy_unknownPrinter_returnsNone():
    svc = PrinterService([])
    try:
        assert svc.get_camera_proxy("MISSING") is None
    finally:
        await svc.stop_async()


@pytest.mark.asyncio
async def test_syncPrinters_ipChange_recreatesProxy():
    cfg = PrinterConfig(
        serial="01PXXX", ip="10.0.0.99", access_code="abcd",
        name="A1", machine_model="GM021",
    )
    svc = PrinterService([cfg])
    try:
        old = svc.get_camera_proxy("01PXXX")
        assert old is not None

        new_cfg = PrinterConfig(
            serial="01PXXX", ip="10.0.0.100", access_code="abcd",
            name="A1", machine_model="GM021",
        )
        svc.sync_printers([new_cfg])
        # Allow stop_async to run on the proxy.
        await asyncio.sleep(0)
        new = svc.get_camera_proxy("01PXXX")
        assert new is not None
        assert new is not old
    finally:
        await svc.stop_async()
```

Note: this also requires adding an async `stop_async()` to `PrinterService` because the existing `stop()` is synchronous (used by FastAPI lifespan, which is async — see step 3).

- [ ] **Step 2: Run the new tests to confirm they fail**

Run: `.venv/bin/pytest tests/test_camera_and_light.py -v -k getCameraProxy_or_syncPrinters_ipChange`
Expected: FAIL — `PrinterService` has no `get_camera_proxy` / `stop_async`.

- [ ] **Step 3: Implement**

In `app/printer_service.py`:

a) At the top of the file (with other imports):

```python
import asyncio

from app.camera_proxy import CameraProxy
```

b) In `PrinterService.__init__`, add:

```python
self._proxies: dict[str, CameraProxy] = {}
```

c) Add new methods before `default_printer_id`:

```python
def get_camera_proxy(self, printer_id: str) -> CameraProxy | None:
    """Return (and lazily create) the camera proxy for a printer.

    Returns None when the printer is unknown, has no IP/access code, or
    its transport isn't `tcp_jpeg`. RTSPS-family printers always return
    None — those are handled by a separate transcode pipeline (future).
    """
    if printer_id in self._proxies:
        return self._proxies[printer_id]
    config = self._configs.get(printer_id)
    if config is None or not config.ip or not config.access_code:
        return None
    transport = _classify_camera_transport(config.machine_model)
    if transport != "tcp_jpeg":
        return None
    proxy = CameraProxy(ip=config.ip, access_code=config.access_code)
    self._proxies[printer_id] = proxy
    return proxy

async def stop_async(self) -> None:
    """Stop all MQTT clients and camera proxies. Safe to call from async code."""
    self.stop()
    proxies = list(self._proxies.values())
    self._proxies.clear()
    for proxy in proxies:
        await proxy.stop()
```

d) In `sync_printers`, drop a removed printer's proxy and tear down a changed printer's proxy. Locate the existing `for serial in to_remove:` block and the changed-config branch inside `for serial in to_check:`, and update both:

```python
# In `for serial in to_remove:` — after del self._configs[serial]
proxy = self._proxies.pop(serial, None)
if proxy is not None:
    asyncio.create_task(proxy.stop())

# In `for serial in to_check:` — inside the `if old.ip != new.ip or old.access_code != new.access_code:` branch, after the new client is created:
proxy = self._proxies.pop(serial, None)
if proxy is not None:
    asyncio.create_task(proxy.stop())
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `.venv/bin/pytest tests/test_camera_and_light.py -v`
Expected: existing tests + 4 new ones all pass.

- [ ] **Step 5: Commit**

```bash
git add app/printer_service.py tests/test_camera_and_light.py
git commit -m "Manage one CameraProxy per A1/P1 printer"
```

---

## Task 6: HTTP endpoints — `/camera/stream.mjpg` and `/camera/status`

**Files:**
- Modify: `app/models.py`
- Modify: `app/main.py`
- Create: `tests/test_camera_endpoints.py`

- [ ] **Step 1: Write the failing endpoint tests**

```python
# tests/test_camera_endpoints.py
"""Integration tests for /api/printers/{id}/camera/stream.mjpg and /status."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import main as app_module
from app.config import PrinterConfig
from app.printer_service import PrinterService


async def _start_fake_camera_server(frames: list[bytes]) -> tuple[asyncio.AbstractServer, int]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        await reader.readexactly(80)
        for jpeg in frames:
            n = len(jpeg)
            header = bytes([n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF, (n >> 24) & 0xFF]) + bytes(12)
            writer.write(header + jpeg)
            await writer.drain()
        try:
            while True:
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    server = await asyncio.start_server(handle, host="127.0.0.1", port=0)
    return server, server.sockets[0].getsockname()[1]


@pytest.fixture
def patched_service(monkeypatch):
    """Install a real PrinterService with one A1 printer pointing to a fake server."""
    cfg = PrinterConfig(
        serial="01PXXX",
        ip="127.0.0.1",
        access_code="abcd",
        name="A1",
        machine_model="GM021",
    )
    svc = PrinterService([cfg])
    monkeypatch.setattr(app_module, "printer_service", svc)
    yield svc
    asyncio.get_event_loop().run_until_complete(svc.stop_async())


def test_cameraStatus_unknownPrinter_returns404(patched_service):
    client = TestClient(app_module.app)
    res = client.get("/api/printers/MISSING/camera/status")
    assert res.status_code == 404


def test_cameraStatus_rtspsPrinter_reportsUnsupported(monkeypatch):
    cfg = PrinterConfig(
        serial="X1S001", ip="10.0.0.50", access_code="abcd",
        name="X1C", machine_model="GM001",
    )
    svc = PrinterService([cfg])
    monkeypatch.setattr(app_module, "printer_service", svc)
    try:
        client = TestClient(app_module.app)
        res = client.get("/api/printers/X1S001/camera/status")
        assert res.status_code == 200
        body = res.json()
        assert body["state"] == "unsupported"
    finally:
        asyncio.get_event_loop().run_until_complete(svc.stop_async())


def test_cameraStream_mjpegEndpoint_emitsTwoParts(patched_service):
    jpeg_a = b"\xff\xd8AAA\xff\xd9"
    jpeg_b = b"\xff\xd8BBB\xff\xd9"

    async def run():
        server, port = await _start_fake_camera_server([jpeg_a, jpeg_b])
        # Override the proxy's port to point at the fake server.
        proxy = patched_service.get_camera_proxy("01PXXX")
        assert proxy is not None
        proxy._port = port
        proxy._use_tls = False
        return server

    server = asyncio.get_event_loop().run_until_complete(run())
    try:
        client = TestClient(app_module.app)
        with client.stream("GET", "/api/printers/01PXXX/camera/stream.mjpg") as res:
            assert res.status_code == 200
            assert "multipart/x-mixed-replace" in res.headers["content-type"]
            data = b""
            for chunk in res.iter_bytes():
                data += chunk
                if data.count(b"--frame\r\n") >= 2 and jpeg_a in data and jpeg_b in data:
                    break
            assert b"--frame\r\nContent-Type: image/jpeg" in data
            assert jpeg_a in data
            assert jpeg_b in data
    finally:
        server.close()
        asyncio.get_event_loop().run_until_complete(server.wait_closed())


def test_cameraStream_unsupportedPrinter_returns404(monkeypatch):
    cfg = PrinterConfig(
        serial="X1S001", ip="10.0.0.50", access_code="abcd",
        name="X1C", machine_model="GM001",
    )
    svc = PrinterService([cfg])
    monkeypatch.setattr(app_module, "printer_service", svc)
    try:
        client = TestClient(app_module.app)
        res = client.get("/api/printers/X1S001/camera/stream.mjpg")
        assert res.status_code == 404
    finally:
        asyncio.get_event_loop().run_until_complete(svc.stop_async())
```

- [ ] **Step 2: Run the new tests to confirm they fail**

Run: `.venv/bin/pytest tests/test_camera_endpoints.py -v`
Expected: FAIL — endpoints don't exist yet (404 from FastAPI route lookup).

- [ ] **Step 3: Add `CameraStatusResponse` to `app/models.py`**

Append (e.g. near the other response models around line 160):

```python
class CameraStatusResponse(BaseModel):
    """State of a printer's camera proxy.

    `state` is one of: ``unsupported``, ``idle``, ``connecting``,
    ``streaming``, ``failed``. ``unsupported`` is returned for printers
    without a camera or with the ``rtsps`` transport (no proxy is created
    for those, so there is no internal state to report).
    """

    state: str
    error: str | None = None
    last_frame_at: float | None = None
```

- [ ] **Step 4: Add the endpoints to `app/main.py`**

Add the import (in the `from app.models import (` block):

```python
CameraStatusResponse,
```

Add the routes (near the existing `set_printer_light` route at line 421):

```python
@app.get("/api/printers/{printer_id}/camera/status", response_model=CameraStatusResponse)
async def get_camera_status(printer_id: str):
    pid = _resolve_printer_id(printer_id)
    if printer_service.get_status(pid) is None:
        raise HTTPException(status_code=404, detail=f"Printer {pid} not found")
    proxy = printer_service.get_camera_proxy(pid)
    if proxy is None:
        return CameraStatusResponse(state="unsupported", error=None, last_frame_at=None)
    return CameraStatusResponse(**proxy.status())


@app.get("/api/printers/{printer_id}/camera/stream.mjpg")
async def get_camera_stream(printer_id: str):
    pid = _resolve_printer_id(printer_id)
    if printer_service.get_status(pid) is None:
        raise HTTPException(status_code=404, detail=f"Printer {pid} not found")
    proxy = printer_service.get_camera_proxy(pid)
    if proxy is None:
        raise HTTPException(status_code=404, detail="Camera not available for this printer")

    boundary = b"--frame\r\n"

    async def generator():
        try:
            async for jpeg in proxy.subscribe():
                yield (
                    boundary
                    + b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
                    + jpeg
                    + b"\r\n"
                )
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )
```

- [ ] **Step 5: Run the tests to confirm they pass**

Run: `.venv/bin/pytest tests/test_camera_endpoints.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/main.py tests/test_camera_endpoints.py
git commit -m "Expose camera MJPEG stream and status endpoints"
```

---

## Task 7: Frontend — API client and tab registration

**Files:**
- Create: `web/src/lib/api/camera.ts`
- Modify: `web/src/lib/api/printer-commands.ts`
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/app-shell.tsx`

We register the route and tab now (pointing at a placeholder route component) so we can iterate UI tasks against a navigable page.

- [ ] **Step 1: Create the camera API client**

```typescript
// web/src/lib/api/camera.ts
import { fetchJson } from './client';

export type CameraState = 'unsupported' | 'idle' | 'connecting' | 'streaming' | 'failed';

export interface CameraStatus {
  state: CameraState;
  error: string | null;
  last_frame_at: number | null;
}

export async function getCameraStatus(printerId: string): Promise<CameraStatus> {
  return fetchJson<CameraStatus>(
    `/api/printers/${encodeURIComponent(printerId)}/camera/status`,
  );
}

/** Build a cache-busted MJPEG URL. Bump `token` to force the browser to reconnect. */
export function cameraStreamUrl(printerId: string, token: number): string {
  return `/api/printers/${encodeURIComponent(printerId)}/camera/stream.mjpg?t=${token}`;
}
```

- [ ] **Step 2: Add `setChamberLight` to the commands client**

Append to `web/src/lib/api/printer-commands.ts`:

```typescript
export async function setChamberLight(printerId: string, on: boolean): Promise<void> {
  await fetchJson<unknown>(`/api/printers/${encodeURIComponent(printerId)}/light`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ on, node: 'chamber_light' }),
  });
}
```

- [ ] **Step 3: Create a placeholder Camera route**

```tsx
// web/src/routes/camera.tsx
export default function CameraRoute() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-[28px] font-extrabold tracking-tight text-white">Camera</h1>
      <div className="text-sm text-text-1">Camera UI coming up.</div>
    </div>
  );
}
```

- [ ] **Step 4: Register the route**

Edit `web/src/App.tsx`. Add the import and the route entry:

```tsx
import CameraRoute from '@/routes/camera';
```

In the `children` array, insert after the index entry:

```tsx
{ path: 'camera', element: <CameraRoute /> },
```

The complete `children` should now be (in order):

```tsx
children: [
  { index: true, element: <DashboardRoute /> },
  { path: 'camera', element: <CameraRoute /> },
  { path: 'print', element: <PrintRoute /> },
  { path: 'jobs', element: <JobsRoute /> },
  { path: 'settings', element: <SettingsRoute /> },
],
```

- [ ] **Step 5: Add the tab to the app shell**

Edit `web/src/components/app-shell.tsx`. In the `<nav>` block, insert a new `TabLink` between Dashboard and Print:

```tsx
<TabLink to="/">Dashboard</TabLink>
<TabLink to="/camera">Camera</TabLink>
<TabLink to="/print">Print</TabLink>
<TabLink to="/jobs">Jobs</TabLink>
```

- [ ] **Step 6: Type-check and verify the route renders**

Run:

```bash
cd web && npm run lint
```

Expected: no TypeScript errors.

To verify navigation manually (optional — final UI check happens after Task 9):

```bash
cd web && npm run dev
```

Open `http://localhost:5173/camera` and confirm the "Camera" tab is highlighted and the placeholder text renders.

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/api/camera.ts web/src/lib/api/printer-commands.ts \
        web/src/routes/camera.tsx web/src/App.tsx web/src/components/app-shell.tsx
git commit -m "Add Camera tab and API client to the web UI"
```

---

## Task 8: Frontend — chamber light toggle component

**Files:**
- Create: `web/src/components/camera/chamber-light-toggle.tsx`

- [ ] **Step 1: Implement the toggle**

```tsx
// web/src/components/camera/chamber-light-toggle.tsx
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Lightbulb } from 'lucide-react';
import type { PrinterStatus } from '@/lib/api/types';
import { setChamberLight } from '@/lib/api/printer-commands';
import { cn } from '@/lib/utils';

export function ChamberLightToggle({ printer }: { printer: PrinterStatus }) {
  const supported = printer.camera?.chamber_light?.supported ?? false;
  const reportedOn = printer.camera?.chamber_light?.on ?? null;
  const [optimisticOn, setOptimisticOn] = useState<boolean | null>(null);
  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: (next: boolean) => setChamberLight(printer.id, next),
    onMutate: (next) => setOptimisticOn(next),
    onSettled: () => {
      setOptimisticOn(null);
      qc.invalidateQueries({ queryKey: ['printers'] });
    },
  });

  if (!supported || reportedOn === null) return null;

  const isOn = optimisticOn ?? reportedOn;
  const disabled = mutation.isPending || !printer.online;

  return (
    <button
      type="button"
      onClick={() => mutation.mutate(!isOn)}
      disabled={disabled}
      aria-label="Chamber light"
      aria-pressed={isOn}
      className={cn(
        'flex items-center justify-center gap-3 w-full h-14 rounded-xl border text-sm font-semibold transition-colors duration-fast',
        isOn
          ? 'bg-accent-strong border-accent-strong text-white'
          : 'bg-surface-1 border-border text-text-0 hover:text-white',
        disabled && 'opacity-60 cursor-not-allowed',
      )}
    >
      <Lightbulb className={cn('w-5 h-5', isOn ? 'fill-current' : '')} aria-hidden />
      {isOn ? 'Chamber light on' : 'Chamber light off'}
    </button>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd web && npm run lint`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/camera/chamber-light-toggle.tsx
git commit -m "Add chamber light pill button to the web UI"
```

---

## Task 9: Frontend — camera feed component

**Files:**
- Create: `web/src/components/camera/camera-feed.tsx`

- [ ] **Step 1: Implement the feed tile**

```tsx
// web/src/components/camera/camera-feed.tsx
import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Loader2, Maximize2 } from 'lucide-react';
import type { PrinterStatus } from '@/lib/api/types';
import { cameraStreamUrl, getCameraStatus } from '@/lib/api/camera';
import { cn } from '@/lib/utils';

export function CameraFeed({ printer }: { printer: PrinterStatus }) {
  const camera = printer.camera;
  const transport = camera?.transport ?? null;
  const supported = transport === 'tcp_jpeg';

  if (!camera) return <Placeholder text="Camera not available for this printer." />;
  if (!supported) {
    return (
      <Placeholder
        text={
          transport === 'rtsps'
            ? 'RTSPS cameras (X1 family) aren’t supported in the web UI yet.'
            : 'Camera not available for this printer.'
        }
      />
    );
  }

  return <SupportedFeed printerId={printer.id} online={printer.online} />;
}

function SupportedFeed({ printerId, online }: { printerId: string; online: boolean }) {
  const [retryToken, setRetryToken] = useState(0);
  const wrapperRef = useRef<HTMLDivElement>(null);

  const statusQuery = useQuery({
    queryKey: ['camera-status', printerId],
    queryFn: () => getCameraStatus(printerId),
    refetchInterval: 2_000,
    enabled: online,
  });

  // Reset the cache-buster when the printer changes so the previous printer's
  // MJPEG connection is dropped immediately.
  useEffect(() => {
    setRetryToken(Date.now());
  }, [printerId]);

  const state = online ? statusQuery.data?.state ?? 'connecting' : 'failed';
  const error = statusQuery.data?.error ?? (online ? null : 'Printer offline.');

  const onFullscreen = () => {
    wrapperRef.current?.requestFullscreen?.().catch(() => {
      /* user gesture lost or already fullscreen */
    });
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-sm text-text-1">
        <span className={cn('w-2 h-2 rounded-full', dotClass(state))} aria-hidden />
        <span>Printer</span>
      </div>
      <div
        ref={wrapperRef}
        className="relative w-full aspect-video bg-black rounded-xl overflow-hidden border border-border"
      >
        {online && (
          <img
            src={cameraStreamUrl(printerId, retryToken)}
            alt="Printer camera"
            className="w-full h-full object-contain"
            draggable={false}
          />
        )}

        <button
          type="button"
          onClick={onFullscreen}
          aria-label="Enter fullscreen"
          className="absolute top-2 right-2 p-1.5 rounded-full bg-black/50 text-white/90 hover:bg-black/70"
        >
          <Maximize2 className="w-4 h-4" />
        </button>

        {(state === 'connecting' || state === 'idle') && <ConnectingOverlay />}
        {state === 'failed' && (
          <FailedOverlay
            message={error ?? 'Camera disconnected.'}
            onRetry={() => setRetryToken((t) => t + 1)}
          />
        )}
      </div>
    </div>
  );
}

function ConnectingOverlay() {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/50 text-white">
      <Loader2 className="w-6 h-6 animate-spin" aria-hidden />
      <div className="text-xs">Connecting…</div>
    </div>
  );
}

function FailedOverlay({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/70 text-white px-4 text-center">
      <AlertTriangle className="w-6 h-6 text-warm" aria-hidden />
      <div className="text-xs">{message}</div>
      <button
        type="button"
        onClick={onRetry}
        className="px-3 py-1.5 rounded-full bg-accent-strong text-white text-xs font-semibold hover:bg-accent"
      >
        Retry
      </button>
    </div>
  );
}

function Placeholder({ text }: { text: string }) {
  return (
    <div className="w-full aspect-video bg-surface-1 border border-border rounded-xl flex items-center justify-center text-sm text-text-1 px-6 text-center">
      {text}
    </div>
  );
}

function dotClass(state: string): string {
  switch (state) {
    case 'streaming': return 'bg-success';
    case 'connecting':
    case 'idle': return 'bg-warm';
    case 'failed': return 'bg-danger';
    default: return 'bg-text-2';
  }
}
```

- [ ] **Step 2: Type-check**

Run: `cd web && npm run lint`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/camera/camera-feed.tsx
git commit -m "Add web UI camera feed tile with status and retry"
```

---

## Task 10: Frontend — wire the Camera route

Replace the placeholder in `web/src/routes/camera.tsx` with the real layout.

**Files:**
- Modify: `web/src/routes/camera.tsx`

- [ ] **Step 1: Implement the route**

```tsx
// web/src/routes/camera.tsx
import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Skeleton } from '@/components/ui/skeleton';
import { Card } from '@/components/ui/card';
import { PrinterPicker } from '@/components/printer-picker';
import { ChamberLightToggle } from '@/components/camera/chamber-light-toggle';
import { CameraFeed } from '@/components/camera/camera-feed';
import { listPrinters } from '@/lib/api/printers';
import { usePrinterContext } from '@/lib/printer-context';

export default function CameraRoute() {
  const { activePrinterId, setActivePrinterId } = usePrinterContext();

  const printersQuery = useQuery({
    queryKey: ['printers'],
    queryFn: () => listPrinters(),
    refetchInterval: 4_000,
  });

  const printers = printersQuery.data?.printers ?? [];

  useEffect(() => {
    if (printers.length === 0) return;
    const stillExists = activePrinterId && printers.some((p) => p.id === activePrinterId);
    if (!stillExists) setActivePrinterId(printers[0].id);
  }, [printers, activePrinterId, setActivePrinterId]);

  if (printersQuery.isLoading) return <CameraLoading />;
  if (printersQuery.isError) {
    return <CameraError detail={(printersQuery.error as Error).message} />;
  }
  if (printers.length === 0) return <CameraEmpty />;

  const active = printers.find((p) => p.id === activePrinterId) ?? printers[0];

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-center justify-between gap-3">
        <h1 className="text-[28px] font-extrabold tracking-tight text-white">Camera</h1>
      </header>

      <PrinterPicker printers={printers} activeId={active.id} onChange={setActivePrinterId} />

      <ChamberLightToggle printer={active} />

      <CameraFeed printer={active} />
    </div>
  );
}

function CameraLoading() {
  return (
    <div className="flex flex-col gap-6">
      <Skeleton className="h-9 w-32" />
      <Skeleton className="h-10 w-full max-w-md rounded-full" />
      <Skeleton className="h-14 w-full rounded-xl" />
      <Skeleton className="aspect-video w-full rounded-xl" />
    </div>
  );
}

function CameraError({ detail }: { detail: string }) {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-[28px] font-extrabold tracking-tight text-white">Camera</h1>
      <Card className="p-4 bg-card border-danger/40 text-sm text-text-0">
        Failed to load printers: <span className="font-mono">{detail}</span>
      </Card>
    </div>
  );
}

function CameraEmpty() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-[28px] font-extrabold tracking-tight text-white">Camera</h1>
      <Card className="p-6 bg-card border-border flex flex-col gap-3 items-start">
        <div className="text-base font-semibold text-white">No printers configured</div>
        <div className="text-sm text-text-1">Add a printer to see its camera feed here.</div>
        <a
          href="/settings"
          className="inline-flex items-center px-3.5 py-2 rounded-full bg-accent-strong text-white text-sm font-semibold hover:bg-accent transition-colors"
        >
          Open Settings →
        </a>
      </Card>
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd web && npm run lint`
Expected: no errors.

- [ ] **Step 3: Build to confirm everything compiles into the FastAPI static bundle**

Run: `cd web && npm run build`
Expected: Vite emits `app/static/dist/index.html` and assets without errors.

- [ ] **Step 4: Manual verification**

The web UI can't be exercised end-to-end without a real A1/P1 printer, but we can at minimum verify:

1. Start the gateway: `.venv/bin/python -m app`
2. Open `http://localhost:4844/camera` (or the dev server at `http://localhost:5173/camera`).
3. Confirm:
   - The `Camera` tab appears between Dashboard and Print and is highlighted on `/camera`.
   - The chamber light pill renders for an A1/P1 printer once the gateway has received its first `lights_report` (button reflects on/off).
   - The feed tile renders. With no printer in reach the overlay shows "Connecting…" then "Camera disconnected" within ~2s of the next status poll, with a working **Retry** button.
   - Clicking the corner icon enters native fullscreen.
   - Switching to another printer in the picker tears down the previous MJPEG connection and starts a new one.
   - On an X1-family printer the placeholder reads "RTSPS cameras (X1 family) aren't supported in the web UI yet."

If any of those checks fail, add follow-up commits to fix.

- [ ] **Step 5: Commit**

```bash
git add web/src/routes/camera.tsx app/static/dist
git commit -m "Wire camera route with picker, light, and live feed"
```

---

## Self-Review

**Spec coverage:**

- A1/P1 TCP-JPEG proxy → Tasks 1–4 (auth, parser, fan-out, upstream lifecycle).
- One upstream connection shared with N viewers → Task 4 + Task 6 (subscribe iterator).
- 5s drain on idle → Task 4 (drain test included).
- `PrinterService` integration with sync_printers → Task 5.
- `GET /camera/stream.mjpg` and `GET /camera/status` → Task 6 (with X1/unknown 404 + unsupported coverage).
- Camera tab between Dashboard and Print → Task 7.
- Reuse `POST /api/printers/{id}/light` for chamber light → Task 7 (`setChamberLight`) + Task 8 (UI).
- Picker + chamber light toggle + always-on feed tile + retry + fullscreen → Tasks 8–10.
- "Not available" placeholder for X1 / no-camera printers → Task 9.
- Status polling every 2s → Task 9.
- Cache-busted retry → Task 9.

**Placeholder scan:** No "TBD" / "TODO" / "implement later" left in the plan. Every code step has runnable code.

**Type consistency:**
- Backend `CameraState` literal stays `"idle" | "connecting" | "streaming" | "failed"`. The HTTP layer also returns `"unsupported"` for non-tcp_jpeg printers; this lives in `CameraStatusResponse.state: str` (broader type intentionally). Frontend `CameraState` mirrors all five.
- `getCameraStatus` and `cameraStreamUrl` names match between Tasks 7 and 9.
- `setChamberLight` signature matches between Tasks 7 and 8.
- `CameraProxy` constructor signature stable from Task 3 onward; Task 4 only adds keyword arguments with defaults.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-28-webui-camera-tab.md`.

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
