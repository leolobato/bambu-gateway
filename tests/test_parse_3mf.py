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


def test_parse3mf_paintColor_marksAllDeclaredFilamentsUsed():
    """MMU face-painting (paint_color) drives extruders beyond the declared
    object/part metadata, so every declared filament must be marked used."""
    project_settings = (
        '{"filament_type": ["PLA", "PLA", "PLA", "PLA"], '
        '"filament_settings_id": ["a", "b", "c", "d"]}'
    )
    model_settings = """\
<config>
  <object id="2">
    <metadata key="name" value="bird"/>
    <metadata key="extruder" value="4"/>
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
    model_xml = (
        '<model><resources><object id="1" type="model"><mesh><triangles>'
        '<triangle v1="0" v2="1" v3="2" paint_color="8"/>'
        '</triangles></mesh></object></resources></model>'
    )
    data = _zip_bytes({
        "3D/3dmodel.model": model_xml,
        "Metadata/project_settings.config": project_settings,
        "Metadata/model_settings.config": model_settings,
    })

    info = parse_3mf(data)

    assert {f.index for f in info.filaments if f.used} == {0, 1, 2, 3}


def test_parse3mf_paintColor_streamingAcrossChunkBoundary_isDetected():
    """The chunked scanner must catch a `paint_color=` token that straddles
    a 1 MiB read boundary."""
    project_settings = (
        '{"filament_type": ["PLA", "PLA"], '
        '"filament_settings_id": ["a", "b"]}'
    )
    model_settings = """\
<config>
  <object id="2">
    <metadata key="extruder" value="2"/>
  </object>
  <plate>
    <metadata key="plater_id" value="1"/>
    <model_instance>
      <metadata key="object_id" value="2"/>
    </model_instance>
  </plate>
</config>
"""
    chunk = 1 << 20
    needle = 'paint_color="4"'
    prefix = "<model>" + ("x" * (chunk - 5))  # places the needle straddling the 1 MiB boundary
    model_xml = prefix + needle + "</model>"
    data = _zip_bytes({
        "3D/3dmodel.model": model_xml,
        "Metadata/project_settings.config": project_settings,
        "Metadata/model_settings.config": model_settings,
    })

    info = parse_3mf(data)

    assert {f.index for f in info.filaments if f.used} == {0, 1}
