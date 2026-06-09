"""Tests for the shared cloud print submission helper."""
from __future__ import annotations

import asyncio
import json
import sys
from contextlib import aclosing
from pathlib import Path

from app.cloud.cloud_printer import CloudPrinterClient
from app.cloud.plugin_host import PluginHost
from app.cloud.submit import submit_cloud_print

FAKE_HOST = Path(__file__).parent / "cloud_fake_host.py"


def _start_print_params(record_file: Path) -> dict:
    reqs = [json.loads(line) for line in record_file.read_text().splitlines()]
    return next(r for r in reqs if r["method"] == "start_print")["params"]


async def _drain(client, host):
    """Feed OnUpdateStatus events from the fake host into the client."""
    while True:
        result = await host.call("bridge.poll_events", {})
        for ev in result.get("events", []):
            if ev.get("kind") == "OnUpdateStatus":
                await client.handle_update_status(ev)
        await asyncio.sleep(0.02)


async def _run_submit(host, client, frames_out: list, **kwargs):
    feeder = asyncio.create_task(_drain(client, host))
    try:
        async with aclosing(submit_cloud_print(cloud_client=client, **kwargs)) as it:
            async for frame in it:
                frames_out.append(frame)
                if frame.get("event") in ("done", "error"):
                    break
    finally:
        feeder.cancel()
        try:
            await feeder
        except asyncio.CancelledError:
            pass


async def test_submit_sends_ams_mapping_as_int_array(tmp_path):
    """The plugin expects the JSON int array OrcaSlicer sends — the old
    dict-of-strings shape made the printer pull from the wrong trays."""
    record = tmp_path / "rpc.jsonl"
    sliced = tmp_path / "job.3mf"
    sliced.write_bytes(b"3mf-bytes")
    client = CloudPrinterClient(dev_id="DEV1")

    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={
            "FAKE_HOST_RECORD_FILE": str(record),
            "FAKE_HOST_PRINT_SCRIPT": "happy",
        },
    ) as host:
        client.attach_host(host)
        frames: list = []
        await _run_submit(
            host, client, frames,
            file_path=sliced,
            filename="benchy.3mf",
            plate_index=1,
            ams_mapping=[1, 0],
            use_ams=True,
        )

    assert frames[-1]["event"] == "done"
    params = _start_print_params(record)
    assert json.loads(params["ams_mapping"]) == [1, 0]
    assert params["dev_id"] == "DEV1"
    assert params["project_name"] == "benchy"
    assert params["plate_index"] == 1


async def test_submit_with_file_path_passes_path_without_copying(tmp_path):
    """A sliced 3MF already on disk is handed to the plugin by path — no
    bytes round-trip through memory, no temp copy."""
    record = tmp_path / "rpc.jsonl"
    sliced = tmp_path / "already_sliced.3mf"
    sliced.write_bytes(b"x" * 64)
    client = CloudPrinterClient(dev_id="DEV1")

    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={
            "FAKE_HOST_RECORD_FILE": str(record),
            "FAKE_HOST_PRINT_SCRIPT": "happy",
        },
    ) as host:
        client.attach_host(host)
        frames: list = []
        await _run_submit(
            host, client, frames,
            file_path=sliced,
            filename="already_sliced.3mf",
            plate_index=1,
            ams_mapping=None,
            use_ams=False,
        )

    assert _start_print_params(record)["filename"] == str(sliced)
    assert sliced.exists()


async def test_submit_with_bytes_writes_temp_file_and_cleans_up(tmp_path):
    record = tmp_path / "rpc.jsonl"
    client = CloudPrinterClient(dev_id="DEV1")

    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={
            "FAKE_HOST_RECORD_FILE": str(record),
            "FAKE_HOST_PRINT_SCRIPT": "happy",
        },
    ) as host:
        client.attach_host(host)
        frames: list = []
        await _run_submit(
            host, client, frames,
            file_data=b"uploaded-bytes",
            filename="upload.3mf",
            plate_index=2,
            ams_mapping=None,
            use_ams=False,
        )

    assert frames[-1]["event"] == "done"
    sent_path = Path(_start_print_params(record)["filename"])
    # The temp file existed for the upload and is gone afterwards.
    assert not sent_path.exists()


async def test_breaking_out_releases_the_in_flight_slot(tmp_path):
    """Consumers break out of the frame stream on done/error; with aclosing
    the client's in-flight slot must be released immediately so the next
    submission doesn't 500 with 'already in flight'."""
    sliced = tmp_path / "job.3mf"
    sliced.write_bytes(b"3mf")
    client = CloudPrinterClient(dev_id="DEV1")

    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={"FAKE_HOST_PRINT_SCRIPT": "happy"},
    ) as host:
        client.attach_host(host)
        for _ in range(2):  # second submission must not raise
            frames: list = []
            await _run_submit(
                host, client, frames,
                file_path=sliced,
                filename="job.3mf",
                plate_index=1,
                ams_mapping=None,
                use_ams=False,
            )
            assert frames[-1]["event"] == "done"
            assert client._progress is None
