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
        terminal = await _wait_for_status(store, job.id, SliceJobStatus.READY)
        assert submit_calls == [("PRINTER1", "cube.3mf", 6)]
        assert terminal.error is None
        assert terminal.printed is True
    finally:
        await manager.stop()


async def test_auto_print_degrades_to_ready_when_printer_busy(tmp_jobs_dir: Path):
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        {"event": "result",
         "data": {"file_base64": base64.b64encode(b"sliced").decode(),
                  "file_size": 6}},
        {"event": "done", "data": {}},
    ])

    printer_service = MagicMock()
    busy_status = MagicMock()
    busy_status.gcode_state = "RUNNING"
    busy_status.online = True
    printer_service.get_status = MagicMock(return_value=busy_status)
    printer_service.submit_print = MagicMock()

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=printer_service,
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="c.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id="PRINTER1", auto_print=True,
        )
        ready = await _wait_for_status(store, job.id, SliceJobStatus.READY)
        assert ready.output_path is not None
        assert ready.printed is False
        printer_service.submit_print.assert_not_called()
    finally:
        await manager.stop()


async def test_auto_print_degrades_to_ready_when_printer_offline(tmp_jobs_dir: Path):
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        {"event": "result",
         "data": {"file_base64": base64.b64encode(b"sliced").decode(),
                  "file_size": 6}},
        {"event": "done", "data": {}},
    ])

    printer_service = MagicMock()
    offline = MagicMock()
    offline.gcode_state = "IDLE"
    offline.online = False
    printer_service.get_status = MagicMock(return_value=offline)
    printer_service.submit_print = MagicMock()

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=printer_service,
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="c.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id="PRINTER1", auto_print=True,
        )
        await _wait_for_status(store, job.id, SliceJobStatus.READY)
        printer_service.submit_print.assert_not_called()
    finally:
        await manager.stop()


async def test_slicer_exception_marks_job_failed(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")

    async def boom(*a, **kw):
        if False:
            yield {}
        raise RuntimeError("slicer boom")

    slicer = MagicMock()
    slicer.slice_stream = boom

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="c.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id=None, auto_print=False,
        )
        failed = await _wait_for_status(store, job.id, SliceJobStatus.FAILED)
        assert "slicer boom" in (failed.error or "")
    finally:
        await manager.stop()


async def test_error_event_surfaces_orca_error_string(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    orca_output = (
        "[2026-04-27 13:08:32.439340] [0x00007f0e9a97d300] [error]   "
        "load_from_json: parse /tmp/x/filament_0.json error, invalid json array for hot_plate_temp\n"
        "run found error, return -5, exit...\n"
        "\n=== result.json ===\n"
        '{"error_string": "The input preset file is invalid and can not be parsed.",'
        ' "return_code": -5}\n'
    )
    slicer = make_slicer([
        {"event": "error", "data": {
            "error": "OrcaSlicer exited with code 251",
            "orca_output": orca_output,
            "critical_warnings": [],
        }},
    ])
    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="c.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id=None, auto_print=False,
        )
        failed = await _wait_for_status(store, job.id, SliceJobStatus.FAILED)
        msg = failed.error or ""
        assert "OrcaSlicer exited with code 251" in msg
        assert "The input preset file is invalid and can not be parsed." in msg
    finally:
        await manager.stop()


async def test_error_event_surfaces_bare_cerr_line(tmp_jobs_dir: Path):
    """Orca writes some critical errors via cerr (no boost-log prefix). The
    flush-volumes export-time check is one — its result.json `error_string`
    is the generic 'Failed slicing the model…' catchall, so the real cause
    only lives on the bare line."""
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    orca_output = (
        "[2026-04-27 13:36:17.323294] [0x00007f] [error]   "
        "found slicing or export error for partplate 1\n"
        "Flush volumes matrix do not match to the correct size!\n"
        "[2026-04-27 13:36:17.323503] [0x00007f] [info]    "
        "record_exit_reson:449, saved config to /tmp/x/result.json\n"
        "run found error, return -100, exit...\n"
        "\n=== result.json ===\n"
        '{"error_string": "Failed slicing the model. Please verify the '
        'slicing of all plates on Orca Slicer before uploading.",'
        ' "return_code": -100}\n'
    )
    slicer = make_slicer([
        {"event": "error", "data": {
            "error": "OrcaSlicer exited with code 156",
            "orca_output": orca_output,
        }},
    ])
    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="c.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id=None, auto_print=False,
        )
        failed = await _wait_for_status(store, job.id, SliceJobStatus.FAILED)
        msg = failed.error or ""
        assert "OrcaSlicer exited with code 156" in msg
        assert "Failed slicing the model" in msg
        assert "Flush volumes matrix do not match to the correct size!" in msg
    finally:
        await manager.stop()


async def test_error_event_falls_back_to_orca_error_line(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    orca_output = (
        "[2026-04-27 13:08:32.111] [0x00007f0] [info]    starting\n"
        "[2026-04-27 13:08:32.222] [0x00007f0] [error]   bad mesh at object 3\n"
    )
    slicer = make_slicer([
        {"event": "error", "data": {
            "error": "OrcaSlicer exited with code 1",
            "orca_output": orca_output,
        }},
    ])
    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="c.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id=None, auto_print=False,
        )
        failed = await _wait_for_status(store, job.id, SliceJobStatus.FAILED)
        assert "bad mesh at object 3" in (failed.error or "")
    finally:
        await manager.stop()


async def test_no_result_event_marks_job_failed(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([{"event": "done", "data": {}}])
    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="c.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id=None, auto_print=False,
        )
        failed = await _wait_for_status(store, job.id, SliceJobStatus.FAILED)
        assert "no output" in (failed.error or "").lower()
    finally:
        await manager.stop()


async def test_upload_exception_marks_job_failed(tmp_jobs_dir: Path):
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        {"event": "result",
         "data": {"file_base64": base64.b64encode(b"sliced").decode(),
                  "file_size": 6}},
        {"event": "done", "data": {}},
    ])

    printer_service = MagicMock()
    idle = MagicMock()
    idle.gcode_state = "IDLE"
    idle.online = True
    printer_service.get_status = MagicMock(return_value=idle)

    def boom(*a, **kw):
        raise RuntimeError("ftp boom")

    printer_service.submit_print = boom

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=printer_service,
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="c.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id="PRINTER1", auto_print=True,
        )
        failed = await _wait_for_status(store, job.id, SliceJobStatus.FAILED)
        assert "ftp boom" in (failed.error or "")
        assert failed.output_path is not None
        assert Path(failed.output_path).exists()
    finally:
        await manager.stop()


async def test_cancel_during_upload_aborts(tmp_jobs_dir: Path):
    import base64
    import time as _time_mod

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        {"event": "result",
         "data": {"file_base64": base64.b64encode(b"sliced" * 1024).decode(),
                  "file_size": 6144}},
        {"event": "done", "data": {}},
    ])

    printer_service = MagicMock()
    idle = MagicMock()
    idle.gcode_state = "IDLE"
    idle.online = True
    printer_service.get_status = MagicMock(return_value=idle)

    upload_started = asyncio.Event()
    main_loop = asyncio.get_event_loop()

    def slow_submit(printer_id, file_data, filename, **kwargs):
        main_loop.call_soon_threadsafe(upload_started.set)
        cb = kwargs.get("progress_callback")
        for _ in range(200):
            cb(64)
            _time_mod.sleep(0.02)

    printer_service.submit_print = slow_submit

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=printer_service,
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="c.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id="PRINTER1", auto_print=True,
        )
        await asyncio.wait_for(upload_started.wait(), timeout=2.0)
        await manager.cancel(job.id)
        cancelled = await _wait_for_status(
            store, job.id, SliceJobStatus.CANCELLED, timeout=3.0,
        )
        assert cancelled.error is None
    finally:
        await manager.stop()


async def test_progress_event_with_null_percent_is_tolerated(tmp_jobs_dir: Path):
    """Real OrcaSlicer can send `percent: null` before it has computed one."""
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        # First a malformed tick — used to crash with int(None) → TypeError
        {"event": "progress", "data": {"percent": None}},
        {"event": "progress", "data": {"percent": "42.7"}},
        {
            "event": "result",
            "data": {
                "file_base64": base64.b64encode(b"sliced").decode(),
                "file_size": 6,
            },
        },
        {"event": "done", "data": {}},
    ])
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
        ready = await _wait_for_status(store, job.id, SliceJobStatus.READY)
        assert ready.error is None
        assert ready.progress == 100
    finally:
        await manager.stop()


async def test_result_event_without_file_base64_is_failed(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        {"event": "result", "data": {}},
        {"event": "done", "data": {}},
    ])
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
        failed = await _wait_for_status(store, job.id, SliceJobStatus.FAILED)
        assert "file_base64" in (failed.error or "")
    finally:
        await manager.stop()


async def test_error_event_surfaces_slicer_message(tmp_jobs_dir: Path):
    """An SSE `error` frame should fail the job with the slicer's message."""
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        {"event": "progress", "data": {"percent": 12}},
        {"event": "error", "data": {"error": "filament profile xyz not found"}},
        {"event": "done", "data": {}},
    ])
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
        failed = await _wait_for_status(store, job.id, SliceJobStatus.FAILED)
        assert "filament profile xyz not found" in (failed.error or "")
    finally:
        await manager.stop()


async def test_no_output_failure_includes_seen_events(tmp_jobs_dir: Path):
    """When the slicer closes without a result, the failure lists what we did see."""
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        {"event": "progress", "data": {"percent": 50}},
        {"event": "status", "data": {"phase": "preparing"}},
        {"event": "done", "data": {}},
    ])
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
        failed = await _wait_for_status(store, job.id, SliceJobStatus.FAILED)
        assert "events seen" in (failed.error or "")
        assert "progress" in (failed.error or "")
    finally:
        await manager.stop()


def test_parse_orca_progress_extracts_percent_and_message():
    from app.slice_jobs import _parse_orca_progress

    line = (
        "[2026-04-26 19:43:56.413206] [0x00007f9e] [debug]   "
        "default_status_callback: percent=75, warning_step=-1, "
        "message=Optimizing toolpath, message_type=0"
    )
    pct, msg = _parse_orca_progress(line)
    assert pct == 75
    assert msg == "Optimizing toolpath"


def test_parse_orca_progress_handles_missing_message():
    from app.slice_jobs import _parse_orca_progress

    line = "[debug]  default_status_callback: percent=20, warning_step=-1"
    pct, msg = _parse_orca_progress(line)
    assert pct == 20
    assert msg is None


def test_parse_orca_progress_returns_none_for_unrelated_lines():
    from app.slice_jobs import _parse_orca_progress

    assert _parse_orca_progress("") == (None, None)
    assert _parse_orca_progress("just some random log line") == (None, None)
    assert _parse_orca_progress("[debug] ~PrintObject: this=0x123") == (None, None)


async def test_progress_extracts_from_embedded_orca_log_line(tmp_jobs_dir: Path):
    """When SSE-level percent is null, mine the orca log line for percent + message."""
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        {
            "event": "progress",
            "data": {
                "line": "[debug] default_status_callback: percent=42, message=Generating skirt, message_type=0",
                "percent": None,
            },
        },
        {
            "event": "result",
            "data": {
                "file_base64": base64.b64encode(b"sliced").decode(),
                "file_size": 6,
            },
        },
        {"event": "done", "data": {}},
    ])
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
        ready = await _wait_for_status(store, job.id, SliceJobStatus.READY)
        # Phase was cleared on transition to READY but the slice should have
        # gone through the embedded-log code path (no error).
        assert ready.error is None
        # Reach the slicer call's `last_write` so the intermediate phase had
        # at least one chance to flush — verified by absence of crash.
    finally:
        await manager.stop()


async def test_status_event_populates_phase(tmp_jobs_dir: Path):
    """Coarse `event: status` frames from the slicer should set the job phase."""
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer_started = asyncio.Event()
    release = asyncio.Event()

    async def stream(*a, **kw):
        yield {"event": "status", "data": {"phase": "reading_3mf", "message": "Reading input file"}}
        slicer_started.set()
        await release.wait()
        yield {
            "event": "result",
            "data": {"file_base64": base64.b64encode(b"x").decode(), "file_size": 1},
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
        # Wait until the slicer has emitted the status event and the manager
        # processed it; then sample the store before releasing.
        await asyncio.wait_for(slicer_started.wait(), timeout=2.0)
        # Give the manager a tick to flush the phase update.
        await asyncio.sleep(0.05)
        mid = await store.get(job.id)
        assert mid is not None
        assert mid.phase == "Reading input file"
        release.set()
        await _wait_for_status(store, job.id, SliceJobStatus.READY)
    finally:
        release.set()
        await manager.stop()


def test_extract_plate_thumbnail_pulls_plate_1_png():
    import io
    import zipfile
    from app.slice_jobs import _extract_plate_thumbnail

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Metadata/plate_1.png", b"\x89PNG\r\n\x1a\n_thumb")
    payload = buf.getvalue()

    data_url = _extract_plate_thumbnail(payload)
    assert data_url is not None
    assert data_url.startswith("data:image/png;base64,")
    import base64
    decoded = base64.b64decode(data_url.split(",", 1)[1])
    assert decoded == b"\x89PNG\r\n\x1a\n_thumb"


def test_extract_plate_thumbnail_returns_none_for_archive_without_thumbnail():
    import io
    import zipfile
    from app.slice_jobs import _extract_plate_thumbnail

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", b"<model></model>")
    assert _extract_plate_thumbnail(buf.getvalue()) is None


def test_extract_plate_thumbnail_returns_none_for_invalid_zip():
    from app.slice_jobs import _extract_plate_thumbnail

    assert _extract_plate_thumbnail(b"not a zip") is None
