"""Preparation stage definitions and state derivation for Bambu Lab printers.

Port of PreparationStages from panda-be-free.
"""

from __future__ import annotations


class StageCategory:
    PREPARE = "prepare"
    CALIBRATE = "calibrate"
    PAUSED = "paused"
    FILAMENT = "filament"
    ISSUE = "issue"


STAGE_NAMES: dict[int, str] = {
    1: "Auto bed leveling",
    2: "Preheating heatbed",
    3: "Vibration compensation",
    4: "Changing filament",
    5: "M400 pause",
    6: "Filament runout pause",
    7: "Heating hotend",
    8: "Calibrating extrusion",
    9: "Scanning bed surface",
    10: "Inspecting first layer",
    11: "Identifying build plate",
    12: "Calibrating micro lidar",
    13: "Homing toolhead",
    14: "Cleaning nozzle tip",
    15: "Checking extruder temp",
    16: "Paused by user",
    17: "Front cover falling",
    18: "Calibrating micro lidar",
    19: "Calibrating extrusion flow",
    20: "Nozzle temp malfunction",
    21: "Heatbed temp malfunction",
    22: "Filament unloading",
    23: "Paused: skipped step",
    24: "Filament loading",
    25: "Calibrating motor noise",
    26: "Paused: AMS lost",
    27: "Paused: low fan speed",
    28: "Chamber temp control error",
    29: "Cooling chamber",
    30: "Paused by G-code",
    31: "Motor noise calibration",
    32: "Paused: nozzle filament covered",
    33: "Paused: cutter error",
    34: "Paused: first layer error",
    35: "Paused: nozzle clog",
    36: "Checking absolute accuracy",
    37: "Absolute accuracy calibration",
    38: "Checking absolute accuracy",
    39: "Calibrating nozzle offset",
    40: "Bed leveling (high temp)",
    41: "Checking quick release",
    42: "Checking door and cover",
    43: "Laser calibration",
    44: "Checking platform",
    45: "Checking camera position",
    46: "Calibrating camera",
    47: "Bed leveling phase 1",
    48: "Bed leveling phase 2",
    49: "Heating chamber",
    50: "Cooling heatbed",
    51: "Printing calibration lines",
    52: "Checking material",
    53: "Live view camera calibration",
    54: "Waiting for heatbed temp",
    55: "Checking material position",
    56: "Cutting module offset calibration",
    57: "Measuring surface",
    58: "Thermal preconditioning",
    59: "Homing blade holder",
    60: "Calibrating camera offset",
    61: "Calibrating blade holder",
    62: "Hotend pick and place test",
    63: "Waiting for chamber temp",
    64: "Preparing hotend",
    65: "Calibrating nozzle clump detection",
    66: "Purifying chamber air",
    77: "Preparing AMS",
}

_PREPARE_STAGES = frozenset({
    1, 2, 3, 7, 9, 11, 13, 14, 15, 29,
    40, 41, 42, 47, 48, 49, 50, 51, 52, 54,
    55, 57, 58, 59, 63, 64, 66, 77,
})

_CALIBRATE_STAGES = frozenset({
    8, 10, 12, 18, 19, 25, 31, 36, 37, 38,
    39, 43, 44, 45, 46, 53, 56, 60, 61, 62, 65,
})

_PAUSED_STAGES = frozenset({5, 16, 30})

_FILAMENT_STAGES = frozenset({4, 22, 24})

_ISSUE_STAGES = frozenset({
    6, 17, 20, 21, 23, 26, 27, 28, 32, 33, 34, 35,
})


def stage_name(stg_cur: int) -> str | None:
    """Return a human-readable name for a preparation stage number."""
    return STAGE_NAMES.get(stg_cur)


def stage_category(stg_cur: int) -> str | None:
    """Return the category for a preparation stage number."""
    if stg_cur in _PREPARE_STAGES:
        return StageCategory.PREPARE
    if stg_cur in _CALIBRATE_STAGES:
        return StageCategory.CALIBRATE
    if stg_cur in _PAUSED_STAGES:
        return StageCategory.PAUSED
    if stg_cur in _FILAMENT_STAGES:
        return StageCategory.FILAMENT
    if stg_cur in _ISSUE_STAGES:
        return StageCategory.ISSUE
    return None


def determine_state(
    gcode_state: str,
    stg_cur: int,
    layer_num: int,
) -> tuple[str, str | None, str | None]:
    """Derive printer state, stage category, and stage name from MQTT fields.

    Returns (state, category, stage_name) where state is one of:
    "idle", "preparing", "printing", "paused", "finished", "cancelled", "error"
    """
    from app.models import PrinterState

    if gcode_state in ("FINISH", "COMPLETED"):
        return PrinterState.finished, None, None

    if gcode_state in ("CANCELLED", "FAILED"):
        return PrinterState.cancelled, None, None

    cat = stage_category(stg_cur)
    name = stage_name(stg_cur)

    if gcode_state == "PAUSE":
        return PrinterState.paused, cat or StageCategory.PAUSED, name

    # Issue stages always surface as error
    if cat == StageCategory.ISSUE:
        return PrinterState.error, cat, name

    # Pause stages (without PAUSE gcode state) still mean paused
    if cat == StageCategory.PAUSED:
        return PrinterState.paused, cat, name

    # Prep/calibration/filament stages
    if cat in (StageCategory.PREPARE, StageCategory.CALIBRATE, StageCategory.FILAMENT):
        if gcode_state == "PREPARE" or (
            gcode_state in ("RUNNING", "PRINTING") and name is not None
        ):
            if layer_num >= 1:
                # Mid-print interruption (e.g. filament change during print)
                return PrinterState.paused, cat, name
            else:
                return PrinterState.preparing, cat, name

    if gcode_state in ("RUNNING", "PRINTING"):
        return PrinterState.printing, None, None

    return PrinterState.idle, None, None
