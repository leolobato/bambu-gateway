"""Cloud printers parse AMS from the same print.ams payload as LAN."""
from __future__ import annotations

import asyncio
import json

from app.cloud.cloud_printer import CloudPrinterClient


def _event(dev_id: str, print_payload: dict) -> dict:
    return {
        "dev_id": dev_id,
        "kind": "OnMessage",
        "payload": json.dumps({"print": print_payload}),
    }


def test_cloud_client_parses_ams_trays_units_and_vt_tray():
    client = CloudPrinterClient(dev_id="S1", name="P")
    payload = {
        "ams": {
            "tray_now": "1",
            "ams": [
                {
                    "id": "0",
                    "humidity": "4",
                    "temp": "28.5",
                    "hw_ver": "AMS08",
                    "tray": [
                        {"id": "0", "tray_type": "PLA",
                         "tray_color": "FF0000FF", "remain": "80"},
                        {"id": "1", "tray_type": "PETG",
                         "tray_color": "00FF00FF", "remain": "50"},
                    ],
                }
            ],
            "vt_tray": {"id": "254", "tray_type": "PLA", "remain": "10"},
        }
    }
    asyncio.run(client.handle_event(_event("S1", payload)))

    trays, units, vt = client.get_ams_info()
    assert len(units) == 1
    assert units[0]["id"] == 0
    assert units[0]["tray_count"] == 2
    assert len(trays) == 2
    assert trays[0]["slot"] == 0
    assert trays[0]["tray_type"] == "PLA"
    assert trays[0]["remain"] == 80  # normalized to int
    assert trays[1]["slot"] == 1
    assert vt is not None and vt["slot"] == 254
    assert client.get_status().active_tray == 1


def test_cloud_client_ignores_foreign_device_events():
    client = CloudPrinterClient(dev_id="S1", name="P")
    payload = {"ams": {"ams": [{"id": "0", "tray": []}]}}
    asyncio.run(client.handle_event(_event("OTHER", payload)))
    trays, units, _ = client.get_ams_info()
    assert trays == [] and units == []


def _version_event(dev_id: str, modules: list[dict]) -> dict:
    return {
        "dev_id": dev_id, "kind": "OnMessage",
        "payload": json.dumps(
            {"info": {"command": "get_version", "module": modules}}
        ),
    }


def test_cloud_get_version_marks_ams_lite_sensorless():
    """An AMS Lite (module ams_f1/0) has no hygrometer — the cloud path must
    read the type from get_version and suppress humidity, even when the AMS
    report carries an empty hw_ver."""
    client = CloudPrinterClient(dev_id="S1", name="P")
    asyncio.run(client.handle_event(_version_event("S1", [{"name": "ams_f1/0"}])))
    # AMS report with NO hw_ver and a humidity placeholder, as the cloud sends.
    payload = {"ams": {"ams": [{
        "id": "0", "humidity": "5", "hw_ver": "",
        "tray": [{"id": "0", "tray_type": "PLA"}],
    }]}}
    asyncio.run(client.handle_event(_event("S1", payload)))
    _, units, _ = client.get_ams_info()
    assert units[0]["ams_type"] == "lite"
    assert units[0]["humidity"] == -1  # suppressed (no sensor)
    assert units[0]["supports_drying"] is False


def test_cloud_get_version_detects_ams_pro_keeps_humidity():
    """Same A1-mini-class printer, but an AMS 2 Pro (n3f/0) DOES have a sensor —
    type is hardware, not model, so humidity is kept."""
    client = CloudPrinterClient(dev_id="S1", name="P")
    asyncio.run(client.handle_event(_version_event("S1", [{"name": "n3f/0"}])))
    payload = {"ams": {"ams": [{
        "id": "0", "humidity": "3", "hw_ver": "",
        "tray": [{"id": "0", "tray_type": "PLA"}],
    }]}}
    asyncio.run(client.handle_event(_event("S1", payload)))
    _, units, _ = client.get_ams_info()
    assert units[0]["ams_type"] == "pro"
    assert units[0]["humidity"] == 3
    assert units[0]["supports_drying"] is True


def test_partial_ams_report_does_not_wipe_cached_trays():
    """Bambu sends partial `ams` blocks (no unit list) during a filament
    change. They must NOT clear the cached trays/units (the dashboard's
    "AMS disappeared" regression)."""
    client = CloudPrinterClient(dev_id="S1", name="P")
    full = {
        "ams": {
            "tray_now": "1",
            "ams": [{
                "id": "0", "hw_ver": "AMS08",
                "tray": [{"id": "0", "tray_type": "PLA", "remain": "80"}],
            }],
        }
    }
    asyncio.run(client.handle_event(_event("S1", full)))
    assert len(client.get_ams_info()[0]) == 1  # tray cached

    # Partial reports the printer emits mid-operation — ams dict but no units.
    for partial in ({"ams": {"tray_now": "255"}},
                    {"ams": {"version": 1}},
                    {"ams": {"ams": []}}):
        asyncio.run(client.handle_event(_event("S1", partial)))
        trays, units, _ = client.get_ams_info()
        assert len(trays) == 1, f"{partial} wiped trays"
        assert len(units) == 1, f"{partial} wiped units"
    # active_tray still tracks the partial report's tray_now (255 -> None).
    assert client.get_status().active_tray is None
