"""GUI-parity recenter behaviour for `_build_v2_slice_body`.

The OrcaSlicer GUI re-arranges items when the user changes the active
printer (so a project authored for a P2S 256mm bed shows up centered on
A1 mini's 180mm bed instead of hanging off the right edge). For
same-machine slices we keep authored placement, matching the GUI's
project-import path.
"""
from __future__ import annotations

import httpx
import pytest

from app.slicer_client import SlicerClient


def _inspect_response(printer_settings_id: str = "") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "schema_version": 2,
            "is_sliced": False,
            "plate_count": 1,
            "plates": [],
            "filaments": [],
            "estimate": None,
            "bbox": None,
            "thumbnail_urls": [],
            "use_set_per_plate": {},
            "printer_settings_id": printer_settings_id,
        },
    )


def _machine_response(name: str) -> httpx.Response:
    return httpx.Response(200, json={"setting_id": "any", "name": name})


@pytest.mark.asyncio
async def test_recenter_true_when_printer_differs():
    """Project authored for a P2S printer + slice request targeting an
    A1 mini → recenter."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/inspect"):
            return _inspect_response("Bambu Lab P2S 0.4 nozzle")
        if "/profiles/machines/" in request.url.path:
            return _machine_response("Bambu Lab A1 mini 0.4 nozzle")
        return httpx.Response(404)

    client = SlicerClient("http://test", transport=httpx.MockTransport(_handler))
    body = await client._build_v2_slice_body(
        input_token="tok",
        machine_profile="GM020",
        process_profile="GP000",
        filament_profiles=["Bambu PLA Basic @BBL A1M"],
        plate=1,
    )
    assert body["recenter"] is True


@pytest.mark.asyncio
async def test_recenter_false_when_printer_matches():
    """Project authored for a P2S printer + slice request targeting the
    same P2S → keep authored placement."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/inspect"):
            return _inspect_response("Bambu Lab P2S 0.4 nozzle")
        if "/profiles/machines/" in request.url.path:
            return _machine_response("Bambu Lab P2S 0.4 nozzle")
        return httpx.Response(404)

    client = SlicerClient("http://test", transport=httpx.MockTransport(_handler))
    body = await client._build_v2_slice_body(
        input_token="tok",
        machine_profile="GM049",
        process_profile="GP000",
        filament_profiles=["Bambu PLA Basic @BBL P2S"],
        plate=1,
    )
    assert body["recenter"] is False


@pytest.mark.asyncio
async def test_recenter_false_when_printer_unknown():
    """Inspect doesn't carry an authored printer name (older 3MFs, raw
    geometry uploads) → keep authored placement (no information to act
    on; the slicer's own validation will catch out-of-bed errors)."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/inspect"):
            return _inspect_response("")  # no printer_settings_id
        if "/profiles/machines/" in request.url.path:
            return _machine_response("Bambu Lab A1 mini 0.4 nozzle")
        return httpx.Response(404)

    client = SlicerClient("http://test", transport=httpx.MockTransport(_handler))
    body = await client._build_v2_slice_body(
        input_token="tok",
        machine_profile="GM020",
        process_profile="GP000",
        filament_profiles=["x"],
        plate=1,
    )
    assert body["recenter"] is False


@pytest.mark.asyncio
async def test_recenter_false_when_machine_lookup_fails():
    """Slicer's /profiles/machines/{id} returns non-200 → can't compare,
    fall through to authored placement (best-effort probe)."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/inspect"):
            return _inspect_response("Bambu Lab P2S 0.4 nozzle")
        if "/profiles/machines/" in request.url.path:
            return httpx.Response(503)
        return httpx.Response(404)

    client = SlicerClient("http://test", transport=httpx.MockTransport(_handler))
    body = await client._build_v2_slice_body(
        input_token="tok",
        machine_profile="GM020",
        process_profile="GP000",
        filament_profiles=["x"],
        plate=1,
    )
    assert body["recenter"] is False
