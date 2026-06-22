"""Tests for cloud printer discovery merge + model plumbing."""
from __future__ import annotations

from app.cloud.cloud_printer import CloudPrinterClient
from app.cloud.discovery import merge_discovered_devices
from app.config import PrinterConfig


def _dev(dev_id, name="", product="", model="", access=""):
    return {
        "dev_id": dev_id,
        "dev_name": name,
        "dev_product_name": product,
        "dev_model_name": model,
        "dev_access_code": access,
        "dev_online": True,
    }


def test_merge_captures_cloud_access_code():
    configs, changed = merge_discovered_devices(
        [], [_dev("S1", name="A1", access="12345678")]
    )
    assert changed is True
    assert configs[0].access_code == "12345678"


def test_merge_updates_existing_access_code():
    existing = [PrinterConfig(ip="10.0.1.157", access_code="", serial="S1",
                              name="A1", machine_model="A1 mini")]
    configs, changed = merge_discovered_devices(
        existing, [_dev("S1", name="A1", access="87654321")]
    )
    assert changed is True
    assert next(c for c in configs if c.serial == "S1").access_code == "87654321"


def test_cloud_printer_uses_relay_until_ssdp_promotes_it():
    from app.printer_service import PrinterService
    cfg = PrinterConfig(ip="10.0.1.157", access_code="abc", serial="S1", name="A1")
    svc = PrinterService([cfg], cloud_mode=True)
    # Having ip + access_code is not enough on its own — the cloud test model
    # seeds those too; only an SSDP promotion flips a printer to LAN.
    assert "S1" not in svc._clients

    svc.promote_lan("S1")
    svc.sync_printers([cfg])
    assert "S1" in svc._clients  # now a read-write LAN client


def test_promotion_without_lan_coords_stays_on_relay():
    from app.printer_service import PrinterService
    cfg = PrinterConfig(ip="", access_code="", serial="S1", name="A1")
    svc = PrinterService([cfg], cloud_mode=True)
    svc.promote_lan("S1")
    svc.sync_printers([cfg])
    assert "S1" not in svc._clients  # no ip/access_code → can't go LAN


def test_merge_adds_unknown_device_with_name_and_model():
    configs, changed = merge_discovered_devices(
        [], [_dev("S1", name="Workshop A1", product="Bambu Lab A1 mini")]
    )
    assert changed is True
    assert len(configs) == 1
    cfg = configs[0]
    assert cfg.serial == "S1"
    assert cfg.name == "Workshop A1"
    assert cfg.machine_model == "Bambu Lab A1 mini"
    assert cfg.ip == "" and cfg.access_code == ""


def test_merge_updates_existing_name_and_model():
    existing = [PrinterConfig(ip="", access_code="", serial="S1",
                              name="Bambu Printer", machine_model="")]
    configs, changed = merge_discovered_devices(
        existing, [_dev("S1", name="Real Name", product="Bambu Lab X1 Carbon")]
    )
    assert changed is True
    cfg = next(c for c in configs if c.serial == "S1")
    assert cfg.name == "Real Name"
    assert cfg.machine_model == "Bambu Lab X1 Carbon"


def test_merge_falls_back_to_model_name_when_no_product():
    configs, _ = merge_discovered_devices([], [_dev("S1", model="C11")])
    assert configs[0].machine_model == "C11"


def test_merge_preserves_unrelated_configs():
    existing = [PrinterConfig(ip="10.0.0.5", access_code="abc", serial="LAN1",
                              name="Lan Printer", machine_model="X1C")]
    configs, changed = merge_discovered_devices(existing, [_dev("S1", name="Cloud")])
    assert changed is True
    serials = {c.serial for c in configs}
    assert serials == {"LAN1", "S1"}
    lan = next(c for c in configs if c.serial == "LAN1")
    assert lan.ip == "10.0.0.5" and lan.name == "Lan Printer"


def test_merge_no_change_when_already_current():
    existing = [PrinterConfig(ip="", access_code="", serial="S1",
                              name="Real", machine_model="Bambu Lab A1")]
    _, changed = merge_discovered_devices(
        existing, [_dev("S1", name="Real", product="Bambu Lab A1")]
    )
    assert changed is False


def test_merge_skips_devices_without_serial():
    configs, changed = merge_discovered_devices([], [_dev("", name="x")])
    assert configs == [] and changed is False


def test_cloud_client_exposes_machine_model():
    client = CloudPrinterClient(
        dev_id="S1", name="P", machine_model="Bambu Lab A1 mini"
    )
    assert client.get_status().machine_model == "Bambu Lab A1 mini"
    client.set_machine_model("Bambu Lab X1")
    assert client.get_status().machine_model == "Bambu Lab X1"
