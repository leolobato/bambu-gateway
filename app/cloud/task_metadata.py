"""Optional Bambu Cloud metadata for a printer-reported print subtask.

The cloud data is deliberately advisory.  It can name a job, provide the
slicer's full-job filament totals for validation, and point at an explicit
3MF artifact.  It is never converted into usage charged to Spoolman.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

from app.cloud.plugin_host import PluginHost


def _number(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _integer(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _object(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _explicit_3mf_url(value) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        return None
    return candidate if parsed.path.lower().endswith(".3mf") else None


def _find_3mf_url(value) -> str | None:
    """Find only an explicit HTTP(S) 3MF artifact, never a thumbnail URL."""
    direct = _explicit_3mf_url(value)
    if direct:
        return direct
    if isinstance(value, dict):
        # File-bearing fields first. The recursive fallback handles Bambu API
        # shape changes while the .3mf suffix keeps image/model URLs out.
        preferred = (
            "projectFile", "project_file", "fileUrl", "file_url",
            "downloadUrl", "download_url", "url",
        )
        for key in preferred:
            found = _find_3mf_url(value.get(key))
            if found:
                return found
        for item in value.values():
            found = _find_3mf_url(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_3mf_url(item)
            if found:
                return found
    return None


def normalize_subtask_metadata(
    task: dict, *, subtask_id: str,
) -> tuple[dict, str | None]:
    """Return ``(usage_reference, optional_3mf_url)`` from plugin task JSON."""
    content = _object(task.get("content"))
    info = _object(content.get("info"))
    plate_index = _integer(
        info.get("plate_idx")
        if "plate_idx" in info
        else task.get("plateIndex")
    )

    context = _object(task.get("context"))
    plates = context.get("plates")
    if not isinstance(plates, list):
        plates = []
    plate = next(
        (
            item for item in plates
            if isinstance(item, dict)
            and plate_index is not None
            and _integer(item.get("index")) == plate_index
        ),
        None,
    )
    if plate is None and len(plates) == 1 and isinstance(plates[0], dict):
        plate = plates[0]
        if plate_index is None:
            plate_index = _integer(plate.get("index"))
    plate = plate or {}

    raw_filaments = plate.get("filaments")
    filaments: list[dict] = []
    if isinstance(raw_filaments, list):
        for position, item in enumerate(raw_filaments):
            if not isinstance(item, dict):
                continue
            used_m = _number(item.get("used_m"))
            used_g = _number(item.get("used_g"))
            filaments.append({
                "filament_index": position,
                "used_length_mm": (
                    round(used_m * 1000.0, 6) if used_m is not None else None
                ),
                "used_weight_g": used_g,
                "type": str(item.get("type") or "").strip() or None,
                "color": str(item.get("color") or "").strip() or None,
            })

    title = next(
        (
            str(value).strip()
            for value in (
                task.get("title"),
                task.get("designTitle"),
                task.get("name"),
                info.get("name"),
            )
            if value is not None and str(value).strip()
        ),
        None,
    )
    total_weight = _number(plate.get("weight"))
    if total_weight is None:
        weights = [
            item["used_weight_g"] for item in filaments
            if item["used_weight_g"] is not None
        ]
        total_weight = round(sum(weights), 6) if weights else None

    usable_lengths = any(
        item["used_length_mm"] is not None for item in filaments
    )
    reference = {
        "available": bool(title or filaments or total_weight is not None),
        "source": "bambu_cloud",
        "subtask_id": subtask_id,
        "title": title,
        "plate_index": plate_index,
        "total_weight_g": total_weight,
        "filaments": filaments,
        "length_validation_available": usable_lengths,
        "reason": None,
    }
    if not reference["available"]:
        reference["reason"] = (
            "Bambu Cloud returned no usable metadata for this print"
        )
    return reference, _find_3mf_url(task)


async def fetch_subtask_metadata(
    *, host: PluginHost, subtask_id: str,
) -> tuple[dict, str | None]:
    result = await host.call(
        "get_subtask_info", {"subtask_id": subtask_id}, timeout=20.0,
    )
    task = result.get("task") if isinstance(result, dict) else None
    if not isinstance(task, dict):
        task = {}
    return normalize_subtask_metadata(task, subtask_id=subtask_id)
