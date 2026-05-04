"""Unit tests for `SlicerClient.resolve_for_machine`.

Pins the wire shape (request body and response parsing) using
`httpx.MockTransport` so CI catches contract drift without needing a
live `orcaslicer-cli` container.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.slicer_client import SlicerClient, SlicingError


@pytest.mark.asyncio
async def test_resolve_for_machine_round_trip():
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "machine_id": "GM020",
                "machine_name": "Bambu Lab A1 mini 0.4 nozzle",
                "process": {
                    "requested": "0.20mm Standard @BBL P2S",
                    "setting_id": "GP005_A1M",
                    "name": "0.20mm Standard @BBL A1M",
                    "alias": "0.20mm Standard",
                    "match": "alias",
                },
                "filaments": [
                    {
                        "slot": 0,
                        "requested": "Bambu PLA Basic @BBL P2S",
                        "setting_id": "GFA00_A1M",
                        "name": "Bambu PLA Basic @BBL A1M",
                        "alias": "Bambu PLA Basic",
                        "match": "alias",
                    },
                ],
                "plate_type": {
                    "requested": "engineering_plate",
                    "resolved": "textured_pei_plate",
                    "match": "default",
                },
            },
        )

    client = SlicerClient("http://test", transport=httpx.MockTransport(_handler))
    out = await client.resolve_for_machine(
        machine_id="GM020",
        process_name="0.20mm Standard @BBL P2S",
        filament_names=["Bambu PLA Basic @BBL P2S"],
        plate_type="engineering_plate",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/profiles/resolve-for-machine"
    assert captured["body"] == {
        "machine_id": "GM020",
        "process_name": "0.20mm Standard @BBL P2S",
        "filament_names": ["Bambu PLA Basic @BBL P2S"],
        "plate_type": "engineering_plate",
    }
    assert out["process"]["match"] == "alias"
    assert out["filaments"][0]["setting_id"] == "GFA00_A1M"
    assert out["plate_type"]["resolved"] == "textured_pei_plate"


@pytest.mark.asyncio
async def test_resolve_for_machine_omits_optional_fields():
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={"machine_id": "GM020", "machine_name": "x", "filaments": []},
        )

    client = SlicerClient("http://test", transport=httpx.MockTransport(_handler))
    await client.resolve_for_machine(machine_id="GM020")

    # Empty defaults must still be sent so the server's request schema
    # accepts the body — `process_name=""`, `filament_names=[]`,
    # `plate_type=""` are all valid no-ops on the slicer side.
    assert captured["body"]["machine_id"] == "GM020"
    assert captured["body"]["filament_names"] == []


@pytest.mark.asyncio
async def test_resolve_for_machine_propagates_400():
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "Machine 'GM999' not found"})

    client = SlicerClient("http://test", transport=httpx.MockTransport(_handler))
    with pytest.raises(SlicingError) as exc:
        await client.resolve_for_machine(machine_id="GM999")
    assert "400" in str(exc.value)
    assert "Machine" in str(exc.value)
