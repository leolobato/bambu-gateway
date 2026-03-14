"""Parse Bambu Lab 3MF files and extract metadata."""

from __future__ import annotations

import base64
import io
import json
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


def _parse_model_settings(zf: zipfile.ZipFile) -> list[PlateInfo]:
    """Parse Metadata/model_settings.config for objects and plates."""
    raw = zf.read("Metadata/model_settings.config").decode()
    root = ET.fromstring(raw)

    # Build object_id -> name lookup
    objects: dict[str, str] = {}
    for obj in root.findall("object"):
        obj_id = obj.get("id")
        for meta in obj.findall("metadata"):
            if meta.get("key") == "name":
                objects[obj_id] = meta.get("value", "")
                break

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

    return plates


def _get_arr(settings: dict, key: str, index: int, default: str = "") -> str:
    """Safely get index from a settings array value."""
    arr = settings.get(key, [])
    if isinstance(arr, list) and index < len(arr):
        return arr[index]
    return default


def _parse_project_settings(
    zf: zipfile.ZipFile,
) -> tuple[list[FilamentInfo], PrintProfileInfo, PrinterInfo]:
    """Parse Metadata/project_settings.config for filaments, profile, printer."""
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


# Parameters that OrcaSlicer rejects when out of range.
# Some MakerWorld 3MF files contain invalid values.
_CLAMP_RULES: dict[str, int | float] = {
    "raft_first_layer_expansion": 0,
    "solid_infill_filament": 1,
    "sparse_infill_filament": 1,
    "wall_filament": 1,
    "tree_support_wall_count": 0,
}


def sanitize_3mf(data: bytes) -> bytes:
    """Fix out-of-range parameter values in a 3MF's project settings.

    Returns the original bytes if no changes are needed, or a patched copy.
    """
    settings_file = "Metadata/project_settings.config"

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if settings_file not in zf.namelist():
            return data

        raw = zf.read(settings_file).decode()
        settings = json.loads(raw)

        changed = False
        for key, min_val in _CLAMP_RULES.items():
            if key in settings:
                val = settings[key]
                try:
                    num = float(val) if isinstance(val, str) else val
                    if num < min_val:
                        settings[key] = str(min_val) if isinstance(val, str) else min_val
                        changed = True
                except (ValueError, TypeError):
                    pass

        if not changed:
            return data

    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zf_in, \
         zipfile.ZipFile(buf, "w") as zf_out:
        for item in zf_in.infolist():
            if item.filename == settings_file:
                zf_out.writestr(item, json.dumps(settings, indent=2))
            else:
                zf_out.writestr(item, zf_in.read(item.filename))

    return buf.getvalue()


def parse_3mf(data: bytes) -> ThreeMFInfo:
    """Parse a Bambu 3MF file from bytes and return structured metadata."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        plates = _parse_model_settings(zf)
        filaments, print_profile, printer = _parse_project_settings(zf)
        has_gcode = _has_gcode(zf)
        _extract_thumbnails(zf, plates)

    return ThreeMFInfo(
        plates=plates,
        filaments=filaments,
        print_profile=print_profile,
        printer=printer,
        has_gcode=has_gcode,
    )
