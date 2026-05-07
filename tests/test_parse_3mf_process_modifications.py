"""Unit tests for ProcessModifications model + ThreeMFInfo extension."""
from __future__ import annotations

from app.models import ProcessModifications, ThreeMFInfo
from app.parse_3mf import _adapt


def test_process_modifications_defaults_to_empty():
    pm = ProcessModifications()
    assert pm.process_setting_id == ""
    assert pm.modified_keys == []
    assert pm.values == {}


def test_three_mf_info_carries_process_modifications():
    info = ThreeMFInfo()
    assert isinstance(info.process_modifications, ProcessModifications)
    assert info.process_modifications.process_setting_id == ""


def test_process_modifications_round_trips():
    pm = ProcessModifications(
        process_setting_id="Custom 0.20mm Standard",
        modified_keys=["layer_height", "wall_loops"],
        values={"layer_height": "0.16", "wall_loops": "3"},
    )
    info = ThreeMFInfo(process_modifications=pm)
    dumped = info.model_dump()
    assert dumped["process_modifications"]["process_setting_id"] == "Custom 0.20mm Standard"
    assert dumped["process_modifications"]["modified_keys"] == ["layer_height", "wall_loops"]
    assert dumped["process_modifications"]["values"] == {"layer_height": "0.16", "wall_loops": "3"}


def _fake_inspect_with_pm(pm: dict | None) -> dict:
    out = {
        "schema_version": 4,
        "is_sliced": False,
        "plate_count": 1,
        "plates": [{"id": 1, "name": "", "used_filament_indices": [0]}],
        "filaments": [
            {"slot": 0, "type": "PLA", "color": "#FFF",
             "filament_id": "GFA00", "settings_id": "Bambu PLA Basic"},
        ],
        "estimate": None,
        "bbox": None,
        "printer_model": "Bambu Lab A1 mini",
        "printer_variant": "0.4",
        "printer_settings_id": "Bambu Lab A1 mini 0.4 nozzle",
        "print_settings_id": "Custom 0.20mm Standard",
        "layer_height": "0.16",
        "curr_bed_type": "Textured PEI Plate",
        "thumbnail_urls": [],
        "use_set_per_plate": {},
    }
    if pm is not None:
        out["process_modifications"] = pm
    return out


def test_adapter_populates_process_modifications():
    insp = _fake_inspect_with_pm({
        "process_setting_id": "Custom 0.20mm Standard",
        "modified_keys": ["layer_height", "wall_loops"],
        "values": {"layer_height": "0.16", "wall_loops": "3"},
    })
    info = _adapt(insp, plate_id=None, thumbnails={})
    assert info.process_modifications.process_setting_id == "Custom 0.20mm Standard"
    assert info.process_modifications.modified_keys == ["layer_height", "wall_loops"]
    assert info.process_modifications.values == {"layer_height": "0.16", "wall_loops": "3"}


def test_adapter_handles_empty_process_modifications():
    insp = _fake_inspect_with_pm({
        "process_setting_id": "",
        "modified_keys": [],
        "values": {},
    })
    info = _adapt(insp, plate_id=None, thumbnails={})
    assert info.process_modifications.process_setting_id == ""
    assert info.process_modifications.modified_keys == []
    assert info.process_modifications.values == {}


def test_adapter_handles_missing_process_modifications():
    """Pre-rev-41 slicer: field not in the inspect payload at all."""
    insp = _fake_inspect_with_pm(None)
    info = _adapt(insp, plate_id=None, thumbnails={})
    assert info.process_modifications.process_setting_id == ""
    assert info.process_modifications.modified_keys == []
    assert info.process_modifications.values == {}
