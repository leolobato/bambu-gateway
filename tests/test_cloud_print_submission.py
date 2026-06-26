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
# appended to tests/test_cloud_print_submission.py


async def test_submit_print_yields_error_when_start_print_rc_nonzero():
    """A non-zero start_print rc (e.g. -1, agent not bootstrapped) must
    surface as an error frame instead of hanging forever on the queue."""
    client = CloudPrinterClient(dev_id="DEV3")
    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={"FAKE_HOST_START_PRINT_RC": "-1"},
    ) as host:
        frames = []
        async for frame in client.submit_print(
            host=host, print_params={"dev_id": "DEV3", "task_name": "z"},
        ):
            frames.append(frame)

    assert frames == [{
        "event": "error", "code": -1,
        "msg": frames[0]["msg"],
    }]
    assert "not ready" in frames[0]["msg"].lower() or "plugin" in frames[0]["msg"]
    # The client must be reusable afterwards.
    assert client._progress is None


async def test_submit_print_rejects_concurrent_submission_with_error_frame():
    """A second submission while one is in flight yields an in-flight error
    frame (not a raise), so the HTTP layer can return 409 instead of 500, and
    the running job's queue is left untouched."""
    from app.cloud.cloud_printer import PRINT_IN_FLIGHT_CODE

    client = CloudPrinterClient(dev_id="DEV6")
    inflight = asyncio.Queue()
    client._progress = inflight  # simulate a print already in flight

    frames = []
    async for frame in client.submit_print(
        print_params={"dev_id": "DEV6", "task_name": "t"},
    ):
        frames.append(frame)

    assert frames == [{
        "event": "error",
        "code": PRINT_IN_FLIGHT_CODE,
        "msg": frames[0]["msg"],
    }]
    assert "already" in frames[0]["msg"].lower()
    # The in-flight job's queue must not be cleared by the rejected attempt.
    assert client._progress is inflight


async def test_submit_print_times_out_when_no_events_arrive():
    """If the host accepts the job but no progress event ever arrives
    (host died, event dropped), the generator must yield an error frame
    after the inactivity timeout instead of blocking the request forever."""
    client = CloudPrinterClient(dev_id="DEV4")
    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={},  # rc=0 but no OnUpdateStatus events queued
    ) as host:
        frames = []
        async for frame in client.submit_print(
            host=host,
            print_params={"dev_id": "DEV4", "task_name": "t"},
            progress_timeout=0.1,
        ):
            frames.append(frame)

    assert frames[-1]["event"] == "error"
    assert "timed out" in frames[-1]["msg"].lower()
    # In-flight slot must be released so the next submission can proceed.
    assert client._progress is None


async def test_submit_print_uses_attached_host_when_host_arg_omitted():
    client = CloudPrinterClient(dev_id="DEV5")
    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={"FAKE_HOST_PRINT_SCRIPT": "happy"},
    ) as host:
        client.attach_host(host)

        async def feed():
            while True:
                result = await host.call("bridge.poll_events", {})
                for ev in result.get("events", []):
                    if ev.get("kind") == "OnUpdateStatus":
                        await client.handle_update_status(ev)
                await asyncio.sleep(0.02)

        feeder = asyncio.create_task(feed())
        frames = []
        async for frame in client.submit_print(
            print_params={"dev_id": "DEV5", "task_name": "h"},
        ):
            frames.append(frame)
            if frame.get("event") in ("done", "error"):
                break
        feeder.cancel()
        try:
            await feeder
        except asyncio.CancelledError:
            pass

    assert frames[-1]["event"] == "done"
