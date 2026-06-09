"""In cloud mode each printer must exist exactly once — as a cloud client.

Regression tests for the duplicate-registry bug: PrinterService used to
create a LAN BambuMQTTClient for every config even in cloud mode, so
GET /api/printers listed each printer twice and the stale LAN entry
shadowed the live cloud status.
"""
from __future__ import annotations

import json

from app.config import PrinterConfig
from app.printer_service import PrinterService


def _cfg(serial: str, name: str = "") -> PrinterConfig:
    return PrinterConfig(ip="10.0.0.9", access_code="ac", serial=serial, name=name)


def test_cloud_mode_creates_no_lan_clients():
    svc = PrinterService([_cfg("DEV1"), _cfg("DEV2")], cloud_mode=True)
    assert svc._clients == {}
    # Configs are still tracked (names, settings CRUD, etc.).
    assert set(c.serial for c in svc.get_configs()) == {"DEV1", "DEV2"}


def test_lan_mode_still_creates_lan_clients():
    svc = PrinterService([_cfg("DEV1")])
    assert set(svc._clients) == {"DEV1"}


def test_cloud_mode_statuses_have_one_entry_per_serial():
    from app.cloud.cloud_printer import CloudPrinterClient

    svc = PrinterService([_cfg("DEV1")], cloud_mode=True)
    svc.set_cloud_printers({"DEV1": CloudPrinterClient(dev_id="DEV1")})

    statuses = svc.get_all_statuses()
    assert [s.id for s in statuses] == ["DEV1"]
    assert svc.get_status("DEV1") is not None


def test_cloud_mode_default_printer_id_resolves_cloud_serial():
    from app.cloud.cloud_printer import CloudPrinterClient

    svc = PrinterService([_cfg("DEV1")], cloud_mode=True)
    svc.set_cloud_printers({"DEV1": CloudPrinterClient(dev_id="DEV1")})
    assert svc.default_printer_id() == "DEV1"


def test_cloud_mode_sync_printers_updates_cloud_registry_in_place():
    from app.cloud.cloud_printer import CloudPrinterClient

    svc = PrinterService([_cfg("DEV1")], cloud_mode=True)
    cloud_map = {"DEV1": CloudPrinterClient(dev_id="DEV1")}
    svc.set_cloud_printers(cloud_map)

    svc.sync_printers([_cfg("DEV2", name="New One")])

    # No LAN clients ever appear in cloud mode.
    assert svc._clients == {}
    # The same dict object is mutated so app.state and the EventPump handler
    # closures observe the change.
    assert set(cloud_map) == {"DEV2"}
    assert cloud_map["DEV2"].get_status().name == "New One"
    assert svc.get_status("DEV2") is not None
    assert svc.get_status("DEV1") is None


def test_lifespan_lists_each_cloud_printer_once(cloud_app_factory):
    with cloud_app_factory(
        printers=[{"serial": "DEVA", "ip": "10.0.0.5"}],
        fake_env={"FAKE_HOST_USER_LOGGED_IN": "1"},
    ) as client:
        resp = client.get("/api/printers")
        assert resp.status_code == 200
        ids = [p["id"] for p in resp.json()["printers"]]
        assert ids == ["DEVA"]
