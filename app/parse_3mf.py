"""Parse Bambu Lab 3MF files and extract metadata."""

from __future__ import annotations

import base64
import io
import json
import logging
import xml.etree.ElementTree as ET
import zipfile

from app.models import (
    FilamentInfo,
    PlateInfo,
    PlateObject,
    PrinterInfo,
    PrintProfileInfo,
    ThreeMFInfo,
)

logger = logging.getLogger(__name__)


def _parse_model_settings(
    zf: zipfile.ZipFile,
) -> tuple[list[PlateInfo], set[int]]:
    """Parse Metadata/model_settings.config for objects, plates, and used filament indices.

    Generic 3MFs (Thingiverse, MakerWorld, non-Bambu slicers) lack this
    Bambu-specific metadata file. In that case fall back to a single empty
    plate so the file can still be sliced — the slicer will assign objects
    to plate 1 by default. `used_filament_indices` will be empty, signalling
    "we don't know what's used; treat them all as used".
    """
    if "Metadata/model_settings.config" not in zf.namelist():
        return [PlateInfo(id=1, name="", objects=[])], set()
    raw = zf.read("Metadata/model_settings.config").decode()
    root = ET.fromstring(raw)

    # Build object_id -> name lookup
    objects: dict[str, str] = {}
    # Collect 0-based filament indices referenced by `extruder` metadata on
    # any object or part (Bambu stores extruder as 1-based: extruder=6 means
    # filament index 5).
    used_indices: set[int] = set()

    def _record_extruder(value: str) -> None:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return
        if n >= 1:
            used_indices.add(n - 1)

    for obj in root.findall("object"):
        obj_id = obj.get("id")
        for meta in obj.findall("metadata"):
            key = meta.get("key")
            if key == "name":
                objects[obj_id] = meta.get("value", "")
            elif key == "extruder":
                _record_extruder(meta.get("value", ""))
        for part in obj.findall("part"):
            for meta in part.findall("metadata"):
                if meta.get("key") == "extruder":
                    _record_extruder(meta.get("value", ""))

    plates: list[PlateInfo] = []
    for plate_el in root.findall("plate"):
        plate_id = 0
        plate_name = ""
        plate_objects: list[PlateObject] = []

        for meta in plate_el.findall("metadata"):
            key = meta.get("key")
            if key == "plater_id":
                plate_id = int(meta.get("value", "0"))
            elif key == "plater_name":
                plate_name = meta.get("value", "")

        for inst in plate_el.findall("model_instance"):
            for meta in inst.findall("metadata"):
                if meta.get("key") == "object_id":
                    oid = meta.get("value", "")
                    plate_objects.append(
                        PlateObject(id=oid, name=objects.get(oid, f"object_{oid}"))
                    )
                    break

        plates.append(PlateInfo(id=plate_id, name=plate_name, objects=plate_objects))

    return plates, used_indices


def _get_arr(settings: dict, key: str, index: int, default: str = "") -> str:
    """Safely get index from a settings array value."""
    arr = settings.get(key, [])
    if isinstance(arr, list) and index < len(arr):
        return arr[index]
    return default


def _parse_project_settings(
    zf: zipfile.ZipFile,
) -> tuple[list[FilamentInfo], PrintProfileInfo, PrinterInfo]:
    """Parse Metadata/project_settings.config for filaments, profile, printer.

    Generic 3MFs without Bambu's project settings still slice fine — the user
    will pick machine/process manually and there are no project filaments to
    map to AMS trays.
    """
    if "Metadata/project_settings.config" not in zf.namelist():
        return [], PrintProfileInfo(), PrinterInfo()
    raw = zf.read("Metadata/project_settings.config").decode()
    settings = json.loads(raw)

    filament_types = settings.get("filament_type", [])
    filaments: list[FilamentInfo] = []
    for i in range(len(filament_types)):
        filaments.append(
            FilamentInfo(
                index=i,
                type=_get_arr(settings, "filament_type", i),
                color=_get_arr(settings, "filament_colour", i),
                setting_id=_get_arr(settings, "filament_settings_id", i),
            )
        )

    print_profile = PrintProfileInfo(
        print_settings_id=settings.get("print_settings_id", ""),
        layer_height=settings.get("layer_height", ""),
    )

    printer = PrinterInfo(
        printer_settings_id=settings.get("printer_settings_id", ""),
        printer_model=settings.get("printer_model", ""),
        nozzle_diameter=settings.get("nozzle_diameter", [""])[0]
        if settings.get("nozzle_diameter")
        else "",
    )

    return filaments, print_profile, printer


def _has_gcode(zf: zipfile.ZipFile) -> bool:
    """Check if the archive contains sliced gcode."""
    return any(
        name.startswith("Metadata/plate_") and name.endswith(".gcode")
        for name in zf.namelist()
    )


def _extract_thumbnails(zf: zipfile.ZipFile, plates: list[PlateInfo]) -> None:
    """Attach base64-encoded plate thumbnails to PlateInfo objects in-place."""
    for plate in plates:
        path = f"Metadata/plate_{plate.id}.png"
        if path in zf.namelist():
            raw = zf.read(path)
            plate.thumbnail = "data:image/png;base64," + base64.b64encode(raw).decode()


def parse_3mf(data: bytes) -> ThreeMFInfo:
    """Parse a Bambu 3MF file from bytes and return structured metadata."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        plates, used_indices = _parse_model_settings(zf)
        filaments, print_profile, printer = _parse_project_settings(zf)
        has_gcode = _has_gcode(zf)
        _extract_thumbnails(zf, plates)

    # Mark each filament `used` based on which extruders any object/part
    # references. If `used_indices` is empty (generic 3MF without Bambu
    # model_settings, or no `extruder` metadata at all), default every
    # declared filament to used so behavior is unchanged for that case.
    if used_indices:
        for f in filaments:
            f.used = f.index in used_indices

    return ThreeMFInfo(
        plates=plates,
        filaments=filaments,
        print_profile=print_profile,
        printer=printer,
        has_gcode=has_gcode,
    )
