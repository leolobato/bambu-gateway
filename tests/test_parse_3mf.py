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


def test_parse3mf_marksOnlyExtruder_referenced_filaments_as_used():
    """If only extruder=6 is referenced, only filament index 5 is `used`."""
    project_settings = (
        '{"filament_type": ["PLA", "PLA", "PLA", "PLA", "PLA", "PLA"], '
        '"filament_settings_id": ["a", "b", "c", "d", "e", "target"]}'
    )
    model_settings = """\
<config>
  <object id="2">
    <metadata key="name" value="part"/>
    <metadata key="extruder" value="6"/>
    <part id="1" subtype="normal_part"/>
  </object>
  <plate>
    <metadata key="plater_id" value="1"/>
    <model_instance>
      <metadata key="object_id" value="2"/>
    </model_instance>
  </plate>
</config>
"""
    data = _zip_bytes({
        "3D/3dmodel.model": "<model></model>",
        "Metadata/project_settings.config": project_settings,
        "Metadata/model_settings.config": model_settings,
    })

    info = parse_3mf(data)

    used = {f.index for f in info.filaments if f.used}
    assert used == {5}
    # All other declared filaments are present but not flagged as used.
    assert len(info.filaments) == 6
    assert all(f.used is False for f in info.filaments if f.index != 5)


def test_parse3mf_genericModelSettings_falls_back_to_all_used():
    """When model_settings.config is missing, every declared filament stays `used=True`."""
    project_settings = (
        '{"filament_type": ["PLA", "PLA"], '
        '"filament_settings_id": ["a", "b"]}'
    )
    data = _zip_bytes({
        "3D/3dmodel.model": "<model></model>",
        "Metadata/project_settings.config": project_settings,
    })

    info = parse_3mf(data)

    assert all(f.used for f in info.filaments)
