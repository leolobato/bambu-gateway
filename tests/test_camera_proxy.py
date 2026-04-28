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
    b_ = b"\xff\xd8" + b"B" * 20 + b"\xff\xd9"
    out = parser.feed(_frame(a) + _frame(b_))
    assert out == [a, b_]


def test_frameParser_partialThenComplete_emitsOnceComplete():
    parser = FrameParser()
    jpeg = b"\xff\xd8" + b"C" * 30 + b"\xff\xd9"
    payload = _frame(jpeg)
    # Feed everything except the last byte first.
    assert parser.feed(payload[:-1]) == []
    # Feed the last byte; now it should emit.
    assert parser.feed(payload[-1:]) == [jpeg]


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
    await proxy.stop()


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
    await proxy.stop()


@pytest.mark.asyncio
async def test_cameraProxy_slowConsumer_dropsOldFramesNotNewest():
    proxy = CameraProxy(ip="127.0.0.1", access_code="x", queue_maxsize=3)
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
    await proxy.stop()


class _FakeCameraServer:
    """Fake Bambu camera TCP server for tests.

    Reads (and ignores) an 80-byte auth packet, then writes each frame in
    `frames` framed with the 16-byte length header.  Keeps the connection
    open afterwards so the proxy stays in `streaming`.

    Call :meth:`close` in test teardown — it cancels all handler tasks so
    ``server.wait_closed()`` does not hang.
    """

    def __init__(self, server: asyncio.AbstractServer, port: int, handler_tasks: list) -> None:
        self._server = server
        self.port = port
        self._handler_tasks = handler_tasks

    async def close(self) -> None:
        self._server.close()
        for t in self._handler_tasks:
            if not t.done():
                t.cancel()
        for t in self._handler_tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await self._server.wait_closed()


async def _start_fake_camera_server(
    frames: list[bytes],
    *,
    expect_auth: bool = True,
) -> _FakeCameraServer:
    """Start a localhost TCP server that mimics the Bambu camera handshake."""
    handler_tasks: list[asyncio.Task] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            if expect_auth:
                await reader.readexactly(80)
            for jpeg in frames:
                n = len(jpeg)
                header = bytes([n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF, (n >> 24) & 0xFF]) + bytes(12)
                writer.write(header + jpeg)
                await writer.drain()
            # Keep the connection open so the proxy stays in `streaming` until torn down.
            while True:
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def handle_and_track(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        task = asyncio.current_task()
        if task is not None:
            handler_tasks.append(task)
        await handle(reader, writer)

    server = await asyncio.start_server(handle_and_track, host="127.0.0.1", port=0)
    port = server.sockets[0].getsockname()[1]
    return _FakeCameraServer(server, port, handler_tasks)


@pytest.mark.asyncio
async def test_cameraProxy_subscribe_startsUpstream_yieldsFrames():
    jpeg_a = b"\xff\xd8frame-A\xff\xd9"
    jpeg_b = b"\xff\xd8frame-B\xff\xd9"
    fake = await _start_fake_camera_server([jpeg_a, jpeg_b])

    proxy = CameraProxy(
        ip="127.0.0.1",
        access_code="abcd",
        port=fake.port,
        use_tls=False,  # plain TCP for the fake server
        retry_delay=0.05,
    )
    try:
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
        await proxy.stop()
        await fake.close()


@pytest.mark.asyncio
async def test_cameraProxy_lastSubscriberLeaves_drainsAndStopsUpstream():
    jpeg = b"\xff\xd8x\xff\xd9"
    fake = await _start_fake_camera_server([jpeg])

    proxy = CameraProxy(
        ip="127.0.0.1",
        access_code="abcd",
        port=fake.port,
        use_tls=False,
        drain_grace=0.1,  # short grace for fast tests
        retry_delay=0.05,
    )
    try:
        async def quick_consumer():
            async for _ in proxy.subscribe():
                return

        await asyncio.wait_for(quick_consumer(), timeout=2.0)
        # Subscriber set is now empty; drain should fire after grace.
        await asyncio.sleep(0.3)
        assert proxy.state == "idle"
        assert proxy._upstream_task is None or proxy._upstream_task.done()
    finally:
        await proxy.stop()
        await fake.close()


@pytest.mark.asyncio
async def test_cameraProxy_twoSubscribers_bothReceiveSameFrames():
    jpeg_a = b"\xff\xd8frame-A\xff\xd9"
    jpeg_b = b"\xff\xd8frame-B\xff\xd9"
    fake = await _start_fake_camera_server([jpeg_a, jpeg_b])

    try:
        proxy = CameraProxy(
            ip="127.0.0.1",
            access_code="abcd",
            port=fake.port,
            use_tls=False,
            retry_delay=0.05,
        )

        received_a: list[bytes] = []
        received_b: list[bytes] = []

        async def consumer(target: list[bytes]) -> None:
            async for frame in proxy.subscribe():
                target.append(frame)
                if len(target) == 2:
                    return

        await asyncio.gather(
            asyncio.wait_for(consumer(received_a), timeout=2.0),
            asyncio.wait_for(consumer(received_b), timeout=2.0),
        )

        assert received_a == [jpeg_a, jpeg_b]
        assert received_b == [jpeg_a, jpeg_b]
    finally:
        await proxy.stop()
        await fake.close()


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
