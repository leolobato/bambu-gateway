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
) -> tuple[list[PlateInfo], dict[str, set[int]]]:
    """Parse `Metadata/model_settings.config` for plates and per-object
    declared extruder indices.

    Returns `(plates, object_extruders)` where `object_extruders[obj_id]`
    is the set of 0-based filament indices declared via `extruder=N`
    metadata on that object or any of its parts (Bambu stores extruder
    1-based: `extruder=6` means filament index 5).

    Generic 3MFs (Thingiverse, MakerWorld, non-Bambu slicers) lack this
    Bambu-specific metadata file. In that case fall back to a single
    empty plate so the file can still be sliced — the slicer assigns
    objects to plate 1 by default — and an empty extruder map signalling
    "we don't know what's used".
    """
    if "Metadata/model_settings.config" not in zf.namelist():
        return [PlateInfo(id=1, name="", objects=[])], {}
    raw = zf.read("Metadata/model_settings.config").decode()
    root = ET.fromstring(raw)

    object_names: dict[str, str] = {}
    object_extruders: dict[str, set[int]] = {}

    def _record(target: set[int], value: str) -> None:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return
        if n >= 1:
            target.add(n - 1)

    for obj in root.findall("object"):
        obj_id = obj.get("id")
        if not obj_id:
            continue
        extruders: set[int] = set()
        for meta in obj.findall("metadata"):
            key = meta.get("key")
            if key == "name":
                object_names[obj_id] = meta.get("value", "")
            elif key == "extruder":
                _record(extruders, meta.get("value", ""))
        for part in obj.findall("part"):
            for meta in part.findall("metadata"):
                if meta.get("key") == "extruder":
                    _record(extruders, meta.get("value", ""))
        object_extruders[obj_id] = extruders

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
                        PlateObject(id=oid, name=object_names.get(oid, f"object_{oid}"))
                    )
                    break

        plates.append(PlateInfo(id=plate_id, name=plate_name, objects=plate_objects))

    return plates, object_extruders


_NS_CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
_NS_PROD = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"


def _parse_object_components(zf: zipfile.ZipFile) -> dict[str, set[str]]:
    """Map each top-level object id in `3D/3dmodel.model` to the zip paths
    where its geometry actually lives.

    Bambu/Orca's aggregator typically declares each top-level `<object>`
    as a wrapper around `<components>` whose `<component p:path="…">`
    attributes reference per-object files under `3D/Objects/`. Older or
    smaller files may put `<mesh>` directly under `<object>`, in which
    case the geometry — and any `paint_color` attributes — live in the
    aggregator itself.

    Knowing this mapping lets per-plate scans only stream the `.model`
    files that the plate's objects actually reference, instead of every
    mesh in the archive.
    """
    if "3D/3dmodel.model" not in zf.namelist():
        return {}
    raw = zf.read("3D/3dmodel.model")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return {}

    resources = root.find(f"{_NS_CORE}resources")
    if resources is None:
        return {}

    result: dict[str, set[str]] = {}
    for obj in resources.findall(f"{_NS_CORE}object"):
        oid = obj.get("id")
        if not oid:
            continue
        components = obj.find(f"{_NS_CORE}components")
        if components is None:
            result[oid] = {"3D/3dmodel.model"}
            continue
        paths: set[str] = set()
        for comp in components.findall(f"{_NS_CORE}component"):
            path = comp.get(f"{_NS_PROD}path")
            paths.add(path.lstrip("/") if path else "3D/3dmodel.model")
        result[oid] = paths or {"3D/3dmodel.model"}

    return result


def _get_arr(settings: dict, key: str, index: int, default: str = "") -> str:
    """Safely get index from a settings array value."""
    arr = settings.get(key, [])
    if isinstance(arr, list) and index < len(arr):
        return arr[index]
    return default


def _parse_project_settings(
    zf: zipfile.ZipFile,
) -> tuple[list[FilamentInfo], PrintProfileInfo, PrinterInfo, str]:
    """Parse Metadata/project_settings.config for filaments, profile, printer.

    Generic 3MFs without Bambu's project settings still slice fine — the user
    will pick machine/process manually and there are no project filaments to
    map to AMS trays.
    """
    if "Metadata/project_settings.config" not in zf.namelist():
        return [], PrintProfileInfo(), PrinterInfo(), ""
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

    bed_type = settings.get("curr_bed_type", "") or ""

    return filaments, print_profile, printer, bed_type


def _has_gcode(zf: zipfile.ZipFile) -> bool:
    """Check if the archive contains sliced gcode."""
    return any(
        name.startswith("Metadata/plate_") and name.endswith(".gcode")
        for name in zf.namelist()
    )


_PAINT_COLOR_NEEDLE = b'paint_color="'
_PAINT_COLOR_CHUNK = 1 << 20  # 1 MiB; 3D model files routinely run 100+ MB


def _file_has_paint_color(zf: zipfile.ZipFile, path: str) -> bool:
    """Stream-scan a single `.model` file for `paint_color="`.

    Decoding OrcaSlicer's bit-packed `paint_color` triangle tree to
    recover the exact extruder set is non-trivial, so any presence of
    the attribute is treated as "all declared filaments may be in
    play". Streaming with chunked reads keeps memory bounded for the
    100+ MB mesh files Bambu produces.
    """
    if path not in zf.namelist():
        return False
    tail = b""
    with zf.open(path) as fp:
        while True:
            chunk = fp.read(_PAINT_COLOR_CHUNK)
            if not chunk:
                return False
            if _PAINT_COLOR_NEEDLE in tail + chunk:
                return True
            tail = chunk[-len(_PAINT_COLOR_NEEDLE):]


def _extract_thumbnails(zf: zipfile.ZipFile, plates: list[PlateInfo]) -> None:
    """Attach base64-encoded plate thumbnails to PlateInfo objects in-place."""
    for plate in plates:
        path = f"Metadata/plate_{plate.id}.png"
        if path in zf.namelist():
            raw = zf.read(path)
            plate.thumbnail = "data:image/png;base64," + base64.b64encode(raw).decode()


def parse_3mf(data: bytes, plate_id: int | None = None) -> ThreeMFInfo:
    """Parse a Bambu 3MF file from bytes and return structured metadata.

    When `plate_id` is None (default), every plate's `used_filament_indices`
    is computed and `f.used` reflects the union across all plates — the
    "is this filament referenced anywhere in the file" flag clients have
    relied on historically.

    When `plate_id` matches a plate, only that plate's
    `used_filament_indices` is populated (skipping `paint_color` scans for
    `.model` files no other plate needs) and `f.used` reflects just that
    plate. If `plate_id` is given but doesn't match any plate, behaviour
    falls back to the all-plates path so callers don't get an empty result.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        plates, object_extruders = _parse_model_settings(zf)
        filaments, print_profile, printer, bed_type = _parse_project_settings(zf)
        has_gcode = _has_gcode(zf)
        _extract_thumbnails(zf, plates)
        object_files = _parse_object_components(zf)

        paint_cache: dict[str, bool] = {}

        def any_painted(paths: set[str]) -> bool:
            for p in paths:
                cached = paint_cache.get(p)
                if cached is None:
                    cached = _file_has_paint_color(zf, p)
                    paint_cache[p] = cached
                if cached:
                    return True
            return False

        if plate_id is not None:
            selected = [p for p in plates if p.id == plate_id]
            plates_to_compute = selected or plates
        else:
            plates_to_compute = plates

        has_extruder_data = any(extr for extr in object_extruders.values())
        all_indices = list(range(len(filaments)))
        any_face_painting_seen = False

        for plate in plates_to_compute:
            obj_ids = [obj.id for obj in plate.objects]
            declared: set[int] = set()
            relevant_files: set[str] = set()
            for oid in obj_ids:
                declared |= object_extruders.get(oid, set())
                relevant_files |= object_files.get(oid, {"3D/3dmodel.model"})

            if relevant_files and any_painted(relevant_files):
                any_face_painting_seen = True
                plate.used_filament_indices = list(all_indices)
            elif has_extruder_data:
                plate.used_filament_indices = sorted(declared)
            # else: no info anywhere — leave as None so clients fall back to
            # "show every declared filament".

        # Mirror the per-plate result onto the global `f.used` flag for
        # back-compat. Skip when there's no info anywhere so the default
        # `used=True` is preserved (generic 3MFs without metadata).
        if has_extruder_data or any_face_painting_seen:
            union: set[int] = set()
            for plate in plates_to_compute:
                if plate.used_filament_indices is not None:
                    union |= set(plate.used_filament_indices)
            for f in filaments:
                f.used = f.index in union

    return ThreeMFInfo(
        plates=plates,
        filaments=filaments,
        print_profile=print_profile,
        printer=printer,
        has_gcode=has_gcode,
        bed_type=bed_type,
    )
