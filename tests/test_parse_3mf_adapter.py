"""Unit tests for _adapt() — the inspect→ThreeMFInfo mapping."""
from __future__ import annotations

from app.parse_3mf import _adapt


def _fake_inspect(*, sliced: bool, plates: list[dict], filaments: list[dict]) -> dict:
    return {
        "schema_version": 2,
        "is_sliced": sliced,
        "plate_count": len(plates),
        "plates": plates,
        "filaments": filaments,
        "estimate": None,
        "bbox": None,
        "printer_model": "Bambu Lab A1 mini",
        "printer_variant": "0.4",
        "curr_bed_type": "Textured PEI Plate",
        "thumbnail_urls": [],
        "use_set_per_plate": {},
    }


def test_adapter_unsliced_single_plate():
    insp = _fake_inspect(
        sliced=False,
        plates=[{"id": 1, "name": "", "used_filament_indices": [0]}],
        filaments=[
            {"slot": 0, "type": "PLA", "color": "#FFFFFF",
             "filament_id": "GFA00", "settings_id": "Bambu PLA Basic"},
        ],
    )
    info = _adapt(insp, plate_id=None, thumbnails={1: "BASE64"})
    assert info.has_gcode is False
    assert info.bed_type == "Textured PEI Plate"
    assert info.printer.printer_model == "Bambu Lab A1 mini"
    assert info.printer.nozzle_diameter == "0.4"
    assert len(info.plates) == 1
    assert info.plates[0].thumbnail == "BASE64"
    assert info.plates[0].used_filament_indices == [0]
    assert info.filaments[0].used is True


def test_adapter_filters_used_per_plate_id():
    insp = _fake_inspect(
        sliced=True,
        plates=[
            {"id": 1, "name": "", "used_filament_indices": [0]},
            {"id": 2, "name": "", "used_filament_indices": [1]},
        ],
        filaments=[
            {"slot": 0, "type": "PLA", "color": "#FFF", "filament_id": "GFA00", "settings_id": "A"},
            {"slot": 1, "type": "PETG", "color": "#000", "filament_id": "GFB00", "settings_id": "B"},
        ],
    )
    info = _adapt(insp, plate_id=2, thumbnails={})
    assert [f.used for f in info.filaments] == [False, True]


def test_adapter_unknown_per_plate_falls_back_to_all():
    insp = _fake_inspect(
        sliced=False,
        plates=[{"id": 1, "name": "", "used_filament_indices": None}],
        filaments=[
            {"slot": 0, "type": "PLA", "color": "#FFF", "filament_id": "GFA00", "settings_id": "A"},
            {"slot": 1, "type": "PETG", "color": "#000", "filament_id": "GFB00", "settings_id": "B"},
        ],
    )
    info = _adapt(insp, plate_id=None, thumbnails={})
    assert all(f.used for f in info.filaments)
