"""Tests for AMS auto-refill status and command parity with OrcaSlicer GUI."""

from __future__ import annotations

from unittest.mock import patch

from app.config import PrinterConfig
from app.mqtt_client import BambuMQTTClient


def _make_client() -> BambuMQTTClient:
    return BambuMQTTClient(PrinterConfig(
        ip="1.2.3.4",
        access_code="0000",
        serial="P01",
        machine_model="X1C",
    ))


def test_auto_refill_status_parses_cfg_bit_18():
    client = _make_client()

    client._update_status({"cfg": str(1 << 18)})
    assert client._status.ams_auto_refill_enabled is True

    client._update_status({"cfg": "0"})
    assert client._status.ams_auto_refill_enabled is False


def test_auto_refill_status_parses_home_flag_bit_10():
    client = _make_client()

    client._update_status({"home_flag": 1 << 10})
    assert client._status.ams_auto_refill_enabled is True

    client._update_status({"home_flag": 0})
    assert client._status.ams_auto_refill_enabled is False


def test_auto_refill_support_parses_reported_capability():
    client = _make_client()

    client._update_status({"support_filament_backup": True})
    assert client._status.ams_auto_refill_supported is True

    client._update_status({"support_filament_backup": False})
    assert client._status.ams_auto_refill_supported is False


def test_send_ams_auto_refill_publishes_orca_print_option_payload():
    client = _make_client()

    with patch.object(BambuMQTTClient, "publish") as publish:
        client.send_ams_auto_refill(True)

    publish.assert_called_once()
    inner = publish.call_args.args[0]["print"]
    assert inner["command"] == "print_option"
    assert inner["auto_switch_filament"] is True
    # sequence_id must be a fresh value in the firmware's studio range so the
    # printer accepts the command over the cloud relay (not the old "0").
    assert 20000 <= int(inner["sequence_id"]) < 30000
    assert client._status.ams_auto_refill_enabled is True


from fastapi.testclient import TestClient

from app import main as app_main


def test_get_ams_includes_auto_refill_fields(monkeypatch):
    class _StubService:
        def default_printer_id(self) -> str:
            return "P01"

        def get_client(self, pid: str):
            return object() if pid == "P01" else None

        def get_status(self, pid: str):
            if pid != "P01":
                return None
            from app.models import PrinterStatus

            return PrinterStatus(
                id="P01",
                name="Printer",
                online=True,
                ams_auto_refill_enabled=True,
                ams_auto_refill_supported=True,
            )

        async def get_ams_info_async(self, pid: str):
            return [], [], None

    async def _no_filaments(_pid):
        return [], ""

    monkeypatch.setattr(app_main, "printer_service", _StubService())
    monkeypatch.setattr(app_main, "_get_machine_slicer_filaments", _no_filaments)

    response = TestClient(app_main.app).get("/api/ams?printer_id=P01")

    assert response.status_code == 200
    body = response.json()
    assert body["auto_refill_enabled"] is True
    assert body["auto_refill_supported"] is True


def test_set_auto_refill_route_publishes_service_command(monkeypatch):
    calls: list[tuple[str, bool]] = []

    class _StubService:
        def get_client(self, pid: str):
            return object() if pid == "P01" else None

        def set_ams_auto_refill(self, pid: str, enabled: bool) -> None:
            calls.append((pid, enabled))

    monkeypatch.setattr(app_main, "printer_service", _StubService())

    response = TestClient(app_main.app).post(
        "/api/printers/P01/ams/auto-refill",
        json={"enabled": True},
    )

    assert response.status_code == 200
    assert response.json()["command"] == "ams_auto_refill:on"
    assert calls == [("P01", True)]


def test_set_auto_refill_route_maps_offline_to_409(monkeypatch):
    class _StubService:
        def get_client(self, pid: str):
            return object() if pid == "P01" else None

        def set_ams_auto_refill(self, pid: str, enabled: bool) -> None:
            raise ConnectionError("Printer P01 is offline")

    monkeypatch.setattr(app_main, "printer_service", _StubService())

    response = TestClient(app_main.app).post(
        "/api/printers/P01/ams/auto-refill",
        json={"enabled": False},
    )

    assert response.status_code == 409
