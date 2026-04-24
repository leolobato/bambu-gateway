"""Tests for parse_3mf — focuses on graceful handling of non-Bambu archives."""

from __future__ import annotations

import io
import zipfile

from app.parse_3mf import parse_3mf


def _zip_bytes(files: dict[str, str | bytes]) -> bytes:
    """Build an in-memory zip containing the given files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            data = content.encode() if isinstance(content, str) else content
            zf.writestr(name, data)
    return buf.getvalue()


def test_parse3mf_genericArchive_returnsDefaultPlate():
    """A 3MF without Bambu's Metadata/model_settings.config still parses."""
    data = _zip_bytes({
        "3D/3dmodel.model": "<model></model>",
        "[Content_Types].xml": "<types/>",
    })

    info = parse_3mf(data)

    assert len(info.plates) == 1
    assert info.plates[0].id == 1
    assert info.plates[0].objects == []
    assert info.filaments == []
    assert info.has_gcode is False


def test_parse3mf_missingProjectSettings_returnsEmptyDefaults():
    """Missing project_settings.config means no filaments and empty profiles."""
    data = _zip_bytes({
        "3D/3dmodel.model": "<model></model>",
    })

    info = parse_3mf(data)

    assert info.filaments == []
    assert info.print_profile.print_settings_id == ""
    assert info.printer.printer_settings_id == ""
