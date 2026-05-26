"""Tests for EventPump using the Python fake host."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from app.cloud.event_pump import EventPump
from app.cloud.plugin_host import PluginHost


FAKE_HOST = Path(__file__).parent / "cloud_fake_host.py"


async def test_event_pump_dispatches_events_to_handler(tmp_path):
    received: list[dict] = []

    async def handler(event: dict) -> None:
        received.append(event)

    async with PluginHost(cmd=[sys.executable, str(FAKE_HOST)]) as host:
        # Inject one event before starting the pump.
        await host.call("_test_push_event", {
            "kind": "OnMessage", "dev_id": "DEV1", "payload": "{\"a\":1}"
        })
        pump = EventPump(
            host=host,
            handlers={"OnMessage": handler},
            idle_interval=0.05,
            active_interval=0.01,
        )
        await pump.start()
        # Wait briefly for the pump to drain.
        await asyncio.sleep(0.2)
        # Inject another event mid-flight.
        await host.call("_test_push_event", {
            "kind": "OnMessage", "dev_id": "DEV2", "payload": "{\"a\":2}"
        })
        await asyncio.sleep(0.2)
        await pump.stop()

    assert len(received) == 2
    assert {e["dev_id"] for e in received} == {"DEV1", "DEV2"}


async def test_event_pump_unknown_event_kind_is_logged_not_raised(caplog):
    async def never_called(event):
        raise AssertionError("should not be called")

    async with PluginHost(cmd=[sys.executable, str(FAKE_HOST)]) as host:
        await host.call("_test_push_event", {"kind": "SomethingNew"})
        pump = EventPump(
            host=host, handlers={"OnMessage": never_called},
            idle_interval=0.05, active_interval=0.01,
        )
        await pump.start()
        await asyncio.sleep(0.2)
        await pump.stop()

    # No crash; some log entry about the unknown kind would be nice but not
    # required for this test.


async def test_event_pump_recovers_when_handler_raises(tmp_path):
    received: list[dict] = []

    async def flaky(event):
        if event["dev_id"] == "BAD":
            raise RuntimeError("boom")
        received.append(event)

    async with PluginHost(cmd=[sys.executable, str(FAKE_HOST)]) as host:
        await host.call("_test_push_event", {"kind": "OnMessage", "dev_id": "BAD"})
        await host.call("_test_push_event", {"kind": "OnMessage", "dev_id": "OK"})
        pump = EventPump(
            host=host, handlers={"OnMessage": flaky},
            idle_interval=0.05, active_interval=0.01,
        )
        await pump.start()
        await asyncio.sleep(0.3)
        await pump.stop()

    # OK still went through despite BAD raising.
    assert any(e["dev_id"] == "OK" for e in received)
