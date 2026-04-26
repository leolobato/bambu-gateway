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

    `used_filament_indices` (when provided) lists the project filament indices
    actually referenced by objects/parts. Indices outside this set are padded
    with a benign profile_setting_id so the slicer doesn't reject the request
    over filament profiles that won't actually print anything. Pass `None` to
    disable padding (legacy behavior).
    """
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

    # Pad unused indices with a known-valid profile so the slicer doesn't
    # reject the request over filaments that no object will extrude.
    # We can only do this when we know which indices are used and we have a
    # valid profile to fill with (taken from one of the user's overrides).
    if used_filament_indices is not None and project_filament_ids:
        fill_profile = _pick_fill_profile(payload)
        if fill_profile is not None:
            for i in range(len(project_filament_ids)):
                slot_str = str(i)
                if slot_str in payload:
                    continue
                if i in used_filament_indices:
                    # Used filament with no override — fall back to the project's
                    # setting_id; the resolver-caller is expected to validate
                    # this elsewhere if it isn't in the slicer catalog.
                    continue
                payload[slot_str] = {"profile_setting_id": fill_profile}

    return payload, None


def _pick_fill_profile(payload: dict[str, Any]) -> str | None:
    """Return the first profile_setting_id found in the user's overrides."""
    for selection in payload.values():
        if isinstance(selection, dict):
            pid = str(selection.get("profile_setting_id", "")).strip()
            if pid:
                return pid
        elif isinstance(selection, str):
            pid = selection.strip()
            if pid:
                return pid
    return None


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
) -> tuple[list[int] | None, bool]:
    """Build the ams_mapping array for the printer's project_file command.

    Returns a variable-length array (one entry per project filament) mapping
    each filament index to an AMS tray slot, plus a use_ams flag. Indices
    without a selection get -1.
    Returns (None, False) when no AMS selections are present.
    """
    tray_slots = extract_selected_tray_slots(filament_payload)
    if not tray_slots:
        logger.info(
            "AMS mapping: no tray_slot selections found in payload=%s",
            filament_payload,
        )
        return None, False

    if project_filament_count is None:
        project_filament_count = max(tray_slots) + 1

    ams_mapping = [-1] * project_filament_count

    use_ams = False
    for filament_index, tray_slot in tray_slots.items():
        if filament_index < 0 or filament_index >= project_filament_count:
            continue
        ams_mapping[filament_index] = tray_slot
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
