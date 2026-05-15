"""Tests for BambuMQTTClient — payload caching & broker publish."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from app.config import PrinterConfig
from app.mqtt_client import BambuMQTTClient


def _make_client() -> BambuMQTTClient:
    config = PrinterConfig(
        ip="127.0.0.1",
        access_code="x",
        serial="S01",
        name="test",
    )
    return BambuMQTTClient(config)


def test_on_message_caches_first_push_status_into_aggregate():
    client = _make_client()
    msg = MagicMock()
    msg.payload = json.dumps({
        "print": {"command": "push_status", "layer_num": 42, "gcode_state": "RUNNING"}
    }).encode()
    msg.topic = "device/S01/report"
    client._on_message(None, None, msg)
    assert client.latest_print_payload == {
        "command": "push_status",
        "layer_num": 42,
        "gcode_state": "RUNNING",
    }


def test_on_message_ignores_non_print_payload():
    client = _make_client()
    msg = MagicMock()
    msg.payload = json.dumps({"info": {"command": "get_version"}}).encode()
    msg.topic = "device/S01/report"
    client._on_message(None, None, msg)
    assert client.latest_print_payload == {}


def test_on_message_merges_subsequent_push_status_into_aggregate():
    """The aggregate accumulates fields across deltas instead of overwriting."""
    client = _make_client()
    first = MagicMock()
    first.payload = json.dumps({
        "print": {"command": "push_status", "layer_num": 1, "wifi_signal": "-60dBm"}
    }).encode()
    first.topic = "device/S01/report"
    second = MagicMock()
    second.payload = json.dumps({
        "print": {"command": "push_status", "layer_num": 2, "gcode_state": "RUNNING"}
    }).encode()
    second.topic = "device/S01/report"
    client._on_message(None, None, first)
    client._on_message(None, None, second)
    # `wifi_signal` survives from the first delta; `layer_num` overwrites;
    # `gcode_state` is added.
    assert client.latest_print_payload == {
        "command": "push_status",
        "layer_num": 2,
        "wifi_signal": "-60dBm",
        "gcode_state": "RUNNING",
    }


@pytest.mark.asyncio
async def test_on_message_publishes_to_broker():
    from app.print_event_broker import PrintEventBroker

    loop = asyncio.get_running_loop()
    broker = PrintEventBroker()
    client = _make_client()
    client.attach_event_broker(broker, loop)

    async with broker.subscribe() as queue:
        msg = MagicMock()
        msg.payload = json.dumps({"print": {"layer_num": 7}}).encode()
        msg.topic = "device/S01/report"
        client._on_message(None, None, msg)
        event = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert event == {"layer_num": 7}


def test_recursive_merge_flat_keys():
    from app.mqtt_client import _recursive_merge

    dst = {"a": 1, "b": 2}
    _recursive_merge(dst, {"b": 20, "c": 30})
    assert dst == {"a": 1, "b": 20, "c": 30}


def test_recursive_merge_nested_dicts():
    from app.mqtt_client import _recursive_merge

    dst = {"ams": {"version": "1.0", "trays": "untouched"}, "outer": "kept"}
    _recursive_merge(dst, {"ams": {"version": "1.1"}})
    assert dst == {
        "ams": {"version": "1.1", "trays": "untouched"},
        "outer": "kept",
    }


def test_recursive_merge_replaces_lists_wholesale():
    from app.mqtt_client import _recursive_merge

    dst = {"trays": [{"id": 0}, {"id": 1}]}
    _recursive_merge(dst, {"trays": [{"id": 0, "color": "red"}]})
    assert dst == {"trays": [{"id": 0, "color": "red"}]}


def test_recursive_merge_dict_replaces_scalar():
    """If dst has a scalar and src brings a dict for the same key, src wins."""
    from app.mqtt_client import _recursive_merge

    dst = {"vt_tray": None}
    _recursive_merge(dst, {"vt_tray": {"id": 254, "color": "blue"}})
    assert dst == {"vt_tray": {"id": 254, "color": "blue"}}


def test_on_message_caches_project_file_separately():
    """`project_file` payloads go into their own cache, not the aggregate."""
    client = _make_client()
    pf = MagicMock()
    pf.payload = json.dumps({
        "print": {
            "command": "project_file",
            "url": "file:///cache/m.3mf",
            "param": "Metadata/plate_1.gcode",
            "task_id": "T1",
            "subtask_id": "ST1",
            "ams_mapping": [0, 1],
            "use_ams": True,
            "gcode_state": "RUNNING",
        }
    }).encode()
    pf.topic = "device/S01/report"
    client._on_message(None, None, pf)

    assert client.latest_project_file_payload == {
        "command": "project_file",
        "url": "file:///cache/m.3mf",
        "param": "Metadata/plate_1.gcode",
        "task_id": "T1",
        "subtask_id": "ST1",
        "ams_mapping": [0, 1],
        "use_ams": True,
        "gcode_state": "RUNNING",
    }
    # Aggregate is NOT polluted with the project_file one-shot fields.
    assert "url" not in client.latest_print_payload
    assert "param" not in client.latest_print_payload


def test_project_file_cache_survives_subsequent_push_status_deltas():
    client = _make_client()
    pf = MagicMock()
    pf.payload = json.dumps({
        "print": {
            "command": "project_file",
            "url": "file:///cache/m.3mf",
            "task_id": "T1",
        }
    }).encode()
    pf.topic = "device/S01/report"
    delta = MagicMock()
    delta.payload = json.dumps({
        "print": {"command": "push_status", "wifi_signal": "-65dBm"}
    }).encode()
    delta.topic = "device/S01/report"

    client._on_message(None, None, pf)
    client._on_message(None, None, delta)

    assert client.latest_project_file_payload is not None
    assert client.latest_project_file_payload["task_id"] == "T1"


def test_finish_clears_project_file_cache():
    client = _make_client()
    pf = MagicMock()
    pf.payload = json.dumps({
        "print": {
            "command": "project_file",
            "url": "file:///cache/m.3mf",
            "task_id": "T1",
            "gcode_state": "RUNNING",
        }
    }).encode()
    pf.topic = "device/S01/report"
    finish = MagicMock()
    finish.payload = json.dumps({
        "print": {"command": "push_status", "gcode_state": "FINISH"}
    }).encode()
    finish.topic = "device/S01/report"

    client._on_message(None, None, pf)
    assert client.latest_project_file_payload is not None
    client._on_message(None, None, finish)
    assert client.latest_project_file_payload is None


def test_failure_clears_project_file_cache():
    client = _make_client()
    pf = MagicMock()
    pf.payload = json.dumps({
        "print": {
            "command": "project_file",
            "url": "file:///cache/m.3mf",
            "task_id": "T1",
            "gcode_state": "RUNNING",
        }
    }).encode()
    pf.topic = "device/S01/report"
    failure = MagicMock()
    failure.payload = json.dumps({
        "print": {"command": "push_status", "gcode_state": "FAILURE"}
    }).encode()
    failure.topic = "device/S01/report"

    client._on_message(None, None, pf)
    client._on_message(None, None, failure)
    assert client.latest_project_file_payload is None


def test_aggregate_preserves_nested_ams_siblings():
    """An update to one nested block doesn't clobber a sibling block."""
    client = _make_client()
    first = MagicMock()
    first.payload = json.dumps({
        "print": {
            "command": "push_status",
            "ams": {"version": "1.0", "tray_now": "0"},
            "vt_tray": {"id": 254, "color": "red"},
        }
    }).encode()
    first.topic = "device/S01/report"
    second = MagicMock()
    second.payload = json.dumps({
        "print": {
            "command": "push_status",
            "vt_tray": {"color": "blue"},
        }
    }).encode()
    second.topic = "device/S01/report"

    client._on_message(None, None, first)
    client._on_message(None, None, second)

    agg = client.latest_print_payload
    # `ams` block untouched by the vt_tray-only update.
    assert agg["ams"] == {"version": "1.0", "tray_now": "0"}
    # `vt_tray.color` updated; `vt_tray.id` preserved.
    assert agg["vt_tray"] == {"id": 254, "color": "blue"}
