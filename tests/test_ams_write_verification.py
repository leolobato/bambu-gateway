"""AMS filament writes are verified against the printer's echo.

The cloud relay accepts a publish (rc=0) with no delivery guarantee — at
QoS 0 the cloud→printer hop drops messages silently, which is why tray
assignments historically needed manual retries until they "stuck". The write
path now publishes at QoS 1, waits for the cached tray state (fed by printer
reports) to echo the assignment, re-publishes when it doesn't, and raises
when the printer never confirms.
"""
from __future__ import annotations

import json

import pytest

from app.config import PrinterConfig
from app.printer_service import PrinterService


class FakeHost:
    """Records RPCs; lets a test hook run on each send_message."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.on_send_message = None

    async def call(self, method: str, params: dict) -> dict:
        self.calls.append((method, params))
        if method == "send_message" and self.on_send_message is not None:
            self.on_send_message(params)
        return {"rc": 0}

    def sent_commands(self) -> list[str]:
        out = []
        for method, params in self.calls:
            if method != "send_message":
                continue
            payload = json.loads(params["payload"])
            inner = payload.get("print") or payload.get("pushing") or payload.get("info") or {}
            out.append(inner.get("command", "?"))
        return out


class FakeCloudClient:
    """Just enough CloudPrinterClient surface for the confirmation check."""

    def __init__(self) -> None:
        self.trays: list[dict] = []
        self.vt_tray: dict | None = None
        self.command_acks: dict[str, dict] = {}

    def get_ams_info(self):
        return list(self.trays), [], self.vt_tray

    def get_command_ack(self, command: str):
        ack = self.command_acks.get(command)
        return dict(ack) if ack else None


def _wire() -> tuple[PrinterService, FakeHost, FakeCloudClient]:
    cfg = PrinterConfig(ip="10.0.1.157", access_code="ac", serial="S1", name="A1")
    svc = PrinterService([cfg], cloud_mode=True)
    svc._cloud_retry_delay = 0
    svc._ams_echo_timeout = 0.2
    svc._ams_echo_poll = 0.01
    host = FakeHost()
    client = FakeCloudClient()
    svc.set_cloud_printers({"S1": client}, host=host)
    return svc, host, client


def _echo_entry(**overrides) -> dict:
    entry = {
        "ams_id": 0, "tray_id": 1, "tray_info_idx": "GFA00",
        "setting_id": "GFSA00_02", "tray_color": "FFFFFFFF",
    }
    entry.update(overrides)
    return entry


async def _assign(svc: PrinterService) -> None:
    await svc.set_ams_filament(
        "S1", 0, 1,
        tray_info_idx="GFA00", tray_color="FFFFFFFF", tray_type="PLA",
        nozzle_temp_min=190, nozzle_temp_max=240, setting_id="GFSA00_02",
    )


async def test_write_publishes_qos1_and_confirms_on_echo():
    svc, host, client = _wire()

    def apply_on_printer(params: dict) -> None:
        payload = json.loads(params["payload"]).get("print", {})
        if payload.get("command") == "ams_filament_setting":
            client.trays = [_echo_entry()]

    host.on_send_message = apply_on_printer
    await _assign(svc)

    sends = [p for m, p in host.calls if m == "send_message"]
    writes = [p for p in sends
              if json.loads(p["payload"]).get("print", {}).get("command")
              == "ams_filament_setting"]
    assert len(writes) == 1
    assert writes[0]["qos"] == 1


async def test_lost_write_is_republished_until_echo():
    svc, host, client = _wire()
    seen = {"writes": 0}

    def drop_first_write(params: dict) -> None:
        payload = json.loads(params["payload"]).get("print", {})
        if payload.get("command") == "ams_filament_setting":
            seen["writes"] += 1
            if seen["writes"] >= 2:  # first publish is "lost" in the cloud
                client.trays = [_echo_entry()]

    host.on_send_message = drop_first_write
    await _assign(svc)
    assert seen["writes"] == 2


async def test_unconfirmed_write_raises_connection_error():
    svc, host, _client = _wire()  # echo never appears
    with pytest.raises(ConnectionError, match="never"):
        await _assign(svc)
    assert host.sent_commands().count("ams_filament_setting") == \
        svc._ams_write_attempts


async def test_waiting_kicks_a_full_status_refresh():
    """Cloud printers only push AMS state on the ~30s pushall cycle; the
    verify loop must request a snapshot instead of waiting a poll out."""
    svc, host, _client = _wire()
    with pytest.raises(ConnectionError):
        await _assign(svc)
    commands = host.sent_commands()
    assert "pushall" in commands
    assert "get_version" in commands


async def test_no_cloud_client_keeps_fire_and_forget():
    """Bare config (no client to read state from) — verification is
    impossible, so the write must not spin or fail."""
    cfg = PrinterConfig(ip="10.0.1.157", access_code="ac", serial="S1", name="A1")
    svc = PrinterService([cfg], cloud_mode=True)
    svc._cloud_retry_delay = 0
    host = FakeHost()
    svc.set_cloud_printers({}, host=host)
    await _assign(svc)
    assert host.sent_commands().count("ams_filament_setting") == 1


async def test_external_spool_echo_checks_vt_tray():
    svc, host, client = _wire()

    def apply_on_printer(params: dict) -> None:
        payload = json.loads(params["payload"]).get("print", {})
        if payload.get("command") == "ams_filament_setting":
            client.vt_tray = _echo_entry(ams_id=-1, tray_id=-1)

    host.on_send_message = apply_on_printer
    await svc.set_ams_filament(
        "S1", 255, 254,
        tray_info_idx="GFA00", tray_color="FFFFFFFF", tray_type="PLA",
        nozzle_temp_min=190, nozzle_temp_max=240, setting_id="GFSA00_02",
    )
    assert host.sent_commands().count("ams_filament_setting") == 1


async def test_command_ack_confirms_without_state_echo():
    """The direct ack is the primary signal: it must confirm the write even
    when the (firmware-throttled) AMS state echo never arrives in time —
    that lag produced false 409s for writes that actually landed."""
    svc, host, client = _wire()

    def ack_on_printer(params: dict) -> None:
        payload = json.loads(params["payload"]).get("print", {})
        if payload.get("command") == "ams_filament_setting":
            client.command_acks["ams_filament_setting"] = {
                "command": "ams_filament_setting",
                "sequence_id": str(payload["sequence_id"]),
                "result": "success",
                "reason": "",
            }

    host.on_send_message = ack_on_printer
    await _assign(svc)  # client.trays stays empty — ack alone confirms
    assert host.sent_commands().count("ams_filament_setting") == 1


async def test_fail_ack_raises_without_retry():
    svc, host, client = _wire()

    def reject_on_printer(params: dict) -> None:
        payload = json.loads(params["payload"]).get("print", {})
        if payload.get("command") == "ams_filament_setting":
            client.command_acks["ams_filament_setting"] = {
                "command": "ams_filament_setting",
                "sequence_id": str(payload["sequence_id"]),
                "result": "fail",
                "reason": "tray busy",
            }

    host.on_send_message = reject_on_printer
    with pytest.raises(ConnectionError, match="rejected.*tray busy"):
        await _assign(svc)
    assert host.sent_commands().count("ams_filament_setting") == 1


async def test_stale_ack_from_previous_write_is_ignored():
    svc, host, _client = _wire()
    _client.command_acks["ams_filament_setting"] = {
        "command": "ams_filament_setting",
        "sequence_id": "1",  # from some earlier assignment
        "result": "success",
        "reason": "",
    }
    with pytest.raises(ConnectionError):
        await _assign(svc)


async def test_cloud_client_records_command_ack():
    from app.cloud.cloud_printer import CloudPrinterClient
    client = CloudPrinterClient(dev_id="S1")
    await client.handle_event({
        "kind": "OnMessage", "dev_id": "S1",
        "payload": json.dumps({"print": {
            "command": "ams_filament_setting",
            "sequence_id": 20042, "result": "SUCCESS",
        }}),
    })
    ack = client.get_command_ack("ams_filament_setting")
    assert ack == {
        "command": "ams_filament_setting", "sequence_id": "20042",
        "result": "success", "reason": "",
    }
    assert client.get_command_ack("pause") is None


def test_lan_client_records_command_ack():
    from app.mqtt_client import BambuMQTTClient
    client = BambuMQTTClient(
        PrinterConfig(ip="10.0.1.157", access_code="ac", serial="S1", name="A1")
    )
    client._update_status({
        "command": "ams_filament_setting",
        "sequence_id": "20099", "result": "fail", "reason": "unknown filament",
    })
    ack = client.get_command_ack("ams_filament_setting")
    assert ack["result"] == "fail"
    assert ack["reason"] == "unknown filament"


def test_echo_match_is_lenient_on_omitted_fields():
    client = FakeCloudClient()
    match = PrinterService._ams_echo_matches

    # Firmware may omit setting_id/tray_color from the report — still a match.
    client.trays = [_echo_entry(setting_id="", tray_color="")]
    assert match(client, 0, 1, "GFA00", "GFSA00_02", "FFFFFFFF")

    # But an echoed conflicting value is a real mismatch.
    client.trays = [_echo_entry(setting_id="GFSA99_99")]
    assert not match(client, 0, 1, "GFA00", "GFSA00_02", "FFFFFFFF")
    client.trays = [_echo_entry(tray_color="000000FF")]
    assert not match(client, 0, 1, "GFA00", "GFSA00_02", "FFFFFFFF")

    # tray_info_idx is never optional.
    client.trays = [_echo_entry(tray_info_idx="GFA99")]
    assert not match(client, 0, 1, "GFA00", "GFSA00_02", "FFFFFFFF")
