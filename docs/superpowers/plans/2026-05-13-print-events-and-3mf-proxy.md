# Print Events SSE + Current-Job 3MF Proxy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new endpoints to `bambu-gateway` so external consumers (specifically `bambu-spool-helper`) can subscribe to live printer `report` payloads and download the active print's 3MF, without opening their own MQTT/FTPS connection to the printer.

**Architecture:** A small `PrintEventBroker` per `BambuMQTTClient` fans every incoming `print` payload out to subscribers via per-consumer `asyncio.Queue`s; an SSE endpoint drains one queue per subscriber. The 3MF proxy reads the latest cached `project_file` payload, resolves its `url`, and streams either an HTTP passthrough or an FTPS download back to the caller.

**Tech Stack:** Python 3.13, FastAPI, `paho-mqtt`, `httpx` (outbound async HTTP), `ftplib.FTP_TLS` (printer FTPS), `pytest` + `pytest-asyncio`.

**Spec reference:** [`../../../bambu-spool-helper/docs/superpowers/specs/2026-05-13-print-tracking-design.md`](../../../../bambu-spool-helper/docs/superpowers/specs/2026-05-13-print-tracking-design.md) — sections 2 and 3 ("Gateway side").

---

## File Structure

**Create:**
- `app/print_event_broker.py` — new pub/sub primitive.
- `tests/test_print_event_broker.py` — unit tests for the broker.
- `tests/test_events_endpoint.py` — endpoint tests for `/api/printers/{id}/events`.
- `tests/test_current_job_file_endpoint.py` — endpoint tests for `/api/printers/{id}/current-job/file`.

**Modify:**
- `app/mqtt_client.py` — cache latest raw `print` payload; publish to broker.
- `app/printer_service.py` — own one `PrintEventBroker` per printer; expose accessor.
- `app/ftp_client.py` — add `download_file` capability.
- `app/main.py` — register the two new routes.

Tests live alongside existing ones in `tests/`. The project uses `pytest` + `pytest-asyncio` (per `requirements.txt`).

---

## Task 1: PrintEventBroker primitive

**Files:**
- Create: `app/print_event_broker.py`
- Create: `tests/test_print_event_broker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_print_event_broker.py`:

```python
"""Tests for PrintEventBroker — per-consumer queue fan-out."""

from __future__ import annotations

import asyncio

import pytest

from app.print_event_broker import PrintEventBroker


@pytest.mark.asyncio
async def test_subscriber_receives_published_event():
    broker = PrintEventBroker()
    async with broker.subscribe() as queue:
        await broker.publish({"layer_num": 1})
        event = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert event == {"layer_num": 1}


@pytest.mark.asyncio
async def test_multiple_subscribers_each_receive_event():
    broker = PrintEventBroker()
    async with broker.subscribe() as q1, broker.subscribe() as q2:
        await broker.publish({"gcode_state": "RUNNING"})
        e1 = await asyncio.wait_for(q1.get(), timeout=0.5)
        e2 = await asyncio.wait_for(q2.get(), timeout=0.5)
    assert e1 == {"gcode_state": "RUNNING"}
    assert e2 == {"gcode_state": "RUNNING"}


@pytest.mark.asyncio
async def test_unsubscribe_drops_queue():
    broker = PrintEventBroker()
    async with broker.subscribe():
        pass
    # publishing after unsubscribe must not raise
    await broker.publish({"x": 1})
    assert broker.subscriber_count == 0


@pytest.mark.asyncio
async def test_full_queue_drops_event_not_blocks():
    broker = PrintEventBroker(max_queue_size=1)
    async with broker.subscribe() as queue:
        await broker.publish({"i": 1})
        await broker.publish({"i": 2})  # should drop, not block
        first = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert first == {"i": 1}
    assert queue.empty()


@pytest.mark.asyncio
async def test_publish_with_no_subscribers_is_noop():
    broker = PrintEventBroker()
    await broker.publish({"x": 1})  # must not raise
    assert broker.subscriber_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_print_event_broker.py -v`
Expected: All tests fail with `ModuleNotFoundError: No module named 'app.print_event_broker'`.

- [ ] **Step 3: Implement the broker**

Create `app/print_event_broker.py`:

```python
"""Per-printer pub/sub for raw MQTT `print` payloads.

One instance per `BambuMQTTClient`. Each subscriber gets its own bounded
`asyncio.Queue`. The broker is fire-and-forget: if a slow subscriber's
queue is full, the new event is dropped for that subscriber rather than
blocking the publisher (which runs on the MQTT thread via the event loop).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

logger = logging.getLogger(__name__)


class PrintEventBroker:
    def __init__(self, max_queue_size: int = 256) -> None:
        self._max_queue_size = max_queue_size
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._lock = asyncio.Lock()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict]]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=self._max_queue_size)
        async with self._lock:
            self._subscribers.add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    async def publish(self, event: dict) -> None:
        async with self._lock:
            targets = list(self._subscribers)
        for queue in targets:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "PrintEventBroker queue full; dropping event for one subscriber"
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_print_event_broker.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/print_event_broker.py tests/test_print_event_broker.py
git commit -m "feat: add PrintEventBroker for per-printer pub/sub"
```

---

## Task 2: Cache latest raw `print` payload on BambuMQTTClient

**Files:**
- Modify: `app/mqtt_client.py:464-480` (the `_on_message` handler)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mqtt_client.py` (create the file if it doesn't exist by copying the header style from `tests/test_print_event_broker.py`):

```python
"""Tests for BambuMQTTClient — payload caching & broker publish."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.mqtt_client import BambuMQTTClient


def _make_client() -> BambuMQTTClient:
    return BambuMQTTClient(
        printer_id="P01",
        host="127.0.0.1",
        access_code="x",
        serial="S01",
        name="test",
    )


def test_on_message_caches_latest_print_payload():
    client = _make_client()
    msg = MagicMock()
    msg.payload = json.dumps({"print": {"layer_num": 42, "gcode_state": "RUNNING"}}).encode()
    msg.topic = "device/S01/report"
    client._on_message(None, None, msg)
    assert client.latest_print_payload == {"layer_num": 42, "gcode_state": "RUNNING"}


def test_on_message_ignores_non_print_payload():
    client = _make_client()
    msg = MagicMock()
    msg.payload = json.dumps({"info": {"command": "get_version"}}).encode()
    msg.topic = "device/S01/report"
    client._on_message(None, None, msg)
    assert client.latest_print_payload is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mqtt_client.py -v`
Expected: FAIL with `AttributeError: 'BambuMQTTClient' object has no attribute 'latest_print_payload'`.

- [ ] **Step 3: Add the cache to `BambuMQTTClient.__init__`**

In `app/mqtt_client.py`, locate the `__init__` method of `BambuMQTTClient`. Add the following line near the existing instance-attribute initializations (next to `self._status`):

```python
        self._latest_print_payload: dict | None = None
```

Then add a public read-only property after `__init__`:

```python
    @property
    def latest_print_payload(self) -> dict | None:
        """Most recently received `print` payload (raw), or None if none seen yet."""
        with self._lock:
            return self._latest_print_payload
```

In `_on_message` (around line 476–480), update the body to:

```python
        print_info = payload.get("print", {})
        if not print_info:
            return

        with self._lock:
            self._latest_print_payload = print_info

        self._update_status(print_info)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_mqtt_client.py -v`
Expected: Both tests PASS.

- [ ] **Step 5: Run the full test suite to confirm no regression**

Run: `pytest -v`
Expected: All existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add app/mqtt_client.py tests/test_mqtt_client.py
git commit -m "feat(mqtt): cache latest raw print payload"
```

---

## Task 3: Publish raw print payload to a broker from BambuMQTTClient

**Files:**
- Modify: `app/mqtt_client.py` (constructor + `_on_message`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mqtt_client.py`:

```python
import asyncio

import pytest


@pytest.mark.asyncio
async def test_on_message_publishes_to_broker():
    from app.print_event_broker import PrintEventBroker

    loop = asyncio.get_running_loop()
    broker = PrintEventBroker()
    client = _make_client()
    client.attach_event_broker(broker, loop)

    async with broker.subscribe() as queue:
        msg = MagicMock()
        msg.payload = json.dumps({"print": {"layer_num": 7}}).encode()
        msg.topic = "device/S01/report"
        client._on_message(None, None, msg)
        event = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert event == {"layer_num": 7}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mqtt_client.py::test_on_message_publishes_to_broker -v`
Expected: FAIL with `AttributeError: 'BambuMQTTClient' object has no attribute 'attach_event_broker'`.

- [ ] **Step 3: Add broker attachment and publish call**

In `app/mqtt_client.py`:

In `__init__`, after `self._latest_print_payload = None`, add:

```python
        self._event_broker = None
        self._event_loop = None
```

Add this method to `BambuMQTTClient`:

```python
    def attach_event_broker(self, broker, loop) -> None:
        """Attach an async PrintEventBroker. MQTT thread schedules publishes
        on the supplied event loop."""
        self._event_broker = broker
        self._event_loop = loop
```

In `_on_message`, after the cache update (`self._latest_print_payload = print_info`) and before `self._update_status(print_info)`, add:

```python
        if self._event_broker is not None and self._event_loop is not None:
            asyncio.run_coroutine_threadsafe(
                self._event_broker.publish(dict(print_info)),
                self._event_loop,
            )
```

(`dict(print_info)` shallow-copies so subscribers don't observe in-place mutations from later messages.)

Make sure `import asyncio` is at the top of `app/mqtt_client.py` (it likely already is — confirm before adding).

- [ ] **Step 4: Run test**

Run: `pytest tests/test_mqtt_client.py::test_on_message_publishes_to_broker -v`
Expected: PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add app/mqtt_client.py tests/test_mqtt_client.py
git commit -m "feat(mqtt): publish raw print payload to event broker"
```

---

## Task 4: Wire one PrintEventBroker per printer through PrinterService

**Files:**
- Modify: `app/printer_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_printer_service_brokers.py`:

```python
"""Tests for PrinterService.get_event_broker."""

from __future__ import annotations

import asyncio

import pytest

from app.printer_service import PrinterService


@pytest.mark.asyncio
async def test_each_printer_has_its_own_broker():
    svc = PrinterService(printers=[
        {"serial": "S1", "ip": "10.0.0.1", "access_code": "x", "name": "A"},
        {"serial": "S2", "ip": "10.0.0.2", "access_code": "x", "name": "B"},
    ])
    try:
        b1 = svc.get_event_broker("S1")
        b2 = svc.get_event_broker("S2")
        assert b1 is not None
        assert b2 is not None
        assert b1 is not b2
    finally:
        await svc.shutdown_async()


@pytest.mark.asyncio
async def test_unknown_printer_returns_none_broker():
    svc = PrinterService(printers=[])
    assert svc.get_event_broker("nope") is None
    await svc.shutdown_async()
```

(If `PrinterService` doesn't accept this constructor shape, adapt the test to whatever shape the existing constructor uses — read `app/printer_service.py` first. The test's *intent* is what matters: each registered printer gets its own broker; unknown ids return `None`.)

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_printer_service_brokers.py -v`
Expected: FAIL with `AttributeError: 'PrinterService' object has no attribute 'get_event_broker'`.

- [ ] **Step 3: Wire brokers into PrinterService**

Read `app/printer_service.py`. Find the place where `BambuMQTTClient` instances are created and stored (most likely in `__init__` or a `start()` method, kept in `self._clients`).

For each client created, instantiate a `PrintEventBroker` and call `client.attach_event_broker(broker, loop)`. Store brokers in a parallel `self._brokers: dict[str, PrintEventBroker]`.

Add at top of file:

```python
from app.print_event_broker import PrintEventBroker
```

In the client-creation loop (pseudocode — adapt to actual code):

```python
        self._brokers: dict[str, PrintEventBroker] = {}
        for cfg in printers:
            broker = PrintEventBroker()
            client = BambuMQTTClient(...)  # existing call
            client.attach_event_broker(broker, asyncio.get_event_loop())
            self._clients[cfg["serial"]] = client
            self._brokers[cfg["serial"]] = broker
```

Add the accessor:

```python
    def get_event_broker(self, printer_id: str) -> PrintEventBroker | None:
        return self._brokers.get(printer_id)
```

If the existing service is constructed before an event loop is running, `asyncio.get_event_loop()` will fail under Python 3.12+. In that case, defer `attach_event_broker` to a `start(loop)` method called from FastAPI startup. Inspect existing code patterns and pick the consistent approach.

- [ ] **Step 4: Run the test**

Run: `pytest tests/test_printer_service_brokers.py -v`
Expected: PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add app/printer_service.py tests/test_printer_service_brokers.py
git commit -m "feat(service): per-printer PrintEventBroker"
```

---

## Task 5: SSE endpoint `GET /api/printers/{id}/events`

**Files:**
- Modify: `app/main.py` (add new route after the existing printer routes near line 370)
- Create: `tests/test_events_endpoint.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_events_endpoint.py`:

```python
"""Tests for GET /api/printers/{id}/events SSE stream."""

from __future__ import annotations

import asyncio
import json

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


@pytest.mark.asyncio
async def test_events_emits_snapshot_then_reports(monkeypatch):
    """First frame is `snapshot` with cached payload; subsequent are `report`."""
    # Set up: replace printer_service with a stub exposing a broker + cached print
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

    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream("GET", "/api/printers/S1/events") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")

            buf = ""
            async def _collect_until_n(n):
                nonlocal buf
                async for chunk in resp.aiter_text():
                    buf += chunk
                    if len(_parse_sse(buf)) >= n:
                        return

            # Publish two report events from another task
            async def _publisher():
                await asyncio.sleep(0.05)
                await broker.publish({"layer_num": 11})
                await broker.publish({"layer_num": 12})

            await asyncio.gather(_collect_until_n(3), _publisher())
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
    from app.print_event_broker import PrintEventBroker
    broker = PrintEventBroker()

    class _StubService:
        def get_client(self, pid):
            class _Client:
                latest_print_payload = None
            return _Client() if pid == "S1" else None
        def get_event_broker(self, pid):
            return broker if pid == "S1" else None
        def default_printer_id(self): return "S1"

    monkeypatch.setattr(main_mod, "printer_service", _StubService())

    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream("GET", "/api/printers/S1/events") as resp:
            buf = ""
            async for chunk in resp.aiter_text():
                buf += chunk
                if "\n\n" in buf:
                    break
    events = _parse_sse(buf)
    assert events[0]["event"] == "snapshot"
    assert events[0]["data"] == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_events_endpoint.py -v`
Expected: All three FAIL with 404 (route doesn't exist yet).

- [ ] **Step 3: Implement the endpoint**

In `app/main.py`, locate the `_sse_event` helper (line 1547). Just below it, add a constant:

```python
SSE_KEEPALIVE_INTERVAL_S = 15.0
```

Then after the existing `@app.get("/api/printers/{printer_id}")` route (around line 370), add:

```python
@app.get("/api/printers/{printer_id}/events")
async def printer_events(printer_id: str):
    """SSE stream of raw printer `print` payloads.

    First frame is an `event: snapshot` with the gateway's currently-cached
    `print` payload (or `{}` if none). Subsequent `event: report` frames
    fire for every MQTT message the printer sends. `event: keepalive` fires
    every 15s.
    """
    pid = _resolve_printer_id(printer_id)
    broker = printer_service.get_event_broker(pid)
    client = printer_service.get_client(pid)
    if broker is None or client is None:
        raise HTTPException(status_code=404, detail="Printer not found")

    async def _gen():
        snapshot = client.latest_print_payload or {}
        yield _sse_event("snapshot", snapshot)

        async with broker.subscribe() as queue:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=SSE_KEEPALIVE_INTERVAL_S,
                    )
                    yield _sse_event("report", event)
                except asyncio.TimeoutError:
                    yield _sse_event("keepalive", {})

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )
```

Confirm `asyncio` and `StreamingResponse` are imported at the top of `app/main.py` (the existing `/api/print-stream` route uses both — they should already be there).

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_events_endpoint.py -v`
Expected: All three PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_events_endpoint.py
git commit -m "feat(api): GET /api/printers/{id}/events SSE stream"
```

---

## Task 6: Add FTPS download to ImplicitFTPS client

**Files:**
- Modify: `app/ftp_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ftp_download.py`:

```python
"""Tests for ftp_client.download_file."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch

from app.ftp_client import download_file


def test_download_file_invokes_retrbinary_and_returns_bytes():
    fake_ftps = MagicMock()
    sample = b"PK\x03\x04" + b"\x00" * 32  # zip-like

    def _retrbinary(cmd, callback, blocksize):
        assert cmd == "RETR /cache/model.3mf"
        callback(sample)

    fake_ftps.retrbinary.side_effect = _retrbinary

    with patch("app.ftp_client.ImplicitFTPS", return_value=fake_ftps):
        out = download_file(
            host="10.0.0.5",
            access_code="x",
            remote_path="/cache/model.3mf",
        )
    assert out == sample
    fake_ftps.login.assert_called_once_with("bblp", "x")
    fake_ftps.prot_p.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ftp_download.py -v`
Expected: FAIL with `ImportError: cannot import name 'download_file' from 'app.ftp_client'`.

- [ ] **Step 3: Implement `download_file`**

Append to `app/ftp_client.py`:

```python
def download_file(*, host: str, access_code: str, remote_path: str, port: int = 990) -> bytes:
    """Download a single file from the printer's FTPS server, returning bytes.

    Mirrors `upload_file`'s connection setup but uses RETR. Like upload, we
    do NOT call `quit()`/`close()` cleanly because Bambu's FTPS daemon does
    not respond to TLS close_notify, which makes graceful shutdown hang.
    """
    chunks: list[bytes] = []

    def _on_chunk(b: bytes) -> None:
        chunks.append(b)

    ftps = ImplicitFTPS()
    ftps.connect(host=host, port=port, timeout=30)
    ftps.login("bblp", access_code)
    ftps.prot_p()
    try:
        ftps.retrbinary(f"RETR {remote_path}", _on_chunk, blocksize=64 * 1024)
    finally:
        try:
            ftps.sock.close()  # avoid the hang; intentional ungraceful close
        except Exception:
            pass

    return b"".join(chunks)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_ftp_download.py -v`
Expected: PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add app/ftp_client.py tests/test_ftp_download.py
git commit -m "feat(ftp): add download_file helper for printer FTPS"
```

---

## Task 7: Endpoint `GET /api/printers/{id}/current-job/file`

**Files:**
- Modify: `app/main.py` (new route after Task 5's events route)
- Create: `tests/test_current_job_file_endpoint.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_current_job_file_endpoint.py`:

```python
"""Tests for GET /api/printers/{id}/current-job/file."""

from __future__ import annotations

import httpx
import pytest

from app import main as main_mod


def _make_stub_service(*, payload=None, ftps_bytes=b"", http_url_bytes=None):
    """Build a minimal printer_service stub for these tests."""
    class _Client:
        def __init__(self, host="10.0.0.5", access_code="x"):
            self.host = host
            self.access_code = access_code
            self.latest_print_payload = payload

    class _Service:
        def get_client(self, pid):
            return _Client() if pid == "S1" else None
        def default_printer_id(self):
            return "S1"

    return _Service()


@pytest.mark.asyncio
async def test_404_when_no_cached_project_file(monkeypatch):
    monkeypatch.setattr(main_mod, "printer_service", _make_stub_service(payload=None))
    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/printers/S1/current-job/file")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ftp_url_triggers_ftps_download(monkeypatch):
    payload = {"command": "project_file", "url": "file:///cache/model.3mf", "task_id": "T1"}
    monkeypatch.setattr(main_mod, "printer_service", _make_stub_service(payload=payload))

    captured = {}
    def _fake_download(*, host, access_code, remote_path, port=990):
        captured["host"] = host
        captured["remote_path"] = remote_path
        return b"FAKE3MFBYTES"
    monkeypatch.setattr(main_mod, "ftp_download_file", _fake_download)

    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/printers/S1/current-job/file")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert resp.content == b"FAKE3MFBYTES"
    assert captured["remote_path"] == "/cache/model.3mf"


@pytest.mark.asyncio
async def test_http_url_passes_through(monkeypatch):
    payload = {"command": "project_file", "url": "https://example.com/m.3mf", "task_id": "T1"}
    monkeypatch.setattr(main_mod, "printer_service", _make_stub_service(payload=payload))

    class _FakeAsyncClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, follow_redirects=True):
            return httpx.Response(200, content=b"HTTP3MF", request=httpx.Request("GET", url))

    monkeypatch.setattr(main_mod, "httpx_AsyncClient", _FakeAsyncClient)

    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/printers/S1/current-job/file")

    assert resp.status_code == 200
    assert resp.content == b"HTTP3MF"


@pytest.mark.asyncio
async def test_task_id_mismatch_returns_409(monkeypatch):
    payload = {"command": "project_file", "url": "file:///cache/x.3mf", "task_id": "T1"}
    monkeypatch.setattr(main_mod, "printer_service", _make_stub_service(payload=payload))
    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/printers/S1/current-job/file?task_id=OTHER")
    assert resp.status_code == 409
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_current_job_file_endpoint.py -v`
Expected: All four FAIL with 404 (route doesn't exist).

- [ ] **Step 3: Implement the endpoint**

In `app/main.py`, near the top with the other imports, add:

```python
from urllib.parse import urlparse

import httpx as _httpx
from app.ftp_client import download_file as ftp_download_file

# Indirection so tests can monkeypatch the async client class:
httpx_AsyncClient = _httpx.AsyncClient
```

After the events route added in Task 5, add:

```python
@app.get("/api/printers/{printer_id}/current-job/file")
async def current_job_file(printer_id: str, task_id: str | None = None):
    """Stream the active print's 3MF.

    Resolves the cached `project_file` payload's `url`. http(s)://
    URLs are passed through; file:// and ftp:// URLs are downloaded from
    the printer over FTPS using the stored access code.

    Optional `task_id` query parameter asserts the active task matches;
    a mismatch returns 409.
    """
    pid = _resolve_printer_id(printer_id)
    client = printer_service.get_client(pid)
    if client is None:
        raise HTTPException(status_code=404, detail="Printer not found")

    payload = client.latest_print_payload
    if not payload or payload.get("command") != "project_file" or not payload.get("url"):
        raise HTTPException(status_code=404, detail="No active print")

    cached_task = payload.get("task_id")
    if task_id is not None and cached_task and task_id != cached_task:
        raise HTTPException(status_code=409, detail="Task ID mismatch")

    url = payload["url"]
    parsed = urlparse(url)

    if parsed.scheme in ("http", "https"):
        async with httpx_AsyncClient(timeout=60.0) as ac:
            r = await ac.get(url, follow_redirects=True)
        if r.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Upstream fetch failed: HTTP {r.status_code}",
            )
        return Response(content=r.content, media_type="application/zip")

    if parsed.scheme in ("file", "ftp"):
        # Bambu's `file://` URLs include the printer host irrelevantly;
        # only the path is meaningful for FTPS RETR.
        remote_path = parsed.path
        try:
            data = await asyncio.to_thread(
                ftp_download_file,
                host=client.host,
                access_code=client.access_code,
                remote_path=remote_path,
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"FTPS download failed: {e}")
        return Response(content=data, media_type="application/zip")

    raise HTTPException(status_code=400, detail=f"Unsupported url scheme: {parsed.scheme}")
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_current_job_file_endpoint.py -v`
Expected: All four PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_current_job_file_endpoint.py
git commit -m "feat(api): GET /api/printers/{id}/current-job/file proxy"
```

---

## Task 8: Document new endpoints in README

**Files:**
- Modify: `README.md` (append rows to the API table that already lists endpoints, if such a table exists; otherwise add a brief section near other API docs)

- [ ] **Step 1: Locate the endpoint inventory in README**

Run: `grep -n "/api/printers" README.md | head -20`
Expected: List of existing endpoint rows.

- [ ] **Step 2: Add two rows / entries describing the new endpoints**

In the same style as the existing rows, append:

- `GET /api/printers/{printer_id}/events` — Server-Sent Events stream. Emits `event: snapshot` once on subscribe (cached `print` payload), `event: report` on every subsequent MQTT message, and `event: keepalive` every 15s. Used by consumers that want live printer telemetry without holding their own MQTT session.
- `GET /api/printers/{printer_id}/current-job/file` — Stream the active print's 3MF. Resolves the cached `project_file` payload's URL (HTTP(S) passthrough or printer FTPS). Optional `task_id` query asserts the active task matches; mismatch returns 409.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document /events and /current-job/file endpoints"
```

---

## Verification & smoke test

After all tasks complete:

- [ ] **Run the full test suite**

Run: `pytest -v`
Expected: All tests pass, no warnings about unawaited coroutines.

- [ ] **Manual smoke test against a real printer (pre-merge)**

1. Set up `printers.json` with a real printer; start the gateway.
2. `curl -N http://localhost:8000/api/printers/{serial}/events` — observe an immediate `event: snapshot` (likely `{}` if printer idle), then `event: keepalive` every 15s.
3. Start a print from OrcaSlicer. Observe `event: report` frames flow, including the `command: "project_file"` payload.
4. Once printing, `curl -o /tmp/job.3mf http://localhost:8000/api/printers/{serial}/current-job/file` and confirm the resulting file is a valid 3MF (`unzip -l /tmp/job.3mf`).
5. With a stale `task_id`: `curl -o /dev/null -w "%{http_code}\n" "http://localhost:8000/api/printers/{serial}/current-job/file?task_id=WRONG"` — expect `409`.
6. After print finishes: same curl as (4) returns `404`.

---

## Self-review checklist (for the implementer)

- All tests above pass on a clean checkout.
- No `print` payload mutation between `_update_status` and `broker.publish` (we shallow-copy with `dict(print_info)`).
- The keepalive timeout uses `asyncio.wait_for`, so cancelled subscribers exit cleanly when the HTTP client disconnects (FastAPI propagates `CancelledError` through the generator).
- `download_file` does not call `quit()`/`close()` cleanly — matches existing `upload_file` rationale (Bambu FTPS doesn't respond to TLS close_notify).
- The 3MF endpoint uses `Response`, not `StreamingResponse`. The 3MF is read entirely into memory before returning. Bambu prints are typically <50 MB; if memory becomes a concern in practice, convert to a streaming download in a follow-up.
