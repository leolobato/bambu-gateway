"""Regression tests for the cloud maintenance tasks."""

import asyncio
from threading import Event

from app.printer_service import PrinterService


def test_periodic_full_status_continues_while_certificate_rpc_is_pending(
    cloud_app_factory, monkeypatch
):
    certificate_started = Event()
    certificate_cancelled = Event()
    status_requested = Event()
    certificate_requests = []

    async def stalled_certificate(self, printer_id):
        certificate_requests.append(printer_id)
        certificate_started.set()
        try:
            await asyncio.Future()
        finally:
            certificate_cancelled.set()

    async def request_full_status(*, host, dev_id):
        if dev_id == "DEV1" and certificate_started.is_set():
            status_requested.set()
        return True

    monkeypatch.setenv("BAMBU_CLOUD_KEEPALIVE_SECS", "0.01")
    monkeypatch.setenv("BAMBU_CLOUD_PUSHALL_SECS", "0.1")
    monkeypatch.setattr(PrinterService, "request_device_cert", stalled_certificate)
    monkeypatch.setattr("app.cloud.session.request_pushall", request_full_status)

    with cloud_app_factory(
        printers=[{"serial": "DEV1", "ip": "10.0.0.5"}],
        fake_env={"FAKE_HOST_USER_LOGGED_IN": "1"},
    ):
        assert certificate_started.wait(timeout=2)
        assert status_requested.wait(timeout=2)
        assert not certificate_cancelled.is_set()
        assert certificate_requests == ["DEV1"]

    assert certificate_cancelled.is_set()
