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
