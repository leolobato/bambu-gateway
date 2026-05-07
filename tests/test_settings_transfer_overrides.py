"""Unit tests for SettingsTransferInfo.process_overrides_applied."""
from __future__ import annotations

from app.models import ProcessOverrideApplied, SettingsTransferInfo


def test_process_override_applied_fields():
    o = ProcessOverrideApplied(key="layer_height", value="0.16", previous="0.20")
    assert o.key == "layer_height"
    assert o.value == "0.16"
    assert o.previous == "0.20"


def test_settings_transfer_info_carries_overrides_applied():
    sti = SettingsTransferInfo(
        status="applied",
        process_overrides_applied=[
            ProcessOverrideApplied(key="layer_height", value="0.16", previous="0.20"),
        ],
    )
    dumped = sti.model_dump()
    assert dumped["process_overrides_applied"] == [
        {"key": "layer_height", "value": "0.16", "previous": "0.20"},
    ]


def test_settings_transfer_info_defaults_overrides_empty():
    sti = SettingsTransferInfo(status="applied")
    assert sti.process_overrides_applied == []
