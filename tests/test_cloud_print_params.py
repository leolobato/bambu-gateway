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
    # The cloud start_print path file-checks config_filename (-3070 when empty),
    # so it reuses the gcode-3MF path rather than a separate config-3MF export.
    assert p["config_filename"] == "/tmp/job.3mf"
    assert p["task_use_ams"] is False
    # No AMS selection -> both mapping forms empty (external spool).
    assert p["ams_mapping"] == ""
    assert p["ams_mapping2"] == ""
    # Cloud routing — connection_type empty (not "lan"):
    assert p.get("connection_type", "") == ""


def test_build_print_params_with_ams_mapping():
    p = build_print_params(
        dev_id="DEV1",
        project_name="multi-color",
        plate_index=2,
        gcode_3mf_path="/tmp/job.3mf",
        ams_mapping=[1, 0],
        use_ams=True,
    )
    assert p["task_use_ams"] is True
    # The plugin expects the same JSON int array OrcaSlicer sends
    # (SelectMachine.cpp builds `json::array()` of tray ids) and the LAN
    # MQTT path uses — a dict shape is silently ignored by the printer.
    assert p["ams_mapping"] == "[1, 0]"
    assert json.loads(p["ams_mapping"]) == [1, 0]
    # ams_mapping2 — v1 object array the A1 Mini firmware actually reads; an
    # empty one makes it fall back to the external spool. Tray ids split into
    # unit/slot by 4 slots per AMS.
    assert json.loads(p["ams_mapping2"]) == [
        {"ams_id": 0, "slot_id": 1},
        {"ams_id": 0, "slot_id": 0},
    ]


def test_build_print_params_ams_mapping2_unmapped_slot_uses_external():
    # An unmapped slot (-1) encodes the external/virtual tray (255), matching
    # OrcaSlicer's invalid-case fallback.
    p = build_print_params(
        dev_id="DEV1",
        project_name="x",
        plate_index=0,
        gcode_3mf_path="/tmp/job.3mf",
        ams_mapping=[-1, 5],
        use_ams=True,
    )
    assert json.loads(p["ams_mapping2"]) == [
        {"ams_id": 255, "slot_id": 255},
        {"ams_id": 1, "slot_id": 1},
    ]


def test_build_print_params_rejects_missing_dev_id():
    with pytest.raises(ValueError):
        build_print_params(
            dev_id="", project_name="x", plate_index=0,
            gcode_3mf_path="/tmp/x.3mf", ams_mapping=None, use_ams=False,
        )
