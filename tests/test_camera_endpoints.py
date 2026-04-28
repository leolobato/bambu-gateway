"""Integration tests for /api/printers/{id}/camera/stream.mjpg and /status."""

from __future__ import annotations

import asyncio
import socket
import struct
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from app import main as app_module
from app.config import PrinterConfig
from app.printer_service import PrinterService


def _find_free_port() -> int:
    """Return an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(coro):
    """Run an async coroutine in a fresh event loop (Python 3.10+ compatible)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeCameraServer:
    """Minimal threading-based TCP server that speaks the Bambu TCP-JPEG protocol.

    Using a plain thread avoids event-loop coupling issues with TestClient's
    internal anyio runner (which creates its own event loop per request).
    """

    def __init__(self, frames: list[bytes]) -> None:
        self._frames = frames
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self._sock.settimeout(5)
        self.port: int = self._sock.getsockname()[1]
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop_event.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            # Consume the 80-byte auth packet
            received = b""
            while len(received) < 80:
                chunk = conn.recv(80 - len(received))
                if not chunk:
                    return
                received += chunk
            # Send each frame with a 16-byte header, then close.
            for jpeg in self._frames:
                n = len(jpeg)
                header = struct.pack("<I", n) + b"\x00" * 12
                conn.sendall(header + jpeg)
            # Close after sending all frames.
        except OSError:
            pass
        finally:
            conn.close()

    def close(self) -> None:
        self._stop_event.set()
        self._sock.close()
        self._thread.join(timeout=3)


@pytest.fixture
def patched_service(monkeypatch):
    """Install a real PrinterService with one A1 printer pointing at a fake server."""
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
    _run(svc.stop_async())


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
        _run(svc.stop_async())


def test_cameraStream_mjpegEndpoint_emitsTwoParts(monkeypatch):
    """Test MJPEG streaming via a real uvicorn server so iter_bytes() truly streams."""

    jpeg_a = b"\xff\xd8AAA\xff\xd9"
    jpeg_b = b"\xff\xd8BBB\xff\xd9"

    cfg = PrinterConfig(
        serial="01PXXX",
        ip="127.0.0.1",
        access_code="abcd",
        name="A1",
        machine_model="GM021",
    )
    svc = PrinterService([cfg])
    monkeypatch.setattr(app_module, "printer_service", svc)

    fake_cam = _FakeCameraServer([jpeg_a, jpeg_b])

    # Point the proxy at the fake server before any request is made.
    proxy = svc.get_camera_proxy("01PXXX")
    assert proxy is not None
    proxy._port = fake_cam.port
    proxy._use_tls = False
    # Zero retry delay so reconnects are instant after EOF.
    proxy._retry_delay = 0.0

    # Start uvicorn in a background thread on a random port.
    server_port = _find_free_port()
    config = uvicorn.Config(
        app_module.app,
        host="127.0.0.1",
        port=server_port,
        lifespan="off",  # avoid clobbering the monkeypatched printer_service
        log_level="error",
    )
    uv_server = uvicorn.Server(config)
    uv_thread = threading.Thread(target=uv_server.run, daemon=True)
    uv_thread.start()

    # Wait for uvicorn to be ready.
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{server_port}/api/health", timeout=0.5)
            break
        except Exception:
            time.sleep(0.1)
    else:
        pytest.fail("uvicorn did not become ready in time")

    try:
        with httpx.stream("GET", f"http://127.0.0.1:{server_port}/api/printers/01PXXX/camera/stream.mjpg", timeout=10) as res:
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
        fake_cam.close()
        uv_server.should_exit = True
        uv_thread.join(timeout=5)
        _run(svc.stop_async())


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
        _run(svc.stop_async())
