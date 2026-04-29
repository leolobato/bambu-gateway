import asyncio
import base64
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest


@pytest.fixture
async def app_client(tmp_path: Path, monkeypatch):
    """Boot the FastAPI app with stubbed printer service and slicer."""
    from app import config_store
    from app.config import settings
    import app.main as main_mod

    config_store.set_path(tmp_path / "printers.json")
    monkeypatch.setattr(settings, "orcaslicer_api_url", "http://stub")
    monkeypatch.setattr(main_mod, "parse_3mf", lambda data: MagicMock(filaments=[]))

    async def _fake_resolve(*a, **kw):
        return {}, None
    monkeypatch.setattr(main_mod, "_resolve_slice_filament_payload", _fake_resolve)

    main_mod.printer_service = MagicMock()
    main_mod.printer_service.default_printer_id.return_value = "PRINTER1"
    idle_status = MagicMock()
    idle_status.gcode_state = "IDLE"
    idle_status.online = True
    main_mod.printer_service.get_status = MagicMock(return_value=idle_status)

    from app.slice_jobs import SliceJobManager, SliceJobStore
    slicer = MagicMock()

    async def stream(*a, **kw):
        yield {
            "event": "result",
            "data": {"file_base64": base64.b64encode(b"sliced").decode(),
                     "file_size": 6},
        }
        yield {"event": "done", "data": {}}

    slicer.slice_stream = stream
    main_mod.slicer_client = slicer

    store = SliceJobStore(tmp_path / "slice_jobs.json")
    main_mod.slice_jobs = SliceJobManager(
        store=store, slicer=slicer, printer_service=main_mod.printer_service,
        notifier=None, max_concurrent=1,
    )
    await main_mod.slice_jobs.start()

    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await main_mod.slice_jobs.stop()


async def test_create_returns_202_with_job_id(app_client):
    resp = await app_client.post(
        "/api/slice-jobs",
        files={"file": ("cube.3mf", b"x", "application/octet-stream")},
        data={
            "machine_profile": "GM014",
            "process_profile": "0.20mm",
            "filament_profiles": "{}",
            "auto_print": "false",
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    assert body["status"] in ("queued", "slicing", "ready")


async def test_list_includes_created_jobs(app_client):
    await app_client.post(
        "/api/slice-jobs",
        files={"file": ("cube.3mf", b"x", "application/octet-stream")},
        data={
            "machine_profile": "GM014",
            "process_profile": "0.20mm",
            "filament_profiles": "{}",
        },
    )
    resp = await app_client.get("/api/slice-jobs")
    assert resp.status_code == 200
    assert len(resp.json()["jobs"]) == 1


async def test_auto_print_without_printer_id_is_400(app_client):
    resp = await app_client.post(
        "/api/slice-jobs",
        files={"file": ("cube.3mf", b"x", "application/octet-stream")},
        data={
            "machine_profile": "GM014",
            "process_profile": "0.20mm",
            "filament_profiles": "{}",
            "auto_print": "true",
        },
    )
    assert resp.status_code == 400


async def test_get_unknown_job_404(app_client):
    resp = await app_client.get("/api/slice-jobs/deadbeef")
    assert resp.status_code == 404


async def test_clear_terminal_jobs(app_client):
    create = await app_client.post(
        "/api/slice-jobs",
        files={"file": ("cube.3mf", b"x", "application/octet-stream")},
        data={
            "machine_profile": "GM014",
            "process_profile": "0.20mm",
            "filament_profiles": "{}",
        },
    )
    job_id = create.json()["job_id"]
    for _ in range(40):
        cur = await app_client.get(f"/api/slice-jobs/{job_id}")
        if cur.json()["status"] == "ready":
            break
        await asyncio.sleep(0.05)
    resp = await app_client.post("/api/slice-jobs/clear", json={})
    assert resp.status_code == 200
    assert any(j["job_id"] == job_id for j in resp.json()["jobs"])
    assert (await app_client.get(f"/api/slice-jobs/{job_id}")).status_code == 404


async def test_print_stream_wraps_slice_job(app_client):
    """The rewritten /api/print-stream should still emit SSE events and a result."""
    resp = await app_client.post(
        "/api/print-stream",
        files={"file": ("cube.3mf", b"x", "application/octet-stream")},
        data={
            "machine_profile": "GM014",
            "process_profile": "0.20mm",
            "filament_profiles": "{}",
            "preview": "true",  # avoid auto_print path which needs printer wiring
        },
    )
    assert resp.status_code == 200
    body = resp.text
    assert "event: result" in body
    assert "event: done" in body
    # preview mode includes preview_id alias
    assert "preview_id" in body


async def test_print_preview_returns_sliced_bytes_and_headers(app_client):
    resp = await app_client.post(
        "/api/print-preview",
        files={"file": ("cube.3mf", b"x", "application/octet-stream")},
        data={
            "machine_profile": "GM014",
            "process_profile": "0.20mm",
            "filament_profiles": "{}",
        },
    )
    assert resp.status_code == 200
    assert resp.content == b"sliced"
    assert resp.headers["x-preview-id"]
    assert resp.headers["x-job-id"] == resp.headers["x-preview-id"]


async def test_print_with_job_id_starts_upload_and_marks_printing(
    app_client, monkeypatch
):
    """Submitting `/api/print` with a job_id should upload the sliced bytes."""
    import app.main as main_mod

    # Create a slice job and wait for ready
    create = await app_client.post(
        "/api/slice-jobs",
        files={"file": ("cube.3mf", b"x", "application/octet-stream")},
        data={
            "machine_profile": "GM014",
            "process_profile": "0.20mm",
            "filament_profiles": "{}",
        },
    )
    job_id = create.json()["job_id"]
    for _ in range(40):
        cur = await app_client.get(f"/api/slice-jobs/{job_id}")
        if cur.json()["status"] == "ready":
            break
        await asyncio.sleep(0.05)

    # Stub printer client + background submit so we don't hit real printer
    fake_client = MagicMock()
    fake_client.ensure_connected = MagicMock()
    fake_client.get_status = MagicMock(return_value=MagicMock(online=True))
    main_mod.printer_service.get_client = MagicMock(return_value=fake_client)
    monkeypatch.setattr(main_mod, "_background_submit", lambda *a, **kw: None)

    resp = await app_client.post(
        "/api/print",
        data={"job_id": job_id, "printer_id": "PRINTER1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "uploading"

    # Slice job should now be in PRINTING status
    job_after = await app_client.get(f"/api/slice-jobs/{job_id}")
    assert job_after.json()["status"] == "printing"


async def test_print_reprints_a_failed_job(app_client, monkeypatch):
    """A failed slice job whose sliced output is still on disk can be reprinted."""
    import app.main as main_mod
    from app.slice_jobs import SliceJobStatus

    create = await app_client.post(
        "/api/slice-jobs",
        files={"file": ("cube.3mf", b"x", "application/octet-stream")},
        data={
            "machine_profile": "GM014",
            "process_profile": "0.20mm",
            "filament_profiles": "{}",
        },
    )
    job_id = create.json()["job_id"]
    for _ in range(40):
        cur = await app_client.get(f"/api/slice-jobs/{job_id}")
        if cur.json()["status"] == "ready":
            break
        await asyncio.sleep(0.05)

    # Pretend a previous print attempt for this slice failed (e.g. the
    # printer rejected the upload). The sliced output blob is still on disk.
    job = await main_mod.slice_jobs._store.get(job_id)
    job.status = SliceJobStatus.FAILED
    await main_mod.slice_jobs._store.upsert(job)

    fake_client = MagicMock()
    fake_client.ensure_connected = MagicMock()
    fake_client.get_status = MagicMock(return_value=MagicMock(online=True))
    main_mod.printer_service.get_client = MagicMock(return_value=fake_client)
    monkeypatch.setattr(main_mod, "_background_submit", lambda *a, **kw: None)

    resp = await app_client.post(
        "/api/print",
        data={"job_id": job_id, "printer_id": "PRINTER1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "uploading"
    after = await app_client.get(f"/api/slice-jobs/{job_id}")
    assert after.json()["status"] == "printing"


async def test_print_rejects_in_flight_slicing_job(app_client):
    """A slice job that is still being processed (slicing/uploading/queued)
    cannot be printed. `printing` is allowed because the slice-job state
    machine never advances to FINISHED — the frontend heuristic decides
    when a `printing` row is safe to reprint."""
    import app.main as main_mod
    from app.slice_jobs import SliceJob, SliceJobStatus

    seed = SliceJob.new(
        filename="x.3mf", machine_profile="GM014", process_profile="0.20mm",
        filament_profiles={}, plate_id=1, plate_type="",
        project_filament_count=0, printer_id=None, auto_print=False,
        input_path=main_mod.slice_jobs._store.input_path("slicingjob"),
    )
    seed.id = "slicingjob"
    seed.status = SliceJobStatus.SLICING
    Path(seed.input_path).write_bytes(b"x")
    await main_mod.slice_jobs._store.upsert(seed)

    resp = await app_client.post(
        "/api/print",
        data={"job_id": seed.id, "printer_id": "PRINTER1"},
    )
    assert resp.status_code == 409


async def test_print_with_unknown_job_id_404(app_client):
    resp = await app_client.post(
        "/api/print",
        data={"job_id": "deadbeef"},
    )
    assert resp.status_code == 404


async def test_get_input_returns_uploaded_bytes(app_client):
    create = await app_client.post(
        "/api/slice-jobs",
        files={"file": ("cube.3mf", b"original-bytes", "application/octet-stream")},
        data={
            "machine_profile": "GM014",
            "process_profile": "0.20mm",
            "filament_profiles": "{}",
        },
    )
    job_id = create.json()["job_id"]

    resp = await app_client.get(f"/api/slice-jobs/{job_id}/input")
    assert resp.status_code == 200
    assert resp.content == b"original-bytes"
    assert resp.headers["x-job-id"] == job_id
    assert "cube.3mf" in resp.headers["content-disposition"]


async def test_get_input_handles_non_ascii_filename(app_client):
    """Non-ASCII filenames must serialize via RFC 5987 instead of crashing
    Starlette's latin-1 header encoder with a 500. Regression: a job with
    a Chinese filename (`小号-多色一体打印版.3mf`) used to surface as a 500."""
    create = await app_client.post(
        "/api/slice-jobs",
        files={
            "file": (
                "小号-多色一体打印版.3mf",
                b"original-bytes",
                "application/octet-stream",
            )
        },
        data={
            "machine_profile": "GM014",
            "process_profile": "0.20mm",
            "filament_profiles": "{}",
        },
    )
    job_id = create.json()["job_id"]

    resp = await app_client.get(f"/api/slice-jobs/{job_id}/input")
    assert resp.status_code == 200
    assert resp.content == b"original-bytes"
    disposition = resp.headers["content-disposition"]
    # Modern UA: full filename via RFC 5987.
    assert "filename*=UTF-8''" in disposition
    assert "%E5%B0%8F%E5%8F%B7" in disposition
    # Legacy fallback: ASCII-only `filename=` so latin-1 encoding succeeds.
    assert 'filename="' in disposition
    legacy_value = disposition.split('filename="', 1)[1].split('"', 1)[0]
    legacy_value.encode("latin-1")


async def test_get_input_404_unknown_job(app_client):
    resp = await app_client.get("/api/slice-jobs/deadbeef/input")
    assert resp.status_code == 404


async def test_get_input_410_when_blob_missing(app_client):
    """If the input file has been removed but the job entry still exists,
    the endpoint reports 410 rather than crashing."""
    import app.main as main_mod
    from app.slice_jobs import SliceJob

    seed = SliceJob.new(
        filename="x.3mf", machine_profile="GM014", process_profile="0.20mm",
        filament_profiles={}, plate_id=1, plate_type="",
        project_filament_count=0, printer_id=None, auto_print=False,
        input_path=main_mod.slice_jobs._store.input_path("gonejob"),
    )
    seed.id = "gonejob"
    # Deliberately do not write the input blob.
    await main_mod.slice_jobs._store.upsert(seed)

    resp = await app_client.get(f"/api/slice-jobs/{seed.id}/input")
    assert resp.status_code == 410


async def test_get_output_returns_sliced_bytes(app_client):
    create = await app_client.post(
        "/api/slice-jobs",
        files={"file": ("cube.3mf", b"x", "application/octet-stream")},
        data={
            "machine_profile": "GM014",
            "process_profile": "0.20mm",
            "filament_profiles": "{}",
        },
    )
    job_id = create.json()["job_id"]
    for _ in range(40):
        cur = await app_client.get(f"/api/slice-jobs/{job_id}")
        if cur.json()["status"] == "ready":
            break
        await asyncio.sleep(0.05)

    resp = await app_client.get(f"/api/slice-jobs/{job_id}/output")
    assert resp.status_code == 200
    assert resp.content == b"sliced"
    assert resp.headers["x-job-id"] == job_id
    assert resp.headers["x-preview-id"] == job_id


async def test_get_output_409_when_not_ready(app_client):
    """A queued job has no output yet — endpoint returns 409."""
    import app.main as main_mod
    from app.slice_jobs import SliceJob

    seed = SliceJob.new(
        filename="x.3mf", machine_profile="GM014", process_profile="0.20mm",
        filament_profiles={}, plate_id=1, plate_type="",
        project_filament_count=0, printer_id=None, auto_print=False,
        input_path=main_mod.slice_jobs._store.input_path("seedjob"),
    )
    seed.id = "seedjob"
    Path(seed.input_path).write_bytes(b"x")
    await main_mod.slice_jobs._store.upsert(seed)

    resp = await app_client.get(f"/api/slice-jobs/{seed.id}/output")
    assert resp.status_code == 409


async def test_get_output_404_unknown_job(app_client):
    resp = await app_client.get("/api/slice-jobs/deadbeef/output")
    assert resp.status_code == 404


async def test_get_thumbnail_404_when_missing(app_client):
    """A queued job has no thumbnail yet — endpoint returns 404."""
    import app.main as main_mod
    from app.slice_jobs import SliceJob

    seed = SliceJob.new(
        filename="x.3mf", machine_profile="GM014", process_profile="0.20mm",
        filament_profiles={}, plate_id=1, plate_type="",
        project_filament_count=0, printer_id=None, auto_print=False,
        input_path=main_mod.slice_jobs._store.input_path("thumbjob"),
    )
    seed.id = "thumbjob"
    Path(seed.input_path).write_bytes(b"x")
    await main_mod.slice_jobs._store.upsert(seed)

    resp = await app_client.get(f"/api/slice-jobs/{seed.id}/thumbnail")
    assert resp.status_code == 404


async def test_get_thumbnail_returns_png_bytes(app_client):
    """When a job has a stored thumbnail data URL, the endpoint streams the PNG."""
    import base64 as _b64
    import app.main as main_mod
    from app.slice_jobs import SliceJob

    seed = SliceJob.new(
        filename="x.3mf", machine_profile="GM014", process_profile="0.20mm",
        filament_profiles={}, plate_id=1, plate_type="",
        project_filament_count=0, printer_id=None, auto_print=False,
        input_path=main_mod.slice_jobs._store.input_path("thumbjob2"),
    )
    seed.id = "thumbjob2"
    Path(seed.input_path).write_bytes(b"x")
    seed.thumbnail = (
        "data:image/png;base64," + _b64.b64encode(b"\x89PNG\r\n\x1a\n_fake").decode()
    )
    await main_mod.slice_jobs._store.upsert(seed)

    resp = await app_client.get(f"/api/slice-jobs/{seed.id}/thumbnail")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == b"\x89PNG\r\n\x1a\n_fake"


async def test_create_rejects_invalid_filament_payload_with_400(
    tmp_path, monkeypatch
):
    """Missing profile_setting_id should be caught at the gateway (not the slicer)."""
    from app import config_store
    from app.config import settings
    import app.main as main_mod
    from app.slice_jobs import SliceJobManager, SliceJobStore

    config_store.set_path(tmp_path / "printers.json")
    monkeypatch.setattr(settings, "orcaslicer_api_url", "http://stub")
    # Project has 5 filaments — matches the user's bug report shape.
    monkeypatch.setattr(
        main_mod,
        "parse_3mf",
        lambda data: MagicMock(
            filaments=[MagicMock(setting_id=f"f{i}") for i in range(5)],
        ),
    )

    main_mod.printer_service = MagicMock()
    main_mod.printer_service.default_printer_id.return_value = "PRINTER1"

    slicer = MagicMock()
    slicer.slice_stream = MagicMock()  # should never be called
    main_mod.slicer_client = slicer

    store = SliceJobStore(tmp_path / "slice_jobs.json")
    main_mod.slice_jobs = SliceJobManager(
        store=store, slicer=slicer, printer_service=main_mod.printer_service,
        notifier=None, max_concurrent=1,
    )
    await main_mod.slice_jobs.start()
    try:
        transport = httpx.ASGITransport(app=main_mod.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/slice-jobs",
                files={"file": ("cube.3mf", b"x", "application/octet-stream")},
                data={
                    "machine_profile": "GM014",
                    "process_profile": "0.20mm",
                    # Override for index 4 with a missing profile_setting_id
                    "filament_profiles": '{"4": {"profile_setting_id": ""}}',
                },
            )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "profile_setting_id" in detail
        # Slicer was never invoked.
        slicer.slice_stream.assert_not_called()
    finally:
        await main_mod.slice_jobs.stop()
