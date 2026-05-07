"""Unit tests for process_overrides plumbing in SlicerClient."""
from __future__ import annotations

import httpx
import pytest

from app.slicer_client import SlicerClient, _slice_result_from_v2


def _stub_transport(handler):
    return httpx.MockTransport(handler)


# Minimal inspect payload SlicerClient needs from internal helpers
# (auto_center machine-name lookup, normalize_filament dict path, etc.).
_FAKE_INSPECT = {
    "schema_version": 4,
    "is_sliced": False,
    "plate_count": 1,
    "plates": [{"id": 1, "used_filament_indices": [0]}],
    "filaments": [
        {"slot": 0, "settings_id": "Bambu PLA Basic"},
    ],
    "printer_settings_id": "Bambu Lab A1 mini 0.4 nozzle",
    "process_modifications": {},
}

_FAKE_MACHINE_DETAIL = {"name": "Bambu Lab A1 mini 0.4 nozzle", "setting_id": "GM004"}


def _make_handler(captured: dict):
    """Build a MockTransport handler that satisfies SlicerClient.slice.

    Records the v2 body the client posts, mocks /3mf upload, /3mf/{token}
    download, /3mf/{token}/inspect, /profiles/machines/{id}.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/3mf":
            return httpx.Response(200, json={
                "token": "INPUT_TOK", "sha256": "x", "size": 0, "evicts": [],
            })
        if request.method == "GET" and path == "/3mf/INPUT_TOK/inspect":
            return httpx.Response(200, json=_FAKE_INSPECT)
        if request.method == "GET" and path.startswith("/profiles/machines/"):
            return httpx.Response(200, json=_FAKE_MACHINE_DETAIL)
        if request.method == "POST" and path == "/slice/v2":
            captured["body"] = request.read().decode()
            import json as _json
            captured["json"] = _json.loads(captured["body"])
            return httpx.Response(200, json={
                "input_token": "INPUT_TOK",
                "output_token": "OUTPUT_TOK",
                "estimate": None,
                "settings_transfer": {
                    "status": "applied",
                    "process_keys": ["layer_height"],
                    "printer_keys": [],
                    "filament_slots": [],
                    "process_overrides_applied": [
                        {"key": "layer_height", "value": "0.16", "previous": "0.20"},
                    ],
                },
                "thumbnail_urls": [],
                "download_url": "/3mf/OUTPUT_TOK/",
            })
        if request.method == "GET" and path == "/3mf/OUTPUT_TOK":
            return httpx.Response(200, content=b"OUTPUT_BYTES")
        return httpx.Response(404)
    return handler


@pytest.mark.asyncio
async def test_slice_includes_process_overrides_in_v2_body():
    captured: dict = {}
    client = SlicerClient(
        "http://slicer", transport=_stub_transport(_make_handler(captured)),
    )
    result = await client.slice(
        b"input-bytes",
        filename="test.3mf",
        machine_profile="GM004",
        process_profile="GP004",
        filament_profiles=["Bambu PLA Basic"],
        process_overrides={"layer_height": "0.16", "wall_loops": "3"},
    )
    assert captured["json"]["process_overrides"] == {
        "layer_height": "0.16",
        "wall_loops": "3",
    }
    assert result.process_overrides_applied == [
        {"key": "layer_height", "value": "0.16", "previous": "0.20"},
    ]


@pytest.mark.asyncio
async def test_slice_omits_process_overrides_when_none():
    captured: dict = {}
    client = SlicerClient(
        "http://slicer", transport=_stub_transport(_make_handler(captured)),
    )
    await client.slice(
        b"x", filename="t.3mf", machine_profile="GM004",
        process_profile="GP004", filament_profiles=["Bambu PLA Basic"],
    )
    assert "process_overrides" not in captured["json"]


@pytest.mark.asyncio
async def test_slice_omits_process_overrides_when_empty_dict():
    captured: dict = {}
    client = SlicerClient(
        "http://slicer", transport=_stub_transport(_make_handler(captured)),
    )
    await client.slice(
        b"x", filename="t.3mf", machine_profile="GM004",
        process_profile="GP004", filament_profiles=["Bambu PLA Basic"],
        process_overrides={},
    )
    assert "process_overrides" not in captured["json"]


def test_slice_result_from_v2_parses_process_overrides_applied():
    payload = {
        "settings_transfer": {
            "status": "applied",
            "filament_slots": [],
            "process_overrides_applied": [
                {"key": "layer_height", "value": "0.16", "previous": "0.20"},
                {"key": "wall_loops", "value": "3", "previous": "2"},
            ],
        },
        "estimate": None,
    }
    result = _slice_result_from_v2(payload, b"X")
    assert result.process_overrides_applied == [
        {"key": "layer_height", "value": "0.16", "previous": "0.20"},
        {"key": "wall_loops", "value": "3", "previous": "2"},
    ]


def test_slice_result_from_v2_defaults_empty_overrides_applied():
    payload = {
        "settings_transfer": {
            "status": "applied",
            "filament_slots": [],
        },
        "estimate": None,
    }
    result = _slice_result_from_v2(payload, b"X")
    assert result.process_overrides_applied == []
