"""Tests for MQTT state-change callback hook."""

from __future__ import annotations

from app.config import PrinterConfig
from app.mqtt_client import BambuMQTTClient


def _make_client() -> BambuMQTTClient:
    return BambuMQTTClient(PrinterConfig(
        ip="1.2.3.4", access_code="0000", serial="P01",
    ))


def test_callback_fires_with_prev_and_new_snapshots():
    client = _make_client()
    calls: list[tuple] = []
    client.set_status_change_callback(lambda prev, new: calls.append((prev.model_copy(), new.model_copy())))
    client._update_status({"nozzle_temper": 200.0})
    assert len(calls) == 1
    prev, new = calls[0]
    assert prev.temperatures.nozzle_temp == 0.0
    assert new.temperatures.nozzle_temp == 200.0


def test_callback_exception_does_not_break_update():
    client = _make_client()
    client.set_status_change_callback(lambda prev, new: (_ for _ in ()).throw(RuntimeError("boom")))
    # Should not raise
    client._update_status({"nozzle_temper": 42.0})
    assert client._status.temperatures.nozzle_temp == 42.0


def test_no_callback_works_fine():
    client = _make_client()
    client._update_status({"nozzle_temper": 1.0})
    assert client._status.temperatures.nozzle_temp == 1.0


def test_consecutive_updates_have_consistent_prev_new_chain():
    """Call N+1's prev snapshot must equal call N's new snapshot."""
    client = _make_client()
    calls: list[tuple] = []
    client.set_status_change_callback(lambda prev, new: calls.append((prev.model_copy(), new.model_copy())))

    client._update_status({"nozzle_temper": 100.0})
    client._update_status({"nozzle_temper": 200.0})

    assert len(calls) == 2
    first_new = calls[0][1]
    second_prev = calls[1][0]
    assert first_new.temperatures.nozzle_temp == second_prev.temperatures.nozzle_temp == 100.0
