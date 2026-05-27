"""Tests for PrintParams construction."""
from __future__ import annotations

import json

import pytest

from app.cloud.print_params import build_print_params


def test_build_print_params_minimal():
    p = build_print_params(
        dev_id="DEV1",
        project_name="benchy",
        plate_index=0,
        gcode_3mf_path="/tmp/job.3mf",
        ams_mapping=None,
        use_ams=False,
    )
    assert p["dev_id"] == "DEV1"
    assert p["task_name"] == "benchy_plate_0"
    assert p["project_name"] == "benchy"
    assert p["plate_index"] == 0
    assert p["filename"] == "/tmp/job.3mf"
    # v1 ships gcode-3MF only — config_filename empty per Phase 0 §Q11.2.
    assert p.get("config_filename", "") == ""
    assert p["task_use_ams"] is False
    # Cloud routing — connection_type empty (not "lan"):
    assert p.get("connection_type", "") == ""


def test_build_print_params_with_ams_mapping():
    p = build_print_params(
        dev_id="DEV1",
        project_name="multi-color",
        plate_index=2,
        gcode_3mf_path="/tmp/job.3mf",
        ams_mapping={"0": "PLA_BLUE", "1": "PLA_RED"},
        use_ams=True,
    )
    assert p["task_use_ams"] is True
    # AMS mapping is JSON-string per Phase 0 spec §7.3.
    assert json.loads(p["ams_mapping"]) == {"0": "PLA_BLUE", "1": "PLA_RED"}


def test_build_print_params_rejects_missing_dev_id():
    with pytest.raises(ValueError):
        build_print_params(
            dev_id="", project_name="x", plate_index=0,
            gcode_3mf_path="/tmp/x.3mf", ams_mapping=None, use_ams=False,
        )
