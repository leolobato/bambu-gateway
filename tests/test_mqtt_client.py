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


def test_on_message_caches_latest_print_payload():
    client = _make_client()
    msg = MagicMock()
    msg.payload = json.dumps({"print": {"layer_num": 42, "gcode_state": "RUNNING"}}).encode()
    msg.topic = "device/S01/report"
    client._on_message(None, None, msg)
    assert client.latest_print_payload == {"layer_num": 42, "gcode_state": "RUNNING"}


def test_on_message_ignores_non_print_payload():
    client = _make_client()
    msg = MagicMock()
    msg.payload = json.dumps({"info": {"command": "get_version"}}).encode()
    msg.topic = "device/S01/report"
    client._on_message(None, None, msg)
    assert client.latest_print_payload is None


def test_on_message_overwrites_previous_payload():
    client = _make_client()
    first = MagicMock()
    first.payload = json.dumps({"print": {"layer_num": 1}}).encode()
    first.topic = "device/S01/report"
    second = MagicMock()
    second.payload = json.dumps({"print": {"layer_num": 2, "gcode_state": "RUNNING"}}).encode()
    second.topic = "device/S01/report"
    client._on_message(None, None, first)
    client._on_message(None, None, second)
    assert client.latest_print_payload == {"layer_num": 2, "gcode_state": "RUNNING"}


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
