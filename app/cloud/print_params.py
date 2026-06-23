"""Builder for the `start_print` RPC params (cloud print submission)."""
from __future__ import annotations

import json
from typing import Sequence

# VIRTUAL_TRAY_MAIN_ID from OrcaSlicer (DevDefs.h) — the external spool. The
# v1 ams_mapping uses it for any unmapped filament slot, mirroring OrcaSlicer's
# invalid-case fallback in SelectMachine.cpp::get_ams_mapping_result.
_VIRTUAL_TRAY_ID = 255
# Bambu numbers AMS trays as a flat global id grouped by 4 slots per unit
# (unit 0 -> 0..3, unit 1 -> 4..7, ...). The v1 mapping wants the unit and
# in-unit slot split back out.
_SLOTS_PER_AMS = 4


def _build_ams_mapping2(ams_mapping: Sequence[int]) -> str:
    """Serialize the v1 ``ams_mapping2`` ([{ams_id, slot_id}, ...]).

    OrcaSlicer's cloud send path sends both the legacy flat int array
    (``ams_mapping``) AND this object array (``SelectMachine.cpp:1229-1230``).
    The A1 Mini firmware selects the tray from the v1 form; when it is empty
    the printer falls back to the external spool, so the gateway must emit it.

    Each entry is parallel to the flat array: a tray id ``t >= 0`` becomes
    ``{ams_id: t // 4, slot_id: t % 4}``; an unmapped slot (``-1``) becomes the
    virtual/external tray, exactly as OrcaSlicer encodes the invalid case.
    """
    entries = []
    for tray in ams_mapping:
        if tray is None or tray < 0:
            entries.append({"ams_id": _VIRTUAL_TRAY_ID, "slot_id": _VIRTUAL_TRAY_ID})
        else:
            entries.append(
                {"ams_id": tray // _SLOTS_PER_AMS, "slot_id": tray % _SLOTS_PER_AMS}
            )
    return json.dumps(entries)


def build_print_params(
    *,
    dev_id: str,
    project_name: str,
    plate_index: int,
    gcode_3mf_path: str,
    ams_mapping: Sequence[int] | None,
    use_ams: bool,
    preset_name: str = "",
) -> dict:
    """Construct the JSON payload for the plugin's start_print RPC.

    Mirrors ``PrintParams`` from bambu_networking.hpp:217-260, populated for
    the pure-cloud send path (mode A from spec §7.2). LAN-only fields are left
    empty/default.
    """
    if not dev_id:
        raise ValueError("dev_id is required")

    ams_list = list(ams_mapping) if ams_mapping else None

    return {
        # Identity
        "dev_id": dev_id,
        "task_name": f"{project_name}_plate_{plate_index}",
        "project_name": project_name,
        "preset_name": preset_name,
        # Files
        "filename": gcode_3mf_path,
        # The cloud start_print path file-checks config_filename and returns
        # -3070 (SP_FILE_NOT_EXIST) when it is empty — the v1 "gcode-3MF only"
        # assumption was wrong. The printer prints from `filename`; the
        # config-3MF is only consumed by the cloud dashboard's job-history
        # preview, so reusing the gcode-3MF path satisfies the existence check
        # without a separate config-3MF export.
        "config_filename": gcode_3mf_path,
        "plate_index": plate_index,
        # AMS — OrcaSlicer's cloud send path sends BOTH the legacy flat int
        # array (ams_mapping) and the v1 object array (ams_mapping2). The
        # A1 Mini firmware selects the tray from the v1 form; an empty v1
        # mapping makes it fall back to the external spool, so both are sent.
        "ams_mapping": json.dumps(ams_list) if ams_list else "",
        "ams_mapping2": _build_ams_mapping2(ams_list) if ams_list else "",
        "ams_mapping_info": "",
        "nozzles_info": "",
        "task_use_ams": use_ams,
        # Routing — cloud (empty connection_type)
        "connection_type": "",
        # Print options — defaults
        "task_bed_leveling": True,
        "task_flow_cali": True,
        "task_vibration_cali": False,
        "task_layer_inspect": False,
        "task_record_timelapse": False,
        # LAN-only fields left empty
        "dev_ip": "",
        "password": "",
        "username": "",
    }
