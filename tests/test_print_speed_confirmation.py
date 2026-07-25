"""Print-speed changes are delivered reliably and reflected immediately.

Two separate failures made a single speed tap look ignored, so users tapped
again (and again):

* the cloud relay publish was QoS 0 — fire-and-forget with no delivery
  guarantee — while the route still answered 200;
* nothing updated the cached ``speed_level`` until the printer volunteered a
  report, and a guaranteed full snapshot only rides the 30s pushall cycle. A
  client polling in between kept reading the old level.

The route now publishes at QoS 1, applies the requested level optimistically
(protected by a short hold-off so in-flight stale reports can't clobber it),
and waits briefly for the printer's ack.
"""
from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest

from app.cloud.cloud_printer import CloudPrinterClient
from app.config import PrinterConfig
from app.models import PrinterStatus
from app.mqtt_client import BambuMQTTClient, apply_print_payload
from app.printer_service import PrinterService


# ---------------------------------------------------------------------------
# The hold-off in the shared payload parser
# ---------------------------------------------------------------------------


def test_reported_speed_ignored_while_hold_active():
    status = PrinterStatus(id="S1", name="A1", speed_level=3)
    apply_print_payload(
        status, {"spd_lvl": 2},
        speed_level_hold_until=time.monotonic() + 5,
    )
    assert status.speed_level == 3


def test_reported_speed_applied_once_hold_expires():
    status = PrinterStatus(id="S1", name="A1", speed_level=3)
    apply_print_payload(
        status, {"spd_lvl": 2},
        speed_level_hold_until=time.monotonic() - 0.01,
    )
    assert status.speed_level == 2


def test_reported_speed_applied_when_no_hold_requested():
    status = PrinterStatus(id="S1", name="A1", speed_level=1)
    apply_print_payload(status, {"spd_lvl": 4})
    assert status.speed_level == 4


# ---------------------------------------------------------------------------
# Cloud client — optimistic level survives a stale report
# ---------------------------------------------------------------------------


def _report(spd_lvl: int) -> dict:
    return {
        "dev_id": "S1",
        "kind": "OnMessage",
        "payload": json.dumps({"print": {"spd_lvl": spd_lvl}}),
    }


async def test_optimistic_speed_survives_stale_report():
    client = CloudPrinterClient(dev_id="S1")
    client.apply_optimistic_speed(4)
    # A report published before the command landed still carries the old level.
    await client.handle_event(_report(2))
    assert client.get_status().speed_level == 4


async def test_clear_speed_hold_lets_reported_speed_win_again():
    client = CloudPrinterClient(dev_id="S1")
    client.apply_optimistic_speed(4)
    client.clear_speed_hold()
    await client.handle_event(_report(2))
    assert client.get_status().speed_level == 2


# ---------------------------------------------------------------------------
# LAN client
# ---------------------------------------------------------------------------


def _lan_client(monkeypatch) -> tuple[BambuMQTTClient, list[dict]]:
    """A LAN client with the transport stubbed out — no socket is opened."""
    cfg = PrinterConfig(ip="10.0.1.157", access_code="ac", serial="S1", name="A1")
    client = BambuMQTTClient(cfg)
    published: list[dict] = []
    monkeypatch.setattr(client, "publish", published.append)
    monkeypatch.setattr(client, "ensure_connected", lambda *a, **k: True)
    return client, published


def test_send_print_speed_applies_level_and_returns_sequence_id(monkeypatch):
    client, published = _lan_client(monkeypatch)

    sequence_id = client.send_print_speed(3)

    assert len(published) == 1
    sent = published[0]["print"]
    assert sent["command"] == "print_speed"
    assert sent["param"] == "3"
    # The ack is matched on this id, so the caller must get the published one.
    assert sequence_id == sent["sequence_id"]
    assert client.get_status().speed_level == 3


def test_send_print_speed_holds_off_stale_reports(monkeypatch):
    client, _ = _lan_client(monkeypatch)
    client.send_print_speed(3)
    client._update_status({"print": {"spd_lvl": 1}})
    assert client.get_status().speed_level == 3


# ---------------------------------------------------------------------------
# PrinterService.await_command_ack
# ---------------------------------------------------------------------------


class FakeAckClient:
    def __init__(self) -> None:
        self.acks: dict[str, dict] = {}

    def get_command_ack(self, command: str):
        ack = self.acks.get(command)
        return dict(ack) if ack else None


def _svc() -> tuple[PrinterService, FakeAckClient]:
    cfg = PrinterConfig(ip="10.0.1.157", access_code="ac", serial="S1", name="A1")
    svc = PrinterService([cfg], cloud_mode=True)
    svc._control_ack_timeout = 0.2
    svc._control_ack_poll = 0.01
    client = FakeAckClient()
    svc.set_cloud_printers({"S1": client}, host=None)
    return svc, client


async def test_await_command_ack_returns_matching_ack():
    svc, client = _svc()
    client.acks["print_speed"] = {
        "command": "print_speed", "sequence_id": "20001",
        "result": "success", "reason": "",
    }
    ack = await svc.await_command_ack("S1", "print_speed", "20001")
    assert ack is not None and ack["result"] == "success"


async def test_await_command_ack_ignores_a_previous_commands_ack():
    """An ack for an earlier sequence_id must not confirm this command."""
    svc, client = _svc()
    client.acks["print_speed"] = {
        "command": "print_speed", "sequence_id": "19999",
        "result": "success", "reason": "",
    }
    assert await svc.await_command_ack("S1", "print_speed", "20001") is None


async def test_await_command_ack_returns_none_on_timeout():
    svc, _ = _svc()
    assert await svc.await_command_ack("S1", "print_speed", "20001") is None


# ---------------------------------------------------------------------------
# Route behaviour
# ---------------------------------------------------------------------------


@pytest.fixture
def cloud_speed_app(cloud_app_factory):
    """Cloud-mode TestClient with a short ack window (the fake host never acks)."""
    import app.main as main_mod

    with cloud_app_factory(
        printers=[{"serial": "DEV1", "ip": "10.0.0.9"}],
        fake_env={},
    ) as client:
        main_mod.printer_service._control_ack_timeout = 0.05
        main_mod.printer_service._control_ack_poll = 0.01
        yield client


def _speed_publishes(record_file) -> list[dict]:
    out = []
    for line in record_file.read_text().splitlines():
        rec = json.loads(line)
        if rec.get("method") != "send_message":
            continue
        payload = json.loads(rec["params"]["payload"])
        if payload.get("print", {}).get("command") == "print_speed":
            out.append(rec["params"])
    return out


def test_speed_route_publishes_at_qos1(cloud_speed_app):
    """QoS 0 drops the packet silently and the route still answers 200."""
    resp = cloud_speed_app.post("/api/printers/DEV1/speed", json={"level": 2})
    assert resp.status_code == 200, resp.text

    publishes = _speed_publishes(cloud_speed_app.rpc_record_file)
    assert len(publishes) == 1
    assert publishes[0]["qos"] == 1


def test_speed_route_reports_new_level_before_the_printer_echoes(cloud_speed_app):
    """The whole point: the next poll shows the requested level, not the old one."""
    resp = cloud_speed_app.post("/api/printers/DEV1/speed", json={"level": 4})
    assert resp.status_code == 200, resp.text

    printers = cloud_speed_app.get("/api/printers").json()["printers"]
    assert printers[0]["speed_level"] == 4


def test_speed_route_reports_unconfirmed_when_no_ack_arrives(cloud_speed_app):
    """Silence is not failure — the command still went out, so this is a 200."""
    resp = cloud_speed_app.post("/api/printers/DEV1/speed", json={"level": 2})
    assert resp.status_code == 200, resp.text
    assert resp.json()["confirmed"] is None


def test_speed_route_maps_an_explicit_rejection_to_502(cloud_speed_app, monkeypatch):
    import app.main as main_mod

    monkeypatch.setattr(
        main_mod.printer_service, "await_command_ack",
        AsyncMock(return_value={
            "command": "print_speed", "sequence_id": "20001",
            "result": "fail", "reason": "not printing",
        }),
    )
    resp = cloud_speed_app.post("/api/printers/DEV1/speed", json={"level": 2})
    assert resp.status_code == 502
    assert "not printing" in resp.json()["detail"]
