"""Helpers for validating project filament selections before slicing."""

from __future__ import annotations

import json
from typing import Any


def build_slicer_filament_payload(
    project_filament_ids: list[str],
    filament_profiles: str,
    tray_profile_map: dict[int, str] | None = None,
) -> tuple[list[str] | dict[str, Any] | None, str | None]:
    """Normalize filament selection input for the slicer API."""
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
