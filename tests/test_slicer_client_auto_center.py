"""Auto-center decision rule for `_build_v2_slice_body`.

Headless-only behaviour: the orcaslicer-headless's `auto_center` flag asks
libslic3r to re-anchor a project's models on the target printer's bed
centre. The GUI has no equivalent runtime flag because a human adjusts
placement visually after a printer change.

The gateway flips `auto_center: true` only when the project's authored
``printer_settings_id`` differs from the target machine's display name.
Same-printer retargets keep authored placement.
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
async def test_auto_center_true_when_printer_differs():
    """Project authored for a P2S printer + slice request targeting an
    A1 mini → auto_center."""

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
    assert body["auto_center"] is True


@pytest.mark.asyncio
async def test_auto_center_false_when_printer_matches():
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
    assert body["auto_center"] is False


@pytest.mark.asyncio
async def test_auto_center_false_when_inspect_has_no_authored_printer():
    """Older 3MFs / non-project files have no `printer_settings_id`. We
    must NOT ask the slicer to recenter in that case — falling back to
    authored placement is the safe default. This locks in the
    best-effort fallback in `_should_auto_center_for_machine`."""

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
        filament_profiles=["Bambu PLA Basic @BBL A1M"],
        plate=1,
    )
    assert body["auto_center"] is False


@pytest.mark.asyncio
async def test_auto_center_true_when_target_unresolved_but_authored_known():
    """Slicer's /profiles/machines/{id} stays non-200 after retries → we
    can't confirm the target is the SAME printer as the authored one.

    Reverses the earlier "fall through to False" rule: leaving a
    cross-printer retarget off-plate (a hard, filament-wasting failure)
    is worse than recentering a model that happened to be on the same
    printer (a benign shift). When the project's authored printer IS
    known but the target name can't be resolved, default to centering.
    Regression for the Flycatraz P2S→A1-mini off-plate preview."""

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
        filament_profiles=["Bambu PLA Basic @BBL A1M"],
        plate=1,
    )
    assert body["auto_center"] is True


@pytest.mark.asyncio
async def test_auto_center_retries_transient_machine_lookup_failure():
    """A single transient 503 on the machine-name probe must not decide
    the placement: the probe retries and the second attempt resolves the
    name, so the real authored-vs-target comparison runs (P2S != A1 mini
    → True) instead of falling through on the blip."""

    attempts = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/inspect"):
            return _inspect_response("Bambu Lab P2S 0.4 nozzle")
        if "/profiles/machines/" in request.url.path:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(503)
            return _machine_response("Bambu Lab A1 mini 0.4 nozzle")
        return httpx.Response(404)

    client = SlicerClient("http://test", transport=httpx.MockTransport(_handler))
    name = await client._machine_display_name("GM020")
    assert name == "Bambu Lab A1 mini 0.4 nozzle"
    assert attempts["n"] == 2  # failed once, then succeeded
