from __future__ import annotations

import asyncio

import httpx
import pytest

from app import main as main_mod
from app.config import PrinterConfig
from app.print_tracking import PrintEventBroker


class StubService:
    def __init__(self, broker):
        self.broker = broker

    def default_printer_id(self):
        return "S1"

    def get_print_event_broker(self, printer_id):
        return self.broker if printer_id == "S1" else None

    def get_config(self, printer_id):
        if printer_id != "S1":
            return None
        return PrinterConfig(
            ip="10.0.0.2", access_code="secret", serial="S1",
        )


@pytest.mark.asyncio
async def test_print_events_starts_with_complete_snapshot(monkeypatch):
    broker = PrintEventBroker("S1")
    broker.update({
        "gcode_state": "RUNNING",
        "task_id": "7",
        "subtask_name": "live.3mf",
        "layer_num": 5,
        "total_layer_num": 40,
    })
    monkeypatch.setattr(main_mod, "printer_service", StubService(broker))
    response = await main_mod.printer_print_events("S1")
    iterator = response.body_iterator
    try:
        frame = await asyncio.wait_for(anext(iterator), 0.5)
    finally:
        await iterator.aclose()
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    text = frame.decode() if isinstance(frame, bytes) else frame
    assert "event: snapshot" in text
    assert '"current": 5' in text
    assert '"total": 40' in text


@pytest.mark.asyncio
async def test_current_job_returns_registered_source_and_rejects_stale_key(
    monkeypatch,
):
    broker = PrintEventBroker("S1")
    key = broker.register_source(
        data=b"PK fake 3mf",
        filename="cube.3mf",
        plate_id=1,
        ams_mapping=[0],
    )
    monkeypatch.setattr(main_mod, "printer_service", StubService(broker))
    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test",
    ) as client:
        ok = await client.get(
            "/api/printers/S1/current-job/file", params={"job_key": key},
        )
        stale = await client.get(
            "/api/printers/S1/current-job/file",
            params={"job_key": "old-job"},
        )

    assert ok.status_code == 200
    assert ok.content == b"PK fake 3mf"
    assert ok.headers["x-print-job-key"] == key
    assert stale.status_code == 409


@pytest.mark.asyncio
async def test_current_job_downloads_printer_file_over_ftps(monkeypatch):
    broker = PrintEventBroker("S1")
    snapshot = broker.update({
        "gcode_state": "RUNNING",
        "task_id": "42",
        "subtask_name": "Single color single plate print",
        "gcode_file": "external.3mf",
    })
    captured = {}

    def download_file(**kwargs):
        captured.update(kwargs)
        return b"PK external bytes"

    monkeypatch.setattr(main_mod, "printer_service", StubService(broker))
    monkeypatch.setattr(main_mod.ftp_client, "download_file", download_file)
    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/printers/S1/current-job/file",
            params={"job_key": snapshot["job_key"]},
        )

    assert response.status_code == 200
    assert response.content == b"PK external bytes"
    assert captured["remote_path"] == "/cache/external.3mf"


@pytest.mark.asyncio
async def test_current_job_rejects_non_3mf_payload(monkeypatch):
    """A partial upload or origin error page must fail the request instead
    of being served as application/zip."""
    broker = PrintEventBroker("S1")
    snapshot = broker.update({
        "gcode_state": "RUNNING",
        "task_id": "42",
        "subtask_name": "Single color single plate print",
        "gcode_file": "external.3mf",
    })

    monkeypatch.setattr(main_mod, "printer_service", StubService(broker))
    monkeypatch.setattr(
        main_mod.ftp_client,
        "download_file",
        lambda **kwargs: b"<html>not a 3mf</html>",
    )
    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/printers/S1/current-job/file",
            params={"job_key": snapshot["job_key"]},
        )

    assert response.status_code == 502
    assert "3MF" in response.json()["detail"]
