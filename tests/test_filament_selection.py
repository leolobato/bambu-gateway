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


# build_slicer_filament_payload — unmapped slots are passed through


def test_unmapped_slots_left_to_project_setting_ids():
    """Multi-filament 3MF, only one slot overridden — others stay implicit.

    The slicer resolves un-overridden slots from the 3MF's authored
    `filament_settings_id` (display names). Padding them with the user's
    chosen profile would make every retained slot identical and trip
    OrcaSlicer's `load_filaments_set` dedup, mis-sizing
    `flush_volumes_matrix` at G-code export.
    """
    project_ids = [f"f{i}" for i in range(12)]
    overrides = '{"5": {"profile_setting_id": "Bambu PLA Basic @BBL A1M", "tray_slot": 4}}'
    payload, err = build_slicer_filament_payload(
        project_ids,
        overrides,
        tray_profile_map={4: ""},
        used_filament_indices={5},
    )
    assert err is None
    assert payload == {
        "5": {"profile_setting_id": "Bambu PLA Basic @BBL A1M", "tray_slot": 4},
    }


def test_used_filament_indices_is_accepted_for_back_compat():
    """The kwarg is retained but no longer alters the payload."""
    project_ids = ["a", "b", "c"]
    payload, err = build_slicer_filament_payload(
        project_ids,
        "{}",
        used_filament_indices={1},
    )
    assert err is None
    assert payload == {}
