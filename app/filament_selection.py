"""Helpers for validating project filament selections before slicing."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_slicer_filament_payload(
    project_filament_ids: list[str],
    filament_profiles: str,
    tray_profile_map: dict[int, str] | None = None,
    used_filament_indices: set[int] | None = None,
) -> tuple[list[str] | dict[str, Any] | None, str | None]:
    """Normalize filament selection input for the slicer API.

    Slots without an explicit override keep the 3MF's authored
    `filament_settings_id` (a display name), which the slicer resolves via
    its name index. Padding unused slots with the user's chosen profile
    would make every retained slot identical and trip OrcaSlicer's
    `load_filaments_set` dedup, which mis-sizes `flush_volumes_matrix` at
    G-code export.

    `used_filament_indices` is accepted for back-compat but no longer
    drives padding.
    """
    del used_filament_indices  # retained for ABI compatibility

    if not filament_profiles:
        return list(project_filament_ids), None

    try:
        payload = json.loads(filament_profiles)
    except json.JSONDecodeError:
        return None, "filament_profiles must be valid JSON"

    if isinstance(payload, list):
        if not all(isinstance(item, str) for item in payload):
            return None, "filament_profiles list values must be strings"
        return payload, None

    if not isinstance(payload, dict):
        return None, (
            "filament_profiles must be a JSON list of setting_id strings or "
            "a JSON object mapping project filament indexes to strings or "
            "{profile_setting_id, tray_slot} objects"
        )

    for slot_str, selection in payload.items():
        try:
            idx = int(slot_str)
        except (TypeError, ValueError):
            return None, f"Invalid project filament index: {slot_str!r}"

        if idx < 0 or idx >= len(project_filament_ids):
            return None, (
                f"Project filament index {idx} out of range for "
                f"{len(project_filament_ids)} project filament(s)"
            )

        if isinstance(selection, str):
            if not selection.strip():
                return None, f"Missing profile_setting_id for project filament {idx}"
            continue

        if not isinstance(selection, dict):
            return None, (
                f"Project filament {idx} selection must be a setting_id string "
                "or an object with profile_setting_id"
            )

        profile_setting_id = str(selection.get("profile_setting_id", "")).strip()
        if not profile_setting_id:
            return None, f"Missing profile_setting_id for project filament {idx}"

        tray_slot = selection.get("tray_slot")
        if tray_slot is None:
            continue
        if not isinstance(tray_slot, int):
            return None, f"tray_slot for project filament {idx} must be an integer"
        if tray_profile_map is None:
            return None, "tray_slot requires a printer with current AMS tray data"
        if tray_slot not in tray_profile_map:
            return None, f"AMS tray slot {tray_slot} is not available on the selected printer"

    return payload, None


def extract_selected_tray_slots(
    filament_payload: list[str] | dict | None,
) -> dict[int, int]:
    """Return {project filament index -> AMS tray slot} from a payload.

    Returns {} for None, list payloads, or any payload with no integer tray_slot
    selections. (List payloads represent slicer profile-id lists with no AMS
    intent.)
    """
    if not isinstance(filament_payload, dict):
        return {}

    tray_slots: dict[int, int] = {}
    for slot_str, selection in filament_payload.items():
        if not isinstance(selection, dict):
            continue
        tray_slot = selection.get("tray_slot")
        if not isinstance(tray_slot, int):
            continue
        try:
            filament_index = int(slot_str)
        except (TypeError, ValueError):
            continue
        tray_slots[filament_index] = tray_slot
    return tray_slots


def build_ams_mapping(
    filament_payload: list[str] | dict | None,
    project_filament_count: int | None = None,
    slot_indices: list[int] | None = None,
) -> tuple[list[int] | None, bool]:
    """Build the ams_mapping array for the printer's project_file command.

    Returns a slot-indexed array mapping each authored filament slot to an
    AMS tray slot, plus a use_ams flag. Slots without a selection get -1.
    Returns (None, False) when no AMS selections are present.

    The payload's keys are *positions* in the project filament list (the
    same positional shape the gateway sends to the slicer). For sparse 3MFs
    — projects whose filament is authored on a non-zero AMS slot, e.g. a
    single-filament file emitting T1 toolchanges — position and slot index
    diverge. The printer's gcode references the authored slot, so the
    mapping array must be sized by ``max(slot)+1`` with each entry placed
    at its slot index, not its payload position. ``slot_indices`` carries
    one authored slot per project filament position (typically
    ``[f.index for f in info.filaments]``) and is used to translate.

    When ``slot_indices`` is omitted the historical position == slot
    contract is preserved, which is correct only for dense 3MFs.
    """
    tray_slots = extract_selected_tray_slots(filament_payload)
    logger.info(
        "AMS mapping inputs: payload=%s project_filament_count=%s slot_indices=%s tray_slots=%s",
        filament_payload, project_filament_count, slot_indices, tray_slots,
    )
    if not tray_slots:
        logger.info(
            "AMS mapping: no tray_slot selections found in payload=%s",
            filament_payload,
        )
        return None, False

    if slot_indices is not None:
        slot_for_position = {pos: slot for pos, slot in enumerate(slot_indices)}
        max_slot = max(slot_for_position.values(), default=-1)
        # `tray_slots` is already keyed by payload position (per
        # `extract_selected_tray_slots`); translate to authored slot.
        position_max = max(tray_slots, default=-1)
        if position_max in slot_for_position:
            max_slot = max(max_slot, slot_for_position[position_max])
        size = max(
            max_slot + 1,
            project_filament_count or 0,
        )
    else:
        slot_for_position = None
        size = project_filament_count if project_filament_count is not None else (
            max(tray_slots) + 1
        )

    ams_mapping = [-1] * size

    use_ams = False
    for position, tray_slot in tray_slots.items():
        if slot_for_position is not None:
            slot = slot_for_position.get(position)
            if slot is None:
                continue
        else:
            slot = position
        if slot < 0 or slot >= size:
            continue
        ams_mapping[slot] = tray_slot
        use_ams = True

    logger.info("AMS mapping: %s, use_ams=%s", ams_mapping, use_ams)
    return ams_mapping, use_ams


async def validate_selected_trays(
    filament_payload: list[str] | dict | None,
    printer_id: str,
    printer_service,
) -> str | None:
    """Validate that every tray_slot referenced in the payload is loaded.

    Returns None on success, or a human-readable error string if a referenced
    tray is empty. Mirrors the original `_validate_selected_trays` from
    `app/main.py`.
    """
    if not isinstance(filament_payload, dict):
        return None

    tray_selections = {
        slot_str: selection
        for slot_str, selection in filament_payload.items()
        if isinstance(selection, dict) and selection.get("tray_slot") is not None
    }
    if not tray_selections:
        return None

    ams_info = printer_service.get_ams_info(printer_id)
    if ams_info is None:
        return None

    raw_trays, _raw_units, raw_vt_tray = ams_info

    all_trays = list(raw_trays)
    if raw_vt_tray is not None:
        all_trays.append(raw_vt_tray)

    tray_profile_map: dict[int, str] = {}
    for raw in all_trays:
        try:
            slot = int(raw.get("slot", -1))
        except (TypeError, ValueError):
            continue
        tray_profile_map[slot] = str(raw.get("tray_info_idx", "")).strip()

    for slot_str, selection in tray_selections.items():
        tray_slot = selection.get("tray_slot")
        if tray_slot not in tray_profile_map:
            return f"AMS tray slot {tray_slot} is not available on the selected printer"

    return None
