"""Cloud submission must work from EVERY print entry point.

Regression tests: the original integration only wired the cloud transport
into the fresh-upload path of /api/print and an inline SSE block — the
preview/job reprint path and the slice-job manager's auto-print stayed
LAN-only and failed for cloud printers.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.cloud.cloud_printer import CloudPrinterClient
from app.cloud.plugin_host import PluginHost
from app.slice_jobs import SliceJobManager, SliceJobStatus, SliceJobStore

FAKE_HOST = Path(__file__).parent / "cloud_fake_host.py"


def _make_slicer(events: list[dict]):
    client = MagicMock()

    async def stream(*args, **kwargs):
        for e in events:
            yield e

    client.slice_stream = stream
    return client


async def _wait_for_status(store, job_id, target, timeout: float = 3.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        job = await store.get(job_id)
        if job and job.status == target:
            return job
        await asyncio.sleep(0.02)
    pytest.fail(
        f"job {job_id} never reached {target} "
        f"(last={job.status if job else None}, error={job.error if job else None})"
    )


class _Feeder:
    """Pumps the fake host's OnUpdateStatus events into a cloud client."""

    def __init__(self, host, client):
        self._task = asyncio.create_task(self._run(host, client))

    async def _run(self, host, client):
        while True:
            result = await host.call("bridge.poll_events", {})
            for ev in result.get("events", []):
                if ev.get("kind") == "OnUpdateStatus":
                    await client.handle_update_status(ev)
            await asyncio.sleep(0.02)

    async def stop(self):
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass


async def test_slice_job_auto_print_uses_cloud_transport(tmp_jobs_dir: Path, tmp_path):
    """POST /api/slice-jobs auto_print=true for a cloud printer must submit
    through the plugin and record job.printed — previously it tried LAN FTPS
    and the print was lost."""
    record = tmp_path / "rpc.jsonl"
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = _make_slicer([
        {"event": "result",
         "data": {"file_base64": base64.b64encode(b"sliced").decode(),
                  "file_size": 6}},
        {"event": "done", "data": {}},
    ])

    notifications: list[str] = []

    async def notifier(job, kind):
        notifications.append(kind)

    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={
            "FAKE_HOST_RECORD_FILE": str(record),
            "FAKE_HOST_PRINT_SCRIPT": "happy",
        },
    ) as host:
        cloud_client = CloudPrinterClient(dev_id="CLOUD1", host=host)
        feeder = _Feeder(host, cloud_client)

        printer_service = MagicMock()
        printer_service.get_cloud_client = (
            lambda pid: cloud_client if pid == "CLOUD1" else None
        )
        status = MagicMock()
        status.gcode_state = "IDLE"
        status.online = True
        printer_service.get_status = MagicMock(return_value=status)
        # The LAN submit path must never be touched.
        printer_service.submit_print = MagicMock(
            side_effect=AssertionError("LAN submit_print called for cloud printer")
        )

        manager = SliceJobManager(
            store=store, slicer=slicer, printer_service=printer_service,
            notifier=notifier, max_concurrent=1,
        )
        await manager.start()
        try:
            job = await manager.submit(
                file_data=b"x", filename="cube.3mf",
                machine_profile="GM014", process_profile="0.20mm",
                filament_profiles={}, plate_id=2, plate_type="",
                project_filament_count=0, printer_id="CLOUD1",
                auto_print=True,
            )
            terminal = await _wait_for_status(store, job.id, SliceJobStatus.READY)
        finally:
            await manager.stop()
            await feeder.stop()

    assert terminal.printed is True
    assert terminal.error is None
    assert "printing" in notifications

    reqs = [json.loads(line) for line in record.read_text().splitlines()]
    sp = next(r for r in reqs if r["method"] == "start_print")["params"]
    assert sp["dev_id"] == "CLOUD1"
    assert sp["plate_index"] == 2
    # The plugin uploads the job's output blob straight from disk.
    assert sp["filename"] == str(terminal.output_path)


async def test_print_from_job_routes_to_cloud(tmp_jobs_dir: Path, tmp_path, monkeypatch):
    """POST /api/print with job_id for a cloud printer must use the plugin,
    not LAN ensure_connected + FTPS (which 409'd for cloud-only printers)."""
    import httpx
    import app.main as main_mod
    from app.slice_jobs import SliceJob

    record = tmp_path / "rpc.jsonl"
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")

    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={
            "FAKE_HOST_RECORD_FILE": str(record),
            "FAKE_HOST_PRINT_SCRIPT": "happy",
        },
    ) as host:
        cloud_client = CloudPrinterClient(dev_id="CLOUD1", host=host)
        feeder = _Feeder(host, cloud_client)

        # A ready job with sliced output on disk.
        out_path = tmp_jobs_dir / "slice_jobs" / "j1.3mf"
        out_path.write_bytes(b"sliced-bytes")
        job = SliceJob(
            id="j1", filename="cube.3mf", status=SliceJobStatus.READY,
            printer_id="CLOUD1", plate_id=1, output_path=str(out_path),
            created_at="2026-06-10T00:00:00Z", updated_at="2026-06-10T00:00:00Z",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_type="", project_filament_count=0,
            auto_print=False, input_path="",
        )
        await store.upsert(job)

        manager = SliceJobManager(
            store=store, slicer=MagicMock(),
            printer_service=MagicMock(), notifier=None,
        )

        monkeypatch.setattr(main_mod.settings, "bambu_cloud_enabled", True)
        monkeypatch.setattr(main_mod, "slice_jobs", manager)
        svc = MagicMock()
        svc.default_printer_id.return_value = "CLOUD1"
        svc.get_client.return_value = None  # no LAN client for cloud printers
        monkeypatch.setattr(main_mod, "printer_service", svc)
        main_mod.app.state.cloud_printers = {"CLOUD1": cloud_client}

        async def _no_tray_error(*a, **kw):
            return None
        monkeypatch.setattr(main_mod, "validate_selected_trays", _no_tray_error)

        transport = httpx.ASGITransport(app=main_mod.app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as http:
                resp = await http.post("/api/print", data={"job_id": "j1"})
        finally:
            await feeder.stop()

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "printing"

    reqs = [json.loads(line) for line in record.read_text().splitlines()]
    sp = next(r for r in reqs if r["method"] == "start_print")["params"]
    assert sp["dev_id"] == "CLOUD1"
    assert sp["filename"] == str(out_path)

    updated = await store.get("j1")
    assert updated.printed is True
