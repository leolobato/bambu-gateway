import pytest

from app.slicer_client import SlicerClient, _slice_result_from_v2


def test_slice_result_surfaces_filament_overrides_applied():
    payload = {
        "settings_transfer": {
            "status": "applied",
            "filament_overrides_applied": [
                {"slot": 0, "key": "nozzle_temperature", "value": "230", "previous": "220"},
            ],
        },
    }
    result = _slice_result_from_v2(payload, b"sliced")
    assert result.filament_overrides_applied == [
        {"slot": 0, "key": "nozzle_temperature", "value": "230", "previous": "220"},
    ]


def test_slice_result_defaults_filament_overrides_applied_empty():
    result = _slice_result_from_v2({"settings_transfer": {"status": "applied"}}, b"x")
    assert result.filament_overrides_applied == []


@pytest.mark.asyncio
async def test_build_v2_body_includes_filament_overrides_when_present(monkeypatch):
    client = SlicerClient("http://slicer")

    async def fake_norm(token, fp, *, machine_profile=None):
        return ["GFL99"], None

    async def fake_center(token, machine):
        return False

    monkeypatch.setattr(client, "_normalize_filament_selection", fake_norm)
    monkeypatch.setattr(client, "should_auto_center_for_machine", fake_center)

    body = await client._build_v2_slice_body(
        input_token="t", machine_profile="GM020", process_profile="GP109",
        filament_profiles=["GFL99"], plate=1,
        filament_overrides={"0": {"nozzle_temperature": "230"}},
    )
    assert body["filament_overrides"] == {"0": {"nozzle_temperature": "230"}}


@pytest.mark.asyncio
async def test_build_v2_body_omits_empty_filament_overrides(monkeypatch):
    client = SlicerClient("http://slicer")

    async def fake_norm(token, fp, *, machine_profile=None):
        return ["GFL99"], None

    async def fake_center(token, machine):
        return False

    monkeypatch.setattr(client, "_normalize_filament_selection", fake_norm)
    monkeypatch.setattr(client, "should_auto_center_for_machine", fake_center)

    for empty in (None, {}):
        body = await client._build_v2_slice_body(
            input_token="t", machine_profile="GM020", process_profile="GP109",
            filament_profiles=["GFL99"], plate=1, filament_overrides=empty,
        )
        assert "filament_overrides" not in body
