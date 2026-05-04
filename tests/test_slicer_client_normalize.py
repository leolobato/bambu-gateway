"""Pin `_normalize_filament_selection` behaviour after the resolver lands.

Before the `/profiles/resolve-for-machine` integration, the gateway
contained a stopgap that replicated the first-overridden filament into
every unused slot to dodge the slicer's compat check. With the resolver
running on machine change in the print form, slots are already filled
with same-alias-for-machine values upstream — the stopgap is gone, and
this test pins the new contract: slots not explicitly overridden retain
their authored name, no second-guessing.
"""
from __future__ import annotations

import httpx
import pytest

from app.slicer_client import SlicerClient


@pytest.mark.asyncio
async def test_unused_slot_keeps_authored_name():
    """Sparse override only on slot 0; slot 1 stays as the 3MF's authored value."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/inspect"):
            return httpx.Response(
                200,
                json={
                    "schema_version": 2,
                    "is_sliced": False,
                    "plate_count": 1,
                    "plates": [],
                    "filaments": [
                        {"slot": 0, "settings_id": "Bambu PLA Basic @BBL P2S"},
                        {"slot": 1, "settings_id": "Bambu PLA Basic @BBL P2S"},
                    ],
                    "estimate": None,
                    "bbox": None,
                    "thumbnail_urls": [],
                    "use_set_per_plate": {},
                },
            )
        return httpx.Response(404, json={"code": "unmocked"})

    client = SlicerClient("http://test", transport=httpx.MockTransport(_handler))
    filament_ids, filament_map = await client._normalize_filament_selection(
        "tok-abc",
        {"0": {"profile_setting_id": "Bambu PLA Basic @BBL A1M", "tray_slot": 1}},
    )
    # Slot 0 takes the override; slot 1 keeps the authored P2S name —
    # the resolver upstream is responsible for swapping it before we get
    # here when the user retargets to a different machine.
    assert filament_ids == ["Bambu PLA Basic @BBL A1M", "Bambu PLA Basic @BBL P2S"]
    # tray_slot is intentionally NOT forwarded to the slicer; AMS routing
    # happens at print time via build_ams_mapping → MQTT project_file.
    # See app/slicer_client.py:_normalize_filament_selection.
    assert filament_map is None


@pytest.mark.asyncio
async def test_list_form_passes_through():
    client = SlicerClient("http://test", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    filament_ids, filament_map = await client._normalize_filament_selection(
        "tok-irrelevant",
        ["Bambu PLA Basic @BBL A1M", "Bambu PETG @BBL A1M"],
    )
    assert filament_ids == ["Bambu PLA Basic @BBL A1M", "Bambu PETG @BBL A1M"]
    assert filament_map is None
