"""AMS filament writes go through the plugin's CLOUD relay (send_message).

A live RPC trace of OrcaSlicer assigning a filament to a cloud-bound A1 Mini
(``docs/bridge_traces`` in the OrcaSlicer-bambulab repo) is decisive: the
``ams_filament_setting`` write rides ``net.send_message`` and returns rc 0 —
there is no ``connect_printer`` / ``send_message_to_printer`` / LAN link. The
gateway's earlier -2 was a session difference: the plugin agent must be branded
as a BambuStudio/slicer client (``set_extra_http_header`` at bring-up) for the
relay to accept ``print``-namespace writes. These tests pin that routing:
writes publish via the cloud relay, never the local link.
"""
from __future__ import annotations

import json

import pytest

from app.config import PrinterConfig
from app.printer_service import PrinterService


class FakeHost:
    """Records RPCs. send_message returns a configurable rc (per attempt)."""

    def __init__(self, send_rcs: list[int] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        # rc per send_message attempt; the last value repeats once exhausted.
        self._send_rcs = list(send_rcs) if send_rcs is not None else [0]
        self.svc: PrinterService | None = None

    async def call(self, method: str, params: dict) -> dict:
        self.calls.append((method, params))
        if method == "send_message":
            idx = sum(1 for m, _ in self.calls if m == "send_message") - 1
            rc = self._send_rcs[min(idx, len(self._send_rcs) - 1)]
            return {"rc": rc}
        if method == "install_device_cert":
            return {"dispatched": True}
        if method == "set_extra_http_header":
            return {"rc": 0}
        return {"rc": 0}


def _cfg() -> PrinterConfig:
    return PrinterConfig(ip="10.0.1.157", access_code="ac", serial="S1", name="A1")


def _wire(**kw) -> tuple[PrinterService, FakeHost]:
    svc = PrinterService([_cfg()], cloud_mode=True)
    host = FakeHost(**kw)
    host.svc = svc
    svc.set_cloud_printers({}, host=host)
    return svc, host


async def _assign(svc: PrinterService, serial: str) -> None:
    await svc.set_ams_filament(
        serial, 0, 1,
        tray_info_idx="GFA00", tray_color="FFFFFFFF", tray_type="PLA",
        nozzle_temp_min=190, nozzle_temp_max=240, setting_id="GFSA00_02",
    )


async def test_write_goes_through_cloud_relay():
    svc, host = _wire()
    await _assign(svc, "S1")

    methods = [m for m, _ in host.calls]
    assert "send_message" in methods  # the cloud relay
    assert "send_message_to_printer" not in methods  # never the local link
    assert "connect_printer" not in methods
    send = next(p for m, p in host.calls if m == "send_message")
    assert send["dev_id"] == "S1"
    write = json.loads(send["payload"])["print"]
    assert write["command"] == "ams_filament_setting"
    assert write["slot_id"] == 1 and write["tray_id"] == 1
    assert write["setting_id"] == "GFSA00_02"


async def test_write_does_not_churn_device_cert():
    """The secure channel is kept warm by the ~1Hz install_device_cert
    heartbeat (see _cloud_keepalive), NOT re-issued per write — a fresh install
    briefly drops the channel, so the write path must leave the cert alone."""
    svc, host = _wire()
    await _assign(svc, "S1")
    assert "install_device_cert" not in [m for m, _ in host.calls]


async def test_cert_heartbeat_issues_cloud_cert():
    """request_device_cert (the heartbeat unit) installs the cloud cert."""
    svc, host = _wire()
    await svc.request_device_cert("S1")
    cert = next(p for m, p in host.calls if m == "install_device_cert")
    assert cert["dev_id"] == "S1" and cert["lan_only"] is False


async def test_transient_minus_two_is_retried_then_succeeds():
    svc, host = _wire(send_rcs=[-2, 0])  # first attempt bounces, second lands
    svc._cloud_retry_delay = 0  # don't sleep in tests
    await _assign(svc, "S1")
    assert [m for m, _ in host.calls].count("send_message") == 2


async def test_persistent_rejection_raises():
    svc, host = _wire(send_rcs=[-2])  # relay keeps rejecting
    svc._cloud_retry_delay = 0
    with pytest.raises(ConnectionError):
        await _assign(svc, "S1")


async def test_unknown_printer_raises_value_error():
    svc, _ = _wire()
    with pytest.raises(ValueError):
        await _assign(svc, "NOPE")
