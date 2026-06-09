"""Builder for the `start_print` RPC params (cloud print submission)."""
from __future__ import annotations

import json
from typing import Sequence


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
    empty/default; v1 ships gcode-3MF only (no config-3MF).
    """
    if not dev_id:
        raise ValueError("dev_id is required")

    return {
        # Identity
        "dev_id": dev_id,
        "task_name": f"{project_name}_plate_{plate_index}",
        "project_name": project_name,
        "preset_name": preset_name,
        # Files
        "filename": gcode_3mf_path,
        "config_filename": "",  # v1: gcode-3MF only
        "plate_index": plate_index,
        # AMS — a JSON int array of tray ids, exactly what OrcaSlicer's cloud
        # send path produces (SelectMachine.cpp builds json::array()) and what
        # the LAN MQTT path publishes. Dict shapes are ignored by the printer.
        "ams_mapping": json.dumps(list(ams_mapping)) if ams_mapping else "",
        "ams_mapping2": "",
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
