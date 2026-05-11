"""Tests for MQTT state-change callback hook."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.config import PrinterConfig
from app.models import PrinterState
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


def test_ams_lite_humidity_is_scrubbed_to_unknown():
    """AMS Lite has no sensor; the firmware's placeholder value must not leak through."""
    client = _make_client()
    client._update_status({
        "ams": {
            "ams": [{
                "id": 0,
                "humidity": "5",  # firmware placeholder on AMS Lite
                "temp": "0.0",
                "hw_ver": "AMS_F1.00.00.00",
                "tray": [],
            }],
        },
    })
    assert client._ams_units[0]["ams_type"] == "lite"
    assert client._ams_units[0]["humidity"] == -1


def test_real_ams_humidity_passes_through():
    """A standard AMS reports a real humidity code (1-5); preserve it."""
    client = _make_client()
    client._update_status({
        "ams": {
            "ams": [{
                "id": 0,
                "humidity": "3",
                "temp": "24.5",
                "hw_ver": "AMS08",
                "tray": [],
            }],
        },
    })
    assert client._ams_units[0]["ams_type"] == "standard"
    assert client._ams_units[0]["humidity"] == 3


def test_on_connect_does_not_clobber_offline_state():
    """After the idle disconnect, reconnect must not flip `state` to `idle`
    before the first pushall lands — otherwise the notification hub's
    `offline → real` discovery guard is defeated and a stale terminal state
    (e.g. a print that finished hours ago) fires a phantom "Print complete"
    alert when a stale browser tab refreshes the dashboard.
    """
    client = _make_client()
    # Simulate the state left behind by `_on_disconnect`/`stop`.
    client._status.online = False
    client._status.state = PrinterState.offline

    # Fire `_on_connect` with a mock mqtt client (just needs `.subscribe`).
    mock_mqtt = MagicMock()
    client._client = mock_mqtt  # so publish() can be skipped harmlessly
    client._on_connect(mock_mqtt, None, None, 0)

    # online flips True, but state must remain offline until the printer
    # actually tells us what state it's in.
    assert client._status.online is True
    assert client._status.state == PrinterState.offline


def test_post_reconnect_pushall_callback_sees_prev_offline():
    """End-to-end: after a reconnect, the first `_update_status` (driven by
    the printer's pushall reply) must report `prev.state == offline` so the
    notification hub's discovery guard skips the transition.
    """
    client = _make_client()
    client._status.online = False
    client._status.state = PrinterState.offline

    mock_mqtt = MagicMock()
    client._client = mock_mqtt
    client._on_connect(mock_mqtt, None, None, 0)

    calls: list[tuple] = []
    client.set_status_change_callback(
        lambda prev, new: calls.append((prev.model_copy(), new.model_copy())),
    )
    # Printer's pushall reveals it's been sitting in FINISH for hours.
    client._update_status({"gcode_state": "FINISH"})

    assert len(calls) == 1
    prev, new = calls[0]
    assert prev.state == PrinterState.offline
    assert new.state == PrinterState.finished


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
