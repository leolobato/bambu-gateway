from app.filament_selection import (
    build_ams_mapping,
    build_slicer_filament_payload,
    extract_selected_tray_slots,
)


def test_extract_returns_empty_for_list_payload():
    assert extract_selected_tray_slots(["GFL99"]) == {}


def test_extract_returns_empty_for_none():
    assert extract_selected_tray_slots(None) == {}


def test_extract_picks_tray_slots():
    payload = {"0": {"tray_slot": 2}, "1": {"tray_slot": 0}}
    assert extract_selected_tray_slots(payload) == {0: 2, 1: 0}


def test_extract_skips_non_int_tray_slot():
    payload = {"0": {"tray_slot": "2"}, "1": {"tray_slot": 0}}
    assert extract_selected_tray_slots(payload) == {1: 0}


def test_build_ams_mapping_with_count():
    payload = {"0": {"tray_slot": 2}, "1": {"tray_slot": 0}}
    mapping, use = build_ams_mapping(payload, project_filament_count=3)
    assert mapping == [2, 0, -1]
    assert use is True


def test_build_ams_mapping_no_selection():
    mapping, use = build_ams_mapping(["GFL99"])
    assert mapping is None
    assert use is False


def test_build_ams_mapping_derives_count_from_max_index():
    payload = {"0": {"tray_slot": 1}, "2": {"tray_slot": 3}}
    mapping, use = build_ams_mapping(payload)
    assert mapping == [1, -1, 3]
    assert use is True


# build_slicer_filament_payload — unused-index padding


def test_padding_skipped_when_used_indices_not_provided():
    project_ids = [f"f{i}" for i in range(12)]
    overrides = '{"5": {"profile_setting_id": "Bambu PLA Basic @BBL A1M", "tray_slot": 4}}'
    payload, err = build_slicer_filament_payload(
        project_ids,
        overrides,
        tray_profile_map={4: ""},
    )
    assert err is None
    # Only the user's override is in the payload — unchanged behavior.
    assert payload == {
        "5": {"profile_setting_id": "Bambu PLA Basic @BBL A1M", "tray_slot": 4},
    }


def test_padding_fills_unused_indices_with_override_profile():
    """Whistle-3MF style: 12 declared filaments, only index 5 actually used."""
    project_ids = [f"f{i}" for i in range(12)]
    overrides = '{"5": {"profile_setting_id": "Bambu PLA Basic @BBL A1M", "tray_slot": 4}}'
    payload, err = build_slicer_filament_payload(
        project_ids,
        overrides,
        tray_profile_map={4: ""},
        used_filament_indices={5},
    )
    assert err is None
    assert isinstance(payload, dict)
    # All 12 indices represented; index 5 keeps its tray_slot, others are
    # filled with just the user's override profile_setting_id.
    assert set(payload.keys()) == {str(i) for i in range(12)}
    assert payload["5"] == {
        "profile_setting_id": "Bambu PLA Basic @BBL A1M",
        "tray_slot": 4,
    }
    for i in range(12):
        if i == 5:
            continue
        assert payload[str(i)] == {"profile_setting_id": "Bambu PLA Basic @BBL A1M"}


def test_padding_leaves_used_indices_without_override_alone():
    """A used index without an override is left to the project's setting_id."""
    project_ids = ["valid_a", "valid_b", "valid_c"]
    overrides = '{"0": {"profile_setting_id": "override_a", "tray_slot": 1}}'
    payload, err = build_slicer_filament_payload(
        project_ids,
        overrides,
        tray_profile_map={1: ""},
        used_filament_indices={0, 1},
    )
    assert err is None
    # Index 0: user override. Index 1: used but unoverridden — left alone.
    # Index 2: unused with no override — padded.
    assert "1" not in payload
    assert payload["2"] == {"profile_setting_id": "override_a"}


def test_padding_skipped_when_no_override_to_fill_with():
    """Without any override there's no profile to copy — leave the dict alone."""
    project_ids = ["a", "b", "c"]
    payload, err = build_slicer_filament_payload(
        project_ids,
        "{}",
        used_filament_indices={1},
    )
    assert err is None
    assert payload == {}
