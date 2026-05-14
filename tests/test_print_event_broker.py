"""Tests for PrintEventBroker — per-consumer queue fan-out."""

from __future__ import annotations

import asyncio

import pytest

from app.print_event_broker import PrintEventBroker


@pytest.mark.asyncio
async def test_subscriber_receives_published_event():
    broker = PrintEventBroker()
    async with broker.subscribe() as queue:
        await broker.publish({"layer_num": 1})
        event = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert event == {"layer_num": 1}


@pytest.mark.asyncio
async def test_multiple_subscribers_each_receive_event():
    broker = PrintEventBroker()
    async with broker.subscribe() as q1, broker.subscribe() as q2:
        await broker.publish({"gcode_state": "RUNNING"})
        e1 = await asyncio.wait_for(q1.get(), timeout=0.5)
        e2 = await asyncio.wait_for(q2.get(), timeout=0.5)
    assert e1 == {"gcode_state": "RUNNING"}
    assert e2 == {"gcode_state": "RUNNING"}


@pytest.mark.asyncio
async def test_unsubscribe_drops_queue():
    broker = PrintEventBroker()
    async with broker.subscribe():
        pass
    # publishing after unsubscribe must not raise
    await broker.publish({"x": 1})
    assert broker.subscriber_count == 0


@pytest.mark.asyncio
async def test_full_queue_drops_event_not_blocks():
    broker = PrintEventBroker(max_queue_size=1)
    async with broker.subscribe() as queue:
        await broker.publish({"i": 1})
        await broker.publish({"i": 2})  # should drop, not block
        first = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert first == {"i": 1}
    assert queue.empty()


@pytest.mark.asyncio
async def test_publish_with_no_subscribers_is_noop():
    broker = PrintEventBroker()
    await broker.publish({"x": 1})  # must not raise
    assert broker.subscriber_count == 0
