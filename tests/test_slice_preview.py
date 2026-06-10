"""Tests for GET /api/slice-jobs/{job_id}/preview."""
import io
import zipfile
from pathlib import Path

from tests.test_slice_jobs_api import app_client  # noqa: F401


def _make_3mf(entries: dict[str, bytes]) -> bytes:
    """Minimal zip standing in for a sliced 3MF."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return out.getvalue()


# 4-byte root offset + 'GCPV' file identifier, as FlatBuffers lays it out.
_VALID_PREVIEW = b"\x10\x00\x00\x00GCPV" + b"\x00" * 32


async def _seed_ready_job(job_id: str, threemf: bytes | None) -> None:
    """Insert a ready job; when `threemf` is None, output_path points nowhere."""
    import app.main as main_mod
    from app.slice_jobs import SliceJob, SliceJobStatus

    store = main_mod.slice_jobs._store
    seed = SliceJob.new(
        filename="x.3mf", machine_profile="GM014", process_profile="0.20mm",
        filament_profiles={}, plate_id=1, plate_type="",
        project_filament_count=0, printer_id=None, auto_print=False,
        input_path=store.input_path(job_id),
    )
    seed.id = job_id
    Path(seed.input_path).parent.mkdir(parents=True, exist_ok=True)
    Path(seed.input_path).write_bytes(b"x")
    seed.status = SliceJobStatus.READY
    out_path = store.output_path(job_id)
    if threemf is not None:
        out_path.write_bytes(threemf)
    seed.output_path = str(out_path)
    await store.upsert(seed)


async def test_preview_returns_bytes_and_headers(app_client):
    await _seed_ready_job(
        "pvjob1", _make_3mf({"Metadata/preview.bin": _VALID_PREVIEW,
                             "Metadata/plate_1.gcode": b"G1"}),
    )
    resp = await app_client.get("/api/slice-jobs/pvjob1/preview")
    assert resp.status_code == 200
    assert resp.content == _VALID_PREVIEW
    assert resp.headers["content-type"] == "application/octet-stream"
    assert resp.headers["x-preview-format-version"] == "1"
    assert "immutable" in resp.headers["cache-control"]


async def test_preview_unknown_job_is_404(app_client):
    resp = await app_client.get("/api/slice-jobs/nope/preview")
    assert resp.status_code == 404


async def test_preview_job_not_ready_is_409(app_client):
    import app.main as main_mod
    from app.slice_jobs import SliceJob

    store = main_mod.slice_jobs._store
    seed = SliceJob.new(
        filename="x.3mf", machine_profile="GM014", process_profile="0.20mm",
        filament_profiles={}, plate_id=1, plate_type="",
        project_filament_count=0, printer_id=None, auto_print=False,
        input_path=store.input_path("pvjob2"),
    )
    seed.id = "pvjob2"
    Path(seed.input_path).parent.mkdir(parents=True, exist_ok=True)
    Path(seed.input_path).write_bytes(b"x")
    await store.upsert(seed)  # status stays QUEUED

    resp = await app_client.get("/api/slice-jobs/pvjob2/preview")
    assert resp.status_code == 409


async def test_preview_missing_output_blob_is_410(app_client):
    await _seed_ready_job("pvjob3", threemf=None)
    resp = await app_client.get("/api/slice-jobs/pvjob3/preview")
    assert resp.status_code == 410


async def test_preview_entry_absent_is_404(app_client):
    await _seed_ready_job(
        "pvjob4", _make_3mf({"Metadata/plate_1.gcode": b"G1"}),
    )
    resp = await app_client.get("/api/slice-jobs/pvjob4/preview")
    assert resp.status_code == 404
    assert "preview" in resp.json()["detail"].lower()


async def test_preview_bad_magic_is_502(app_client):
    await _seed_ready_job(
        "pvjob5", _make_3mf({"Metadata/preview.bin": b"\x10\x00\x00\x00XXXX" + b"\x00" * 32}),
    )
    resp = await app_client.get("/api/slice-jobs/pvjob5/preview")
    assert resp.status_code == 502
