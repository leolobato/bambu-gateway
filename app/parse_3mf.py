"""Parse Bambu Lab 3MF files and extract metadata."""

from __future__ import annotations

import base64
import io
import json
import logging
import re
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


def _flatten_3mf(data: bytes) -> bytes:
    """Flatten multi-file 3MF production-extension models into a single model file.

    OrcaSlicer CLI crashes (SIGSEGV) when re-slicing 3MF files that use the 3MF
    production extension (``requiredextensions="p"``) with external model references
    via ``p:path``. This rewrites such files into a single inline model.

    Returns the original bytes if no flattening is needed.
    """
    _3MF_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    _PROD_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        main_path = "3D/3dmodel.model"
        if main_path not in zf.namelist():
            return data

        main_xml = zf.read(main_path).decode()

        # Quick check: does this file use the production extension with external refs?
        if f"{{{_PROD_NS}}}path" not in main_xml and "p:path" not in main_xml:
            return data

        root = ET.fromstring(main_xml)
        ns = {"m": _3MF_NS, "p": _PROD_NS}

        # Collect all external mesh data by (path, objectid)
        external_meshes: dict[tuple[str, str], str] = {}
        for comp_parent in root.findall(".//m:object/m:components/m:component", ns):
            p_path = comp_parent.get(f"{{{_PROD_NS}}}path", "")
            obj_id = comp_parent.get("objectid", "")
            if not p_path or not obj_id:
                continue
            # Normalize path (strip leading /)
            rel_path = p_path.lstrip("/")
            key = (rel_path, obj_id)
            if key in external_meshes:
                continue
            if rel_path not in zf.namelist():
                logger.warning("Referenced model %s not found in 3MF", rel_path)
                return data
            ext_xml = zf.read(rel_path).decode()
            # Extract the <mesh>...</mesh> block for the target object id
            mesh_match = re.search(r"(<mesh>.*?</mesh>)", ext_xml, re.DOTALL)
            if mesh_match:
                external_meshes[key] = mesh_match.group(1)

        if not external_meshes:
            return data

        logger.info(
            "Flattening multi-file 3MF: %d external mesh(es)", len(external_meshes)
        )

        # Build new model with inline meshes
        # Collect build items with their transforms
        build_items: list[dict[str, str]] = []
        build = root.find("m:build", ns)
        if build is not None:
            for item in build.findall("m:item", ns):
                build_items.append({
                    "objectid": item.get("objectid", ""),
                    "transform": item.get("transform", ""),
                })

        # For each build item's object, resolve component -> external mesh
        object_meshes: dict[str, str] = {}
        for obj in root.findall("m:resources/m:object", ns):
            obj_id = obj.get("id", "")
            for comp in obj.findall("m:components/m:component", ns):
                p_path = comp.get(f"{{{_PROD_NS}}}path", "").lstrip("/")
                comp_obj_id = comp.get("objectid", "")
                key = (p_path, comp_obj_id)
                if key in external_meshes:
                    object_meshes[obj_id] = external_meshes[key]

        if not object_meshes:
            return data

        # Build clean model XML
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'
            ' unit="millimeter">',
            "  <resources>",
        ]
        for obj_id, mesh_xml in object_meshes.items():
            lines.append(f'    <object id="{obj_id}" type="model">')
            lines.append(f"      {mesh_xml}")
            lines.append("    </object>")
        lines.append("  </resources>")
        lines.append("  <build>")
        for item in build_items:
            attrs = f'objectid="{item["objectid"]}"'
            if item["transform"]:
                attrs += f' transform="{item["transform"]}"'
            lines.append(f"    <item {attrs} />")
        lines.append("  </build>")
        lines.append("</model>")

        new_model = "\n".join(lines)

        # Rebuild the ZIP without external model files and rels.
        # model_settings.config is preserved so the slicer can extract
        # individual plates from multi-plate files.
        external_files = {p.lstrip("/") for (p, _) in external_meshes}
        skip_files = external_files | {
            "3D/_rels/3dmodel.model.rels",
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf_out:
            for item in zf.infolist():
                if item.filename == main_path:
                    zf_out.writestr(item, new_model)
                elif item.filename in skip_files:
                    continue
                else:
                    zf_out.writestr(item, zf.read(item.filename))

        return buf.getvalue()

    return data


def sanitize_3mf(data: bytes) -> bytes:
    """Fix issues in a 3MF that prevent OrcaSlicer from slicing it.

    Applies the following fixes:
    - Flattens multi-file production-extension models (prevents SIGSEGV)
    - Clamps out-of-range parameter values that OrcaSlicer rejects
    - Strips machine-specific settings that conflict with the target machine

    Returns the original bytes if no changes are needed, or a patched copy.
    """
    data = _flatten_3mf(data)

    settings_file = "Metadata/project_settings.config"

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if settings_file not in zf.namelist():
            return data

        raw = zf.read(settings_file).decode()
        settings = json.loads(raw)

        changed = False

        # Clamp out-of-range values
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

        # Strip machine-specific settings that conflict with re-slicing
        # for a different machine (prevents bed size mismatch crashes)
        _MACHINE_REMOVE = {
            "printable_area", "printable_height",
            "bed_exclude_area", "bed_custom_model", "bed_custom_texture",
            "upward_compatible_machine",
        }
        for key in list(settings.keys()):
            if key in _MACHINE_REMOVE or key.startswith("machine_"):
                del settings[key]
                changed = True

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
