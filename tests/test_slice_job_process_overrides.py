"""Unit tests for process_overrides on SliceJob."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.slice_jobs import SliceJob, SliceJobStore, SliceJobStatus


def _new_job(**overrides) -> SliceJob:
    base = dict(
        filename="test.3mf",
        machine_profile="GM004",
        process_profile="GP004",
        filament_profiles=["Bambu PLA Basic"],
        plate_id=1,
        plate_type="",
        project_filament_count=1,
        printer_id="PRINTER1",
        auto_print=False,
        input_path=Path("/tmp/test.3mf"),
    )
    base.update(overrides)
    return SliceJob.new(**base)


def test_slice_job_defaults_process_overrides_to_none():
    job = _new_job()
    assert job.process_overrides is None


def test_slice_job_carries_process_overrides_when_set():
    job = _new_job()
    job.process_overrides = {"layer_height": "0.16"}
    d = job.to_dict()
    assert d["process_overrides"] == {"layer_height": "0.16"}


def test_slice_job_round_trips_through_dict():
    job = _new_job()
    job.process_overrides = {"layer_height": "0.16", "wall_loops": "3"}
    restored = SliceJob.from_dict(job.to_dict())
    assert restored.process_overrides == {"layer_height": "0.16", "wall_loops": "3"}


def test_slice_job_round_trips_with_none_overrides():
    job = _new_job()
    restored = SliceJob.from_dict(job.to_dict())
    assert restored.process_overrides is None


def test_slice_job_from_dict_handles_legacy_payload(tmp_path):
    """A slice_jobs.json written before this feature has no key for the field."""
    legacy = {
        "id": "abc123",
        "created_at": "2026-04-01T00:00:00Z",
        "updated_at": "2026-04-01T00:00:00Z",
        "filename": "old.3mf",
        "machine_profile": "GM004",
        "process_profile": "GP004",
        "filament_profiles": ["Bambu PLA Basic"],
        "plate_id": 1,
        "plate_type": "",
        "project_filament_count": 1,
        "printer_id": "PRINTER1",
        "auto_print": False,
        "input_path": "/tmp/old.3mf",
        "output_path": None,
        "status": "ready",
        "progress": 100,
        "phase": None,
        "printed": False,
        "estimate": None,
        "settings_transfer": None,
        "output_size": None,
        "thumbnail": None,
        "error": None,
    }
    job = SliceJob.from_dict(legacy)
    assert job.process_overrides is None


@pytest.mark.asyncio
async def test_store_persists_process_overrides_round_trip(tmp_path):
    json_path = tmp_path / "slice_jobs.json"
    store = SliceJobStore(json_path)
    job = _new_job()
    job.process_overrides = {"layer_height": "0.16"}
    await store.upsert(job)

    # Re-read from disk via a fresh store.
    store2 = SliceJobStore(json_path)
    restored = await store2.get(job.id)
    assert restored is not None
    assert restored.process_overrides == {"layer_height": "0.16"}
