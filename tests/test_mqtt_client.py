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
