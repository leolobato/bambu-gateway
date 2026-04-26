from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.slice_jobs import (
    SliceJob,
    SliceJobManager,
    SliceJobStatus,
    SliceJobStore,
)


async def _seed_job(tmp_jobs_dir: Path, status: SliceJobStatus) -> SliceJob:
    """Write a single job with the given status to disk and return it."""
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    job = SliceJob.new(
        filename="x.3mf",
        machine_profile="GM014",
        process_profile="0.20mm",
        filament_profiles={},
        plate_id=1,
        plate_type="",
        project_filament_count=0,
        printer_id=None,
        auto_print=False,
        input_path=tmp_jobs_dir / "slice_jobs" / "x.input.3mf",
    )
    job.status = status
    Path(job.input_path).write_bytes(b"x")
    await store.upsert(job)
    return job


async def test_interrupted_slicing_marked_failed(tmp_jobs_dir: Path):
    job = await _seed_job(tmp_jobs_dir, SliceJobStatus.SLICING)

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    manager = SliceJobManager(
        store=store, slicer=MagicMock(), printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.recover_on_startup()
    recovered = await store.get(job.id)
    assert recovered.status == SliceJobStatus.FAILED
    assert "interrupted" in (recovered.error or "").lower()


async def test_interrupted_uploading_marked_failed(tmp_jobs_dir: Path):
    job = await _seed_job(tmp_jobs_dir, SliceJobStatus.UPLOADING)
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    manager = SliceJobManager(
        store=store, slicer=MagicMock(), printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.recover_on_startup()
    recovered = await store.get(job.id)
    assert recovered.status == SliceJobStatus.FAILED


async def test_queued_job_is_re_enqueued(tmp_jobs_dir: Path):
    import asyncio
    import base64

    job = await _seed_job(tmp_jobs_dir, SliceJobStatus.QUEUED)
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")

    slicer = MagicMock()

    async def stream(*a, **kw):
        yield {
            "event": "result",
            "data": {"file_base64": base64.b64encode(b"x").decode(), "file_size": 1},
        }
        yield {"event": "done", "data": {}}

    slicer.slice_stream = stream

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.recover_on_startup()
    await manager.start()
    try:
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            cur = await store.get(job.id)
            if cur and cur.status == SliceJobStatus.READY:
                break
            await asyncio.sleep(0.02)
        assert (await store.get(job.id)).status == SliceJobStatus.READY
    finally:
        await manager.stop()


async def test_terminal_jobs_left_alone(tmp_jobs_dir: Path):
    job = await _seed_job(tmp_jobs_dir, SliceJobStatus.READY)
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    manager = SliceJobManager(
        store=store, slicer=MagicMock(), printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.recover_on_startup()
    assert (await store.get(job.id)).status == SliceJobStatus.READY
