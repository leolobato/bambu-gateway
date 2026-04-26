from app.filament_selection import build_ams_mapping, extract_selected_tray_slots


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
