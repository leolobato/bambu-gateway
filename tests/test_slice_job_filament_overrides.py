from app.slice_jobs import SliceJob


def test_slice_job_new_stores_filament_overrides(tmp_path):
    blob = tmp_path / "in.3mf"
    blob.write_bytes(b"x")
    job = SliceJob.new(
        filename="a.3mf",
        machine_profile="GM020",
        process_profile="GP109",
        filament_profiles=["GFL99"],
        plate_id=1,
        plate_type="",
        project_filament_count=1,
        printer_id=None,
        auto_print=False,
        input_path=blob,
        filament_overrides={"0": {"nozzle_temperature": "230"}},
    )
    assert job.filament_overrides == {"0": {"nozzle_temperature": "230"}}


def test_slice_job_filament_overrides_round_trip(tmp_path):
    blob = tmp_path / "in.3mf"
    blob.write_bytes(b"x")
    job = SliceJob.new(
        filename="a.3mf", machine_profile="GM020", process_profile="GP109",
        filament_profiles=["GFL99"], plate_id=1, plate_type="",
        project_filament_count=1, printer_id=None, auto_print=False,
        input_path=blob, filament_overrides={"1": {"filament_flow_ratio": "0.95"}},
    )
    restored = SliceJob.from_dict(job.to_dict())
    assert restored.filament_overrides == {"1": {"filament_flow_ratio": "0.95"}}


def test_slice_job_defaults_filament_overrides_none(tmp_path):
    blob = tmp_path / "in.3mf"
    blob.write_bytes(b"x")
    job = SliceJob.new(
        filename="a.3mf", machine_profile="GM020", process_profile="GP109",
        filament_profiles=["GFL99"], plate_id=1, plate_type="",
        project_filament_count=1, printer_id=None, auto_print=False,
        input_path=blob,
    )
    assert job.filament_overrides is None
    # Legacy jobs persisted before this field must still load.
    legacy = job.to_dict()
    legacy.pop("filament_overrides")
    assert SliceJob.from_dict(legacy).filament_overrides is None
