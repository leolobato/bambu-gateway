from pathlib import Path

from app.slice_jobs import SliceJob, SliceJobStatus


def test_new_job_has_queued_status_and_zero_progress(tmp_jobs_dir: Path):
    job = SliceJob.new(
        filename="cube.3mf",
        machine_profile="GM014",
        process_profile="0.20mm",
        filament_profiles={"0": "GFL99"},
        plate_id=1,
        plate_type="",
        project_filament_count=1,
        printer_id="PRINTER1",
        auto_print=False,
        input_path=tmp_jobs_dir / "slice_jobs" / "abc.input.3mf",
    )
    assert job.status == SliceJobStatus.QUEUED
    assert job.progress == 0
    assert job.error is None
    assert len(job.id) == 12
    assert job.created_at == job.updated_at


def test_job_round_trips_through_dict(tmp_jobs_dir: Path):
    job = SliceJob.new(
        filename="cube.3mf",
        machine_profile="GM014",
        process_profile="0.20mm",
        filament_profiles={"0": "GFL99"},
        plate_id=1,
        plate_type="",
        project_filament_count=1,
        printer_id="PRINTER1",
        auto_print=True,
        input_path=tmp_jobs_dir / "slice_jobs" / "abc.input.3mf",
    )
    job.progress = 42
    job.phase = "slicing"

    rebuilt = SliceJob.from_dict(job.to_dict())
    assert rebuilt == job


def test_terminal_helper():
    assert SliceJobStatus.READY.is_terminal
    assert SliceJobStatus.FAILED.is_terminal
    assert SliceJobStatus.CANCELLED.is_terminal
    assert not SliceJobStatus.QUEUED.is_terminal
    assert not SliceJobStatus.SLICING.is_terminal
    assert not SliceJobStatus.UPLOADING.is_terminal


def test_legacy_printing_status_migrates_to_ready(tmp_jobs_dir: Path):
    job = SliceJob.new(
        filename="cube.3mf",
        machine_profile="GM014",
        process_profile="0.20mm",
        filament_profiles={"0": "GFL99"},
        plate_id=1,
        plate_type="",
        project_filament_count=1,
        printer_id="PRINTER1",
        auto_print=True,
        input_path=tmp_jobs_dir / "slice_jobs" / "abc.input.3mf",
    )
    legacy = job.to_dict()
    legacy["status"] = "printing"

    rebuilt = SliceJob.from_dict(legacy)
    assert rebuilt.status == SliceJobStatus.READY
