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


async def test_ready_job_thumbnail_backfilled_from_output(tmp_jobs_dir: Path):
    """Old READY jobs sliced before the auxiliary fallback had `thumbnail=None`
    persisted. On startup we re-extract from their output blob."""
    import io
    import zipfile

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    out_path = tmp_jobs_dir / "slice_jobs" / "y.output.3mf"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Metadata/plate_1.gcode", b"; gcode")
        zf.writestr(
            "Auxiliaries/.thumbnails/thumbnail_middle.png",
            b"\x89PNG\r\n\x1a\n_middle",
        )
    out_path.write_bytes(buf.getvalue())

    job = SliceJob.new(
        filename="y.3mf",
        machine_profile="GM014",
        process_profile="0.20mm",
        filament_profiles={},
        plate_id=1,
        plate_type="",
        project_filament_count=0,
        printer_id=None,
        auto_print=False,
        input_path=tmp_jobs_dir / "slice_jobs" / "y.input.3mf",
    )
    job.status = SliceJobStatus.READY
    job.output_path = str(out_path)
    job.output_size = out_path.stat().st_size
    job.thumbnail = None
    Path(job.input_path).write_bytes(b"x")
    await store.upsert(job)

    manager = SliceJobManager(
        store=store, slicer=MagicMock(), printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.recover_on_startup()

    recovered = await store.get(job.id)
    assert recovered.thumbnail is not None
    assert recovered.thumbnail.startswith("data:image/png;base64,")


async def test_ready_job_without_output_skipped_by_backfill(tmp_jobs_dir: Path):
    """READY jobs without an output_path (legacy fixture) should not crash backfill."""
    job = await _seed_job(tmp_jobs_dir, SliceJobStatus.READY)
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    manager = SliceJobManager(
        store=store, slicer=MagicMock(), printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.recover_on_startup()
    recovered = await store.get(job.id)
    assert recovered.thumbnail is None
    assert recovered.status == SliceJobStatus.READY
