import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.slice_jobs import (
    SliceJob,
    SliceJobManager,
    SliceJobStatus,
    SliceJobStore,
)


def make_slicer(events: list[dict]):
    """Build a SlicerClient mock whose slice_stream yields the given events."""
    client = MagicMock()

    async def stream(*args, **kwargs):
        for e in events:
            yield e

    client.slice_stream = stream
    return client


async def _wait_for_status(
    store: SliceJobStore, job_id: str, target: SliceJobStatus, timeout: float = 2.0,
):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        job = await store.get(job_id)
        if job and job.status == target:
            return job
        await asyncio.sleep(0.02)
    pytest.fail(f"job {job_id} never reached {target} (last={job.status if job else None})")


async def test_submit_slice_succeeds_and_writes_output_blob(tmp_jobs_dir: Path):
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        {"event": "progress", "data": {"percent": 25}},
        {"event": "progress", "data": {"percent": 80}},
        {
            "event": "result",
            "data": {
                "file_base64": base64.b64encode(b"sliced!").decode(),
                "file_size": 7,
                "estimate": {"total_time_seconds": 1234},
            },
        },
        {"event": "done", "data": {}},
    ])
    manager = SliceJobManager(
        store=store,
        slicer=slicer,
        printer_service=MagicMock(),
        notifier=None,
        max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"original-3mf-bytes",
            filename="cube.3mf",
            machine_profile="GM014",
            process_profile="0.20mm",
            filament_profiles={"0": "GFL99"},
            plate_id=1,
            plate_type="",
            project_filament_count=1,
            printer_id=None,
            auto_print=False,
        )
        assert job.status == SliceJobStatus.QUEUED

        ready = await _wait_for_status(store, job.id, SliceJobStatus.READY)
        assert ready.progress == 100
        assert ready.output_path is not None
        assert Path(ready.output_path).read_bytes() == b"sliced!"
        assert ready.estimate == {"total_time_seconds": 1234}
        assert ready.error is None
    finally:
        await manager.stop()


async def test_progress_events_update_job_progress(tmp_jobs_dir: Path):
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    seen_progress: list[int] = []

    # Slow slicer so we can observe intermediate state.
    async def stream(*a, **kw):
        yield {"event": "progress", "data": {"percent": 33}}
        await asyncio.sleep(0.05)
        yield {"event": "progress", "data": {"percent": 66}}
        await asyncio.sleep(0.05)
        yield {
            "event": "result",
            "data": {
                "file_base64": base64.b64encode(b"x").decode(),
                "file_size": 1,
            },
        }
        yield {"event": "done", "data": {}}

    slicer = MagicMock()
    slicer.slice_stream = stream
    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="cube.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id=None, auto_print=False,
        )
        # Sample progress periodically until terminal
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            cur = await store.get(job.id)
            if cur and cur.progress not in seen_progress:
                seen_progress.append(cur.progress)
            if cur and cur.status.is_terminal:
                break
            await asyncio.sleep(0.01)
        assert any(p in seen_progress for p in (33, 66))
        assert seen_progress[-1] == 100
    finally:
        await manager.stop()


async def test_max_concurrent_limits_parallel_slices(tmp_jobs_dir: Path):
    """With max_concurrent=2, the 3rd job must wait until one of the first two finishes."""
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    in_flight = 0
    peak = 0
    gate = asyncio.Event()

    async def stream(*a, **kw):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await gate.wait()
            yield {
                "event": "result",
                "data": {"file_base64": base64.b64encode(b"x").decode(), "file_size": 1},
            }
            yield {"event": "done", "data": {}}
        finally:
            in_flight -= 1

    slicer = MagicMock()
    slicer.slice_stream = stream

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=2,
    )
    await manager.start()
    try:
        ids = []
        for _ in range(3):
            job = await manager.submit(
                file_data=b"x", filename="c.3mf",
                machine_profile="GM014", process_profile="0.20mm",
                filament_profiles={}, plate_id=1, plate_type="",
                project_filament_count=0, printer_id=None, auto_print=False,
            )
            ids.append(job.id)

        # Give workers a chance to pick up jobs
        await asyncio.sleep(0.1)
        assert peak == 2  # never exceeded the limit
        gate.set()

        for jid in ids:
            await _wait_for_status(store, jid, SliceJobStatus.READY)
    finally:
        gate.set()
        await manager.stop()


async def test_cancel_queued_job_skips_slicer(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")

    # First job blocks the worker behind a gate; second job sits queued.
    gate = asyncio.Event()

    async def gated_stream(*a, **kw):
        await gate.wait()
        yield {"event": "done", "data": {}}

    slicer = MagicMock()
    slicer.slice_stream = gated_stream

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        first = await manager.submit(
            file_data=b"x", filename="a.3mf", machine_profile="GM014",
            process_profile="0.20mm", filament_profiles={}, plate_id=1,
            plate_type="", project_filament_count=0, printer_id=None,
            auto_print=False,
        )
        second = await manager.submit(
            file_data=b"x", filename="b.3mf", machine_profile="GM014",
            process_profile="0.20mm", filament_profiles={}, plate_id=1,
            plate_type="", project_filament_count=0, printer_id=None,
            auto_print=False,
        )
        await asyncio.sleep(0.05)
        assert await manager.cancel(second.id) is True
        gate.set()
        cancelled = await _wait_for_status(store, second.id, SliceJobStatus.CANCELLED)
        assert cancelled.error is None
        # First job still completes naturally
        await _wait_for_status(store, first.id, SliceJobStatus.FAILED)
    finally:
        gate.set()
        await manager.stop()


async def test_cancel_terminal_job_returns_false(tmp_jobs_dir: Path):
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        {"event": "result",
         "data": {"file_base64": base64.b64encode(b"x").decode(), "file_size": 1}},
        {"event": "done", "data": {}},
    ])
    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="c.3mf", machine_profile="GM014",
            process_profile="0.20mm", filament_profiles={}, plate_id=1,
            plate_type="", project_filament_count=0, printer_id=None,
            auto_print=False,
        )
        await _wait_for_status(store, job.id, SliceJobStatus.READY)
        assert await manager.cancel(job.id) is False
    finally:
        await manager.stop()


async def test_cancel_during_slicing_aborts(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    started = asyncio.Event()
    finished_naturally = False

    async def slow_stream(*a, **kw):
        nonlocal finished_naturally
        started.set()
        try:
            await asyncio.sleep(5)  # would block past the test timeout
            yield {"event": "result", "data": {"file_base64": "", "file_size": 0}}
            yield {"event": "done", "data": {}}
            finished_naturally = True
        except asyncio.CancelledError:
            raise

    slicer = MagicMock()
    slicer.slice_stream = slow_stream

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="a.3mf", machine_profile="GM014",
            process_profile="0.20mm", filament_profiles={}, plate_id=1,
            plate_type="", project_filament_count=0, printer_id=None,
            auto_print=False,
        )
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await manager.cancel(job.id)
        cancelled = await _wait_for_status(
            store, job.id, SliceJobStatus.CANCELLED, timeout=2.0,
        )
        assert cancelled.output_path is None
        assert not finished_naturally
    finally:
        await manager.stop()


async def test_auto_print_uploads_when_printer_idle(tmp_jobs_dir: Path):
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        {"event": "result",
         "data": {"file_base64": base64.b64encode(b"sliced").decode(),
                  "file_size": 6}},
        {"event": "done", "data": {}},
    ])

    submit_calls = []
    printer_service = MagicMock()

    def fake_submit(printer_id, file_data, filename, **kwargs):
        submit_calls.append((printer_id, filename, len(file_data)))
        cb = kwargs.get("progress_callback")
        if cb:
            cb(len(file_data))

    printer_service.submit_print = fake_submit

    status = MagicMock()
    status.gcode_state = "IDLE"
    status.online = True
    printer_service.get_status = MagicMock(return_value=status)

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=printer_service,
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="cube.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id="PRINTER1",
            auto_print=True,
        )
        terminal = await _wait_for_status(store, job.id, SliceJobStatus.PRINTING)
        assert submit_calls == [("PRINTER1", "cube.3mf", 6)]
        assert terminal.error is None
    finally:
        await manager.stop()
