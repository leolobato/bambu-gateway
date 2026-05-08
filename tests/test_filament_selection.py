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


def test_build_ams_mapping_sparse_3mf_routes_by_authored_slot():
    """Sparse 3MFs author filaments at non-zero slot indices.

    The frontend keys `filament_profiles` by *position* in `info.filaments`
    (0..N-1), but the printer's gcode references the authored *slot index*
    (e.g. a single-filament 3MF with the filament on AMS slot 1 emits T1
    toolchanges). Without `slot_indices`, the position-based ams_mapping is
    too short and the printer falls back to identity routing for slot 1 —
    pulling from tray 1 even though the user picked tray 0. Threading
    `slot_indices` through translates position→slot so the mapping array
    is sized by `max(slot)+1` and the user's pick lands at the right index.
    """
    payload = {"0": {"tray_slot": 0, "profile_setting_id": "GFA00"}}
    mapping, use = build_ams_mapping(
        payload, project_filament_count=1, slot_indices=[1],
    )
    assert mapping == [-1, 0]
    assert use is True


def test_build_ams_mapping_slot_indices_with_dense_3mf():
    payload = {"0": {"tray_slot": 2}, "1": {"tray_slot": 0}}
    mapping, use = build_ams_mapping(
        payload, project_filament_count=2, slot_indices=[0, 1],
    )
    assert mapping == [2, 0]
    assert use is True


def test_build_ams_mapping_slot_indices_default_matches_dense():
    """With slot_indices=None, behaviour is unchanged (position == slot)."""
    payload = {"0": {"tray_slot": 2}, "1": {"tray_slot": 0}}
    mapping, use = build_ams_mapping(payload, project_filament_count=2)
    assert mapping == [2, 0]
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
