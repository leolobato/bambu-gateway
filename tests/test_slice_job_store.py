import asyncio
from pathlib import Path

import pytest

from app.slice_jobs import SliceJob, SliceJobStatus, SliceJobStore


def _make_job(tmp_jobs_dir: Path, **overrides) -> SliceJob:
    return SliceJob.new(
        filename=overrides.get("filename", "cube.3mf"),
        machine_profile="GM014",
        process_profile="0.20mm",
        filament_profiles={"0": "GFL99"},
        plate_id=1,
        plate_type="",
        project_filament_count=1,
        printer_id="PRINTER1",
        auto_print=False,
        input_path=tmp_jobs_dir / "slice_jobs" / "in.3mf",
    )


async def test_store_creates_directory_on_init(tmp_path: Path):
    base = tmp_path / "newdir"
    store = SliceJobStore(base / "slice_jobs.json")
    assert (base / "slice_jobs").exists()
    assert (await store.list_all()) == []


async def test_save_and_load_round_trip(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    job = _make_job(tmp_jobs_dir)
    await store.upsert(job)

    other = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    loaded = await other.list_all()
    assert len(loaded) == 1
    assert loaded[0].id == job.id


async def test_upsert_replaces_existing(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    job = _make_job(tmp_jobs_dir)
    await store.upsert(job)
    job.status = SliceJobStatus.SLICING
    job.progress = 50
    await store.upsert(job)

    loaded = await store.list_all()
    assert len(loaded) == 1
    assert loaded[0].status == SliceJobStatus.SLICING
    assert loaded[0].progress == 50


async def test_delete_removes_job_and_blobs(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    job = _make_job(tmp_jobs_dir)
    Path(job.input_path).write_bytes(b"input")
    output = tmp_jobs_dir / "slice_jobs" / f"{job.id}.output.3mf"
    output.write_bytes(b"output")
    job.output_path = str(output)
    await store.upsert(job)

    await store.delete(job.id)

    assert (await store.get(job.id)) is None
    assert not Path(job.input_path).exists()
    assert not output.exists()


async def test_blob_path_helpers(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    assert store.input_path("xyz") == tmp_jobs_dir / "slice_jobs" / "xyz.input.3mf"
    assert store.output_path("xyz") == tmp_jobs_dir / "slice_jobs" / "xyz.output.3mf"


async def test_atomic_write_does_not_corrupt_on_partial_failure(
    tmp_jobs_dir: Path, monkeypatch
):
    """If rename fails, the existing JSON file must remain valid."""
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    await store.upsert(_make_job(tmp_jobs_dir, filename="first.3mf"))

    original_replace = Path.replace
    calls = {"n": 0}

    def flaky_replace(self, target):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated rename failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    with pytest.raises(OSError):
        await store.upsert(_make_job(tmp_jobs_dir, filename="second.3mf"))

    monkeypatch.setattr(Path, "replace", original_replace)
    loaded = await store.list_all()
    assert len(loaded) == 1
    assert loaded[0].filename == "first.3mf"
