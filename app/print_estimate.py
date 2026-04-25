"""Extract slicer print estimates from sliced 3MF archives."""

from __future__ import annotations

import logging
import zipfile
from io import BytesIO
from xml.etree import ElementTree

from app.models import PrintEstimate

logger = logging.getLogger(__name__)


def extract_print_estimate(file_data: bytes) -> PrintEstimate | None:
    """Read Bambu/Orca slice metadata from a sliced 3MF, if present."""
    try:
        with zipfile.ZipFile(BytesIO(file_data)) as zf:
            raw = zf.read("Metadata/slice_info.config")
    except (KeyError, zipfile.BadZipFile, OSError):
        return None

    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        logger.warning("Failed to parse Metadata/slice_info.config")
        return None

    total_seconds = _first_int_metadata(root, "prediction")
    metadata_weight = _first_float_metadata(root, "weight")
    filament_meters = 0.0
    filament_grams = 0.0

    for filament in root.findall(".//filament"):
        filament_meters += _float_attr(filament, "used_m") or 0.0
        filament_grams += _float_attr(filament, "used_g") or 0.0

    total_mm = filament_meters * 1000 if filament_meters > 0 else None
    total_g = filament_grams if filament_grams > 0 else metadata_weight

    estimate = PrintEstimate(
        total_filament_millimeters=total_mm,
        total_filament_grams=total_g,
        model_filament_millimeters=total_mm,
        model_filament_grams=total_g,
        model_print_seconds=total_seconds,
        total_seconds=total_seconds,
    )
    return None if estimate.is_empty else estimate


def _first_int_metadata(root: ElementTree.Element, key: str) -> int | None:
    value = _first_metadata(root, key)
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _first_float_metadata(root: ElementTree.Element, key: str) -> float | None:
    value = _first_metadata(root, key)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _first_metadata(root: ElementTree.Element, key: str) -> str | None:
    for node in root.findall(".//metadata"):
        if node.attrib.get("key") == key:
            return node.attrib.get("value")
    return None


def _float_attr(node: ElementTree.Element, attr: str) -> float | None:
    value = node.attrib.get(attr)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
