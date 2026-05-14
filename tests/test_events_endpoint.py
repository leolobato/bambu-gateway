"""Tests for GET /api/printers/{id}/events SSE stream."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import anyio
import httpx
import pytest

from app import main as main_mod


def _parse_sse(chunk: str) -> list[dict]:
    """Parse SSE wire format into a list of {event, data} dicts."""
    events = []
    for block in chunk.split("\n\n"):
        if not block.strip():
            continue
        event_type = None
        data = None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_type = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = json.loads(line.split(":", 1)[1].strip())
        if event_type:
            events.append({"event": event_type, "data": data})
    return events


class _AsyncGenByteStream(httpx.AsyncByteStream):
    """Wraps an async generator as an httpx.AsyncByteStream."""

    def __init__(
        self,
        gen: AsyncIterator[bytes],
        task: asyncio.Task,
        disconnect_event: asyncio.Event,
    ) -> None:
        self._gen = gen
        self._task = task
        self._disconnect = disconnect_event

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._gen:
                yield chunk
        finally:
            # Signal disconnect so that Starlette's listen_for_disconnect
            # task unblocks and the task group can exit cleanly.
            self._disconnect.set()
            # Also cancel the task in case the generator is still running.
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass


class _StreamingASGITransport(httpx.AsyncBaseTransport):
    """Async ASGI transport with true streaming support.

    Unlike ``httpx.ASGITransport``, this transport runs the ASGI app in a
    background asyncio task and returns a ``Response`` whose body is a
    streaming async generator. The caller can break out of ``aiter_text()``
    at any point; the ``async with ac.stream(...)`` block cancels the
    background task on exit.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "headers": [(k.lower(), v) for k, v in request.headers.raw],
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "server": (request.url.host, request.url.port),
            "client": ("127.0.0.1", 123),
            "root_path": "",
        }

        # Unbounded in-memory pipe between the ASGI app and the response body.
        send_stream, recv_stream = anyio.create_memory_object_stream(max_buffer_size=256)

        status_code: list[int] = []
        response_headers: list = []
        headers_ready = asyncio.Event()
        # Signals that the "client" has disconnected (stream closed by caller).
        disconnect_event = asyncio.Event()
        request_body_sent = False

        async def receive() -> dict:
            nonlocal request_body_sent
            if not request_body_sent:
                request_body_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            # After the request body is consumed, block until disconnect.
            await disconnect_event.wait()
            return {"type": "http.disconnect"}

        async def send_msg(message: dict) -> None:
            if message["type"] == "http.response.start":
                status_code.append(message["status"])
                response_headers.extend(message.get("headers", []))
                headers_ready.set()
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                more_body = message.get("more_body", False)
                if body:
                    await send_stream.send(body)
                if not more_body:
                    await send_stream.aclose()

        async def run_app() -> None:
            try:
                await self.app(scope, receive, send_msg)
            except (asyncio.CancelledError, Exception):
                pass
            finally:
                try:
                    await send_stream.aclose()
                except Exception:
                    pass

        app_task = asyncio.get_running_loop().create_task(run_app())

        # Wait for the ASGI app to send response headers before returning.
        await headers_ready.wait()

        async def body_gen() -> AsyncIterator[bytes]:
            async with recv_stream:
                async for chunk in recv_stream:
                    yield chunk

        return httpx.Response(
            status_code=status_code[0],
            headers=response_headers,
            stream=_AsyncGenByteStream(body_gen(), app_task, disconnect_event),
        )


@pytest.mark.asyncio
async def test_events_emits_snapshot_then_reports(monkeypatch):
    """First frame is `snapshot` with cached payload; subsequent are `report`.

    Uses the full HTTP stack (custom streaming ASGI transport + ac.stream) to
    verify SSE response headers, HTTP status code, and event content.
    """
    from app.print_event_broker import PrintEventBroker

    broker = PrintEventBroker()

    class _StubService:
        def get_client(self, pid):
            class _Client:
                latest_print_payload = {"gcode_state": "RUNNING", "layer_num": 10}
            return _Client() if pid == "S1" else None
        def get_event_broker(self, pid):
            return broker if pid == "S1" else None
        def default_printer_id(self):
            return "S1"

    monkeypatch.setattr(main_mod, "printer_service", _StubService())

    transport = _StreamingASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream("GET", "/api/printers/S1/events") as resp:
            # Header / status assertions — these are part of the spec.
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            assert resp.headers.get("cache-control") == "no-cache"
            assert resp.headers.get("x-accel-buffering") == "no"

            buf = ""

            async def _publisher():
                # Give the snapshot frame a chance to emit first.
                await asyncio.sleep(0.05)
                await broker.publish({"layer_num": 11})
                await broker.publish({"layer_num": 12})

            async def _consumer():
                nonlocal buf
                async for chunk in resp.aiter_text():
                    buf += chunk
                    # _parse_sse only counts complete frames (separated by \n\n).
                    if len(_parse_sse(buf)) >= 3:
                        return

            await asyncio.wait_for(
                asyncio.gather(_consumer(), _publisher()),
                timeout=2.0,
            )

    events = _parse_sse(buf)[:3]
    assert events[0]["event"] == "snapshot"
    assert events[0]["data"] == {"gcode_state": "RUNNING", "layer_num": 10}
    assert events[1]["event"] == "report"
    assert events[1]["data"] == {"layer_num": 11}
    assert events[2]["event"] == "report"
    assert events[2]["data"] == {"layer_num": 12}


@pytest.mark.asyncio
async def test_events_unknown_printer_returns_404(monkeypatch):
    class _StubService:
        def get_client(self, pid): return None
        def get_event_broker(self, pid): return None
        def default_printer_id(self): return None

    monkeypatch.setattr(main_mod, "printer_service", _StubService())

    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/printers/nope/events")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_events_emits_empty_snapshot_when_no_cached_payload(monkeypatch):
    """When `latest_print_payload` is None the snapshot frame sends `{}`."""
    from app.print_event_broker import PrintEventBroker

    broker = PrintEventBroker()

    class _StubService:
        def get_client(self, pid):
            class _Client:
                latest_print_payload = None
            return _Client() if pid == "S1" else None
        def get_event_broker(self, pid):
            return broker if pid == "S1" else None
        def default_printer_id(self):
            return "S1"

    monkeypatch.setattr(main_mod, "printer_service", _StubService())

    transport = _StreamingASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream("GET", "/api/printers/S1/events") as resp:
            assert resp.status_code == 200
            buf = ""

            async def _read_first_frame():
                nonlocal buf
                async for chunk in resp.aiter_text():
                    buf += chunk
                    if "\n\n" in buf:
                        return

            await asyncio.wait_for(_read_first_frame(), timeout=2.0)

    events = _parse_sse(buf)
    assert events[0]["event"] == "snapshot"
    assert events[0]["data"] == {}
