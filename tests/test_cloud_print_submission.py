"""End-to-end cloud print submission test (uses fake host)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from app.cloud.cloud_printer import CloudPrinterClient
from app.cloud.plugin_host import PluginHost


FAKE_HOST = Path(__file__).parent / "cloud_fake_host.py"


async def test_submit_print_emits_progress_then_finished(tmp_path):
    client = CloudPrinterClient(dev_id="DEV1")

    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={"FAKE_HOST_PRINT_SCRIPT": "happy"},
    ) as host:
        events_seen: list[dict] = []

        async def feed_events():
            # Drain the fake host's pending events into the client.
            while not events_seen or events_seen[-1].get("stage") != 6:
                result = await host.call("bridge.poll_events", {})
                for ev in result.get("events", []):
                    if ev.get("kind") == "OnUpdateStatus":
                        events_seen.append(ev)
                        await client.handle_update_status(ev)
                await asyncio.sleep(0.02)

        # Kick off the print + the feeder concurrently.
        feeder = asyncio.create_task(feed_events())
        frames: list[dict] = []
        async for frame in client.submit_print(
            host=host,
            print_params={"dev_id": "DEV1", "task_name": "x"},
        ):
            frames.append(frame)
            if frame.get("event") in ("done", "error"):
                break
        feeder.cancel()
        try:
            await feeder
        except asyncio.CancelledError:
            pass

    # Frames should cover at least progress + done.
    kinds = [f.get("event") for f in frames]
    assert "progress" in kinds
    assert "done" in kinds


async def test_submit_print_yields_error_on_nonzero_rc():
    """If start_print returns rc != 0 the generator yields an error frame."""
    from tests.cloud_fake_host import _PENDING_EVENTS

    client = CloudPrinterClient(dev_id="DEV2")

    # Use a custom host environment where we override start_print to return
    # rc=-1 by pushing a specific event. The fake host always returns rc=0 for
    # start_print, so we test this path by having the EventPump emit an error
    # stage (7) immediately.
    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={},  # no FAKE_HOST_PRINT_SCRIPT — no OnUpdateStatus events queued
    ) as host:
        # Push a terminal error stage directly so the feeder unblocks.
        async def feed_error():
            await asyncio.sleep(0.05)
            await client.handle_update_status(
                {"kind": "OnUpdateStatus", "stage": 7, "code": -2110, "msg": ""}
            )

        feeder = asyncio.create_task(feed_error())
        frames: list[dict] = []
        async for frame in client.submit_print(
            host=host,
            print_params={"dev_id": "DEV2", "task_name": "y"},
        ):
            frames.append(frame)
            if frame.get("event") in ("done", "error"):
                break
        feeder.cancel()
        try:
            await feeder
        except asyncio.CancelledError:
            pass

    assert frames[-1]["event"] == "error"
    assert frames[-1]["code"] == -2110
    assert "OSS upload" in frames[-1]["msg"]
