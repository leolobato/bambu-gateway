"""Tests for cloud printer discovery merge + model plumbing."""
from __future__ import annotations

from app.cloud.cloud_printer import CloudPrinterClient
from app.cloud.discovery import (
    merge_discovered_devices,
    resolve_machine_setting_id,
)
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


_MACHINES = [
    {"setting_id": "GM021", "name": "Bambu Lab A1 mini 0.2 nozzle",
     "printer_model": "Bambu Lab A1 mini", "nozzle_diameter": "0.2"},
    {"setting_id": "GM020", "name": "Bambu Lab A1 mini 0.4 nozzle",
     "printer_model": "Bambu Lab A1 mini", "nozzle_diameter": "0.4"},
    {"setting_id": "GM023", "name": "Bambu Lab A1 mini 0.8 nozzle",
     "printer_model": "Bambu Lab A1 mini", "nozzle_diameter": "0.8"},
    {"setting_id": "BL-X1C", "name": "Bambu Lab X1 Carbon 0.4 nozzle",
     "printer_model": "Bambu Lab X1 Carbon", "nozzle_diameter": "0.4"},
]


def test_resolve_bare_product_name_to_default_nozzle_setting_id():
    # "A1 mini" -> the 0.4 (stock) variant, not the first listed (0.2).
    assert resolve_machine_setting_id("A1 mini", _MACHINES) == "GM020"


def test_resolve_full_printer_model_name():
    assert resolve_machine_setting_id("Bambu Lab A1 mini", _MACHINES) == "GM020"


def test_resolve_honors_requested_nozzle():
    assert resolve_machine_setting_id("A1 mini", _MACHINES, nozzle="0.8") == "GM023"


def test_resolve_setting_id_is_idempotent():
    assert resolve_machine_setting_id("GM020", _MACHINES) == "GM020"


def test_resolve_returns_none_for_unknown_model():
    assert resolve_machine_setting_id("C11", _MACHINES) is None
    assert resolve_machine_setting_id("", _MACHINES) is None


def test_merge_resolves_machine_model_to_setting_id():
    configs, changed = merge_discovered_devices(
        [], [_dev("S1", name="A1", product="A1 mini")],
        resolve_model=lambda raw: resolve_machine_setting_id(raw, _MACHINES),
    )
    assert changed is True
    assert configs[0].machine_model == "GM020"


def test_merge_resolution_is_idempotent_across_runs():
    resolve = lambda raw: resolve_machine_setting_id(raw, _MACHINES)
    existing = [PrinterConfig(ip="", access_code="", serial="S1",
                              name="A1", machine_model="GM020")]
    _, changed = merge_discovered_devices(
        existing, [_dev("S1", name="A1", product="A1 mini")],
        resolve_model=resolve,
    )
    assert changed is False


def test_merge_keeps_resolved_id_when_resolver_returns_none():
    # A slicer outage (resolver yields None) must not clobber a stored id.
    existing = [PrinterConfig(ip="", access_code="", serial="S1",
                              name="A1", machine_model="GM020")]
    configs, changed = merge_discovered_devices(
        existing, [_dev("S1", name="A1", product="A1 mini")],
        resolve_model=lambda raw: None,
    )
    assert changed is False
    assert next(c for c in configs if c.serial == "S1").machine_model == "GM020"


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


def test_cloud_printer_always_uses_relay_even_with_lan_coords():
    from app.printer_service import PrinterService
    cfg = PrinterConfig(ip="10.0.1.157", access_code="abc", serial="S1", name="A1")
    svc = PrinterService([cfg], cloud_mode=True)
    # Cloud mode never opens a competing local MQTT client, even when the
    # printer has a LAN ip + access code — writes go over the cloud relay.
    assert "S1" not in svc._clients
    svc.sync_printers([cfg])  # an SSDP ip update must not flip it to LAN
    assert "S1" not in svc._clients


def test_non_cloud_printer_uses_lan_client():
    from app.printer_service import PrinterService
    cfg = PrinterConfig(ip="10.0.1.157", access_code="abc", serial="S1", name="A1")
    svc = PrinterService([cfg], cloud_mode=False)
    assert "S1" in svc._clients  # LAN mode: direct MQTT client


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
