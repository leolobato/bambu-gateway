"""Async adapter from orcaslicer-headless /3mf/inspect → ThreeMFInfo.

Replaces the in-process ZIP/XML parser. Behaviour preserved:
- ``plate_id=None`` returns every plate's ``used_filament_indices``;
- ``plate_id=N`` returns only that plate's slot data;
- ``FilamentInfo.used`` reflects union (or single-plate) as before.

Thumbnails: the inspect endpoint returns URLs; we fetch them and
base64-encode for the existing ``PlateInfo.thumbnail`` field. This adds
one round trip per plate but keeps the wire shape unchanged. Migration
to URL-based thumbnails is a separate Phase 4 task.
"""
from __future__ import annotations

import base64
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

from app.models import (
    FilamentInfo,
    PlateInfo,
    PlateObject,
    PrinterInfo,
    PrintProfileInfo,
    ProcessModifications,
    ThreeMFInfo,
)
from app.slicer_client import SlicerClient


async def parse_3mf_via_slicer(
    data: bytes,
    slicer: SlicerClient,
    *,
    plate_id: Optional[int] = None,
    include_thumbnails: bool = True,
) -> ThreeMFInfo:
    """Upload + inspect + adapt. Always deletes the upload token before returning."""
    upload = await slicer.upload_3mf(data)
    token = upload["token"]
    try:
        insp = await slicer.inspect(token)
        thumbnails: dict[int, str] = {}
        if include_thumbnails:
            thumbnails = await _fetch_main_thumbnails(slicer, token, insp)
        return _adapt(insp, plate_id=plate_id, thumbnails=thumbnails)
    finally:
        await slicer.delete_token(token)


async def _fetch_main_thumbnails(
    slicer: SlicerClient, token: str, insp: dict,
) -> dict[int, str]:
    """Fetch one ``main`` PNG per plate, return ``{plate_id: data-url}``.

    The value is a ``data:image/png;base64,...`` URL so the frontend can
    drop it straight into ``<img src>``. Plates without a main thumbnail
    are omitted — older 3MFs that only carry ``small`` variants surface
    as empty strings, matching the pre-migration behaviour for those files.
    """
    out: dict[int, str] = {}
    for entry in insp.get("thumbnail_urls", []):
        if entry.get("kind") != "main":
            continue
        plate = int(entry["plate"])
        if plate in out:
            continue
        url = f"{slicer._base_url}{entry['url']}"
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=30.0)
            if r.status_code == 200:
                b64 = base64.b64encode(r.content).decode("ascii")
                out[plate] = f"data:image/png;base64,{b64}"
    return out


def _adapt(
    insp: dict,
    *,
    plate_id: Optional[int],
    thumbnails: dict[int, str],
) -> ThreeMFInfo:
    plates: list[PlateInfo] = []
    for p in insp.get("plates", []):
        pid = int(p["id"])
        plates.append(PlateInfo(
            id=pid,
            name=p.get("name", "") or "",
            objects=[
                PlateObject(id=str(o.get("id", "")), name=o.get("name", "") or "")
                for o in p.get("objects", [])
            ],
            thumbnail=thumbnails.get(pid, ""),
            used_filament_indices=p.get("used_filament_indices"),
        ))

    filaments: list[FilamentInfo] = []
    for f in insp.get("filaments", []):
        filaments.append(FilamentInfo(
            index=int(f["slot"]),
            type=f.get("type", "") or "",
            color=f.get("color", "") or "",
            setting_id=f.get("settings_id", "") or "",
            used=True,  # Set below.
        ))

    # Mirror per-plate selection onto FilamentInfo.used.
    if plate_id is not None:
        selected = next((p for p in plates if p.id == plate_id), None)
        target_indices = (
            selected.used_filament_indices
            if selected and selected.used_filament_indices is not None
            else [f.index for f in filaments]
        )
    else:
        union: set[int] = set()
        any_known = False
        for p in plates:
            if p.used_filament_indices is not None:
                union |= set(p.used_filament_indices)
                any_known = True
        target_indices = list(union) if any_known else [f.index for f in filaments]
    target_set = set(target_indices)
    for f in filaments:
        f.used = f.index in target_set

    pm_raw = insp.get("process_modifications") or {}
    process_modifications = ProcessModifications(
        process_setting_id=pm_raw.get("process_setting_id", "") or "",
        modified_keys=list(pm_raw.get("modified_keys") or []),
        values=dict(pm_raw.get("values") or {}),
    )

    logger.info(
        "parse_3mf: plate_id=%s filaments=%s used=%s",
        plate_id,
        [(f.index, f.setting_id) for f in filaments],
        sorted(int(i) for i in target_set),
    )

    return ThreeMFInfo(
        plates=plates,
        filaments=filaments,
        print_profile=PrintProfileInfo(
            print_settings_id=insp.get("print_settings_id", "") or "",
            layer_height=insp.get("layer_height", "") or "",
        ),
        printer=PrinterInfo(
            printer_settings_id=insp.get("printer_settings_id", "") or "",
            printer_model=insp.get("printer_model", "") or "",
            nozzle_diameter=insp.get("printer_variant", "") or "",
        ),
        has_gcode=bool(insp.get("is_sliced", False)),
        bed_type=insp.get("curr_bed_type", "") or "",
        process_modifications=process_modifications,
    )
