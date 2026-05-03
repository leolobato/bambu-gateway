"""Tests for parse_3mf — focuses on graceful handling of non-Bambu archives."""

from __future__ import annotations

import io
import zipfile

from app.parse_3mf_legacy import parse_3mf


def _zip_bytes(files: dict[str, str | bytes]) -> bytes:
    """Build an in-memory zip containing the given files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            data = content.encode() if isinstance(content, str) else content
            zf.writestr(name, data)
    return buf.getvalue()


def _aggregator_with_components(object_to_path: dict[str, str]) -> str:
    """Build a `3D/3dmodel.model` aggregator that maps each top-level object
    to a per-object file via `<components><component p:path="…"/>`. Real
    Bambu/Orca files use this layout; tests need it for `paint_color` scans
    to be scoped to the right per-object `.model`."""
    items = "".join(
        (
            f'<object id="{oid}" type="model">'
            f'<components><component p:path="/{path}" objectid="1"/></components>'
            "</object>"
        )
        for oid, path in object_to_path.items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'
        ' xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">'
        f"<resources>{items}</resources>"
        "</model>"
    )


def _per_object_with_paint_color() -> str:
    return (
        '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        '<resources><object id="1" type="model"><mesh><triangles>'
        '<triangle v1="0" v2="1" v3="2" paint_color="8"/>'
        "</triangles></mesh></object></resources></model>"
    )


def _per_object_plain() -> str:
    return (
        '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        '<resources><object id="1" type="model"><mesh><triangles>'
        '<triangle v1="0" v2="1" v3="2"/>'
        "</triangles></mesh></object></resources></model>"
    )


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
    assert len(info.filaments) == 6
    assert all(f.used is False for f in info.filaments if f.index != 5)
    # And the per-plate map agrees.
    assert info.plates[0].used_filament_indices == [5]


def test_parse3mf_genericModelSettings_falls_back_to_all_used():
    """When model_settings.config is missing, every declared filament stays `used=True`
    and `used_filament_indices` is left as None so clients show everything."""
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
    assert info.plates[0].used_filament_indices is None


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
    # Inline mesh: aggregator declares object 2 with paint_color directly.
    model_xml = (
        '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        '<resources><object id="2" type="model"><mesh><triangles>'
        '<triangle v1="0" v2="1" v3="2" paint_color="8"/>'
        "</triangles></mesh></object></resources></model>"
    )
    data = _zip_bytes({
        "3D/3dmodel.model": model_xml,
        "Metadata/project_settings.config": project_settings,
        "Metadata/model_settings.config": model_settings,
    })

    info = parse_3mf(data)

    assert {f.index for f in info.filaments if f.used} == {0, 1, 2, 3}
    assert info.plates[0].used_filament_indices == [0, 1, 2, 3]


def test_parse3mf_paintColor_inSplitObjectModel_marksAllDeclaredFilamentsUsed():
    """Bambu/Orca commonly split geometry into per-object files referenced
    from the aggregator via `<components p:path="…"/>`. Face-painting in the
    per-object file must still be detected for the plate's filaments."""
    project_settings = (
        '{"filament_type": ["PLA", "PLA", "PLA"], '
        '"filament_settings_id": ["a", "b", "c"]}'
    )
    model_settings = """\
<config>
  <object id="2">
    <metadata key="extruder" value="1"/>
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
        "3D/3dmodel.model": _aggregator_with_components({
            "2": "3D/Objects/object_2.model",
        }),
        "3D/Objects/object_2.model": _per_object_with_paint_color(),
        "Metadata/project_settings.config": project_settings,
        "Metadata/model_settings.config": model_settings,
    })

    info = parse_3mf(data)

    assert {f.index for f in info.filaments if f.used} == {0, 1, 2}
    assert info.plates[0].used_filament_indices == [0, 1, 2]


def test_parse3mf_perPlate_facePaintingOnOnePlateDoesNotBleed():
    """Reproduces the Windmill bug: plate A only declares one extruder and
    has no face-painted geometry; plate B has a different object with
    paint_color in its per-object file. The two plates must report
    independent `used_filament_indices` — face-painting on plate B must not
    bleed onto plate A's filament list."""
    project_settings = (
        '{"filament_type": ["PLA", "PLA", "PLA"], '
        '"filament_settings_id": ["a", "b", "c"]}'
    )
    model_settings = """\
<config>
  <object id="10">
    <metadata key="name" value="plain"/>
    <metadata key="extruder" value="2"/>
  </object>
  <object id="20">
    <metadata key="name" value="painted"/>
    <metadata key="extruder" value="1"/>
  </object>
  <plate>
    <metadata key="plater_id" value="1"/>
    <model_instance>
      <metadata key="object_id" value="10"/>
    </model_instance>
  </plate>
  <plate>
    <metadata key="plater_id" value="2"/>
    <model_instance>
      <metadata key="object_id" value="20"/>
    </model_instance>
  </plate>
</config>
"""
    data = _zip_bytes({
        "3D/3dmodel.model": _aggregator_with_components({
            "10": "3D/Objects/object_10.model",
            "20": "3D/Objects/object_20.model",
        }),
        "3D/Objects/object_10.model": _per_object_plain(),
        "3D/Objects/object_20.model": _per_object_with_paint_color(),
        "Metadata/project_settings.config": project_settings,
        "Metadata/model_settings.config": model_settings,
    })

    info = parse_3mf(data)
    by_id = {p.id: p for p in info.plates}

    assert by_id[1].used_filament_indices == [1]
    assert by_id[2].used_filament_indices == [0, 1, 2]
    # The global `f.used` flag is the union across plates.
    assert {f.index for f in info.filaments if f.used} == {0, 1, 2}


def test_parse3mf_plateIdArg_scopesUsedFlagToThatPlate():
    """When the caller passes `plate_id`, `f.used` reflects only that plate
    even if other plates would otherwise mark different filaments used.
    Other plates' `used_filament_indices` stays `None` (not computed)."""
    project_settings = (
        '{"filament_type": ["PLA", "PLA", "PLA"], '
        '"filament_settings_id": ["a", "b", "c"]}'
    )
    model_settings = """\
<config>
  <object id="10">
    <metadata key="extruder" value="2"/>
  </object>
  <object id="20">
    <metadata key="extruder" value="3"/>
  </object>
  <plate>
    <metadata key="plater_id" value="1"/>
    <model_instance>
      <metadata key="object_id" value="10"/>
    </model_instance>
  </plate>
  <plate>
    <metadata key="plater_id" value="2"/>
    <model_instance>
      <metadata key="object_id" value="20"/>
    </model_instance>
  </plate>
</config>
"""
    data = _zip_bytes({
        "3D/3dmodel.model": _aggregator_with_components({
            "10": "3D/Objects/object_10.model",
            "20": "3D/Objects/object_20.model",
        }),
        "3D/Objects/object_10.model": _per_object_plain(),
        "3D/Objects/object_20.model": _per_object_plain(),
        "Metadata/project_settings.config": project_settings,
        "Metadata/model_settings.config": model_settings,
    })

    info = parse_3mf(data, plate_id=1)
    by_id = {p.id: p for p in info.plates}

    assert by_id[1].used_filament_indices == [1]
    assert by_id[2].used_filament_indices is None  # not computed
    # f.used reflects only plate 1.
    assert {f.index for f in info.filaments if f.used} == {1}


def test_parse3mf_plateIdArg_unknownPlate_fallsBackToAllPlates():
    """Asking for a plate the file doesn't have shouldn't return an empty
    used set — fall back to computing every plate so the response is at
    least useful."""
    project_settings = (
        '{"filament_type": ["PLA", "PLA"], '
        '"filament_settings_id": ["a", "b"]}'
    )
    model_settings = """\
<config>
  <object id="10">
    <metadata key="extruder" value="2"/>
  </object>
  <plate>
    <metadata key="plater_id" value="1"/>
    <model_instance>
      <metadata key="object_id" value="10"/>
    </model_instance>
  </plate>
</config>
"""
    data = _zip_bytes({
        "3D/3dmodel.model": _aggregator_with_components({
            "10": "3D/Objects/object_10.model",
        }),
        "3D/Objects/object_10.model": _per_object_plain(),
        "Metadata/project_settings.config": project_settings,
        "Metadata/model_settings.config": model_settings,
    })

    info = parse_3mf(data, plate_id=99)

    assert info.plates[0].used_filament_indices == [1]
    assert {f.index for f in info.filaments if f.used} == {1}


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
    # Inline mesh under <object id="2"> places paint_color directly in
    # 3D/3dmodel.model — the scanner must find it across the 1 MiB boundary.
    header = (
        '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        '<resources><object id="2" type="model"><mesh>'
    )
    padding = "x" * max(0, chunk - len(header) - 5)
    model_xml = header + padding + needle + "</mesh></object></resources></model>"
    data = _zip_bytes({
        "3D/3dmodel.model": model_xml,
        "Metadata/project_settings.config": project_settings,
        "Metadata/model_settings.config": model_settings,
    })

    info = parse_3mf(data)

    assert {f.index for f in info.filaments if f.used} == {0, 1}
