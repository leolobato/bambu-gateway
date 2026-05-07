"""Unit tests for ProcessModifications model + ThreeMFInfo extension."""
from __future__ import annotations

from app.models import ProcessModifications, ThreeMFInfo


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
