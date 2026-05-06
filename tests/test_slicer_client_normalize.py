"""Pin `_normalize_filament_selection` behaviour.

The gateway is the headless equivalent of the OrcaSlicer GUI's "active
selection" layer: it composes machine + process + filaments before the
slicer runs. When the user retargets a project to a different machine
than the one it was authored for, ``PresetBundle::update_compatible``
in the GUI rotates the active filament list to same-alias variants for
the target machine. We mirror that here by calling
``/profiles/resolve-for-machine`` for any slot that wasn't explicitly
overridden by the caller.

User-overridden slots are NEVER touched — the user's pick wins, matching
the GUI's behaviour where the user can override the bundle's auto-resolved
selection.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.slicer_client import SlicerClient


def _inspect_response(filaments: list[dict]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "schema_version": 2,
            "is_sliced": False,
            "plate_count": 1,
            "plates": [],
            "filaments": filaments,
            "estimate": None,
            "bbox": None,
            "thumbnail_urls": [],
            "use_set_per_plate": {},
        },
    )


@pytest.mark.asyncio
async def test_carryover_slot_resolved_to_same_alias_variant():
    """Sparse override on slot 0 only; slot 1's carry-over P2S name is
    swapped to the same-alias A1M variant via the resolver."""

    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/inspect"):
            return _inspect_response([
                {"slot": 0, "settings_id": "Bambu PLA Basic @BBL P2S"},
                {"slot": 1, "settings_id": "Bambu PLA Basic @BBL P2S"},
            ])
        if request.method == "POST" and request.url.path == "/profiles/resolve-for-machine":
            captured["resolve_body"] = json.loads(request.content.decode())
            return httpx.Response(200, json={
                "machine_id": "GM020",
                "machine_name": "Bambu Lab A1 mini 0.4 nozzle",
                "filaments": [
                    # Slot 0 was user-overridden — resolver still receives
                    # the override (we don't pre-filter), but the gateway
                    # ignores the resolved value for overridden slots.
                    {"slot": 0, "requested": "Bambu PLA Basic @BBL A1M",
                     "setting_id": "GFSA00_02", "name": "Bambu PLA Basic @BBL A1M",
                     "alias": "Bambu PLA Basic", "match": "unchanged"},
                    {"slot": 1, "requested": "Bambu PLA Basic @BBL P2S",
                     "setting_id": "GFSA00_02", "name": "Bambu PLA Basic @BBL A1M",
                     "alias": "Bambu PLA Basic", "match": "alias"},
                ],
            })
        return httpx.Response(404, json={"code": "unmocked"})

    client = SlicerClient("http://test", transport=httpx.MockTransport(_handler))
    filament_ids, filament_map = await client._normalize_filament_selection(
        "tok-abc",
        {"0": {"profile_setting_id": "Bambu PLA Basic @BBL A1M", "tray_slot": 1}},
        machine_profile="GM020",
    )

    # Slot 0: user override wins (would even override an alias-resolved
    # value; the user's explicit pick is sacred).
    # Slot 1: resolver swapped P2S → A1M same-alias variant.
    assert filament_ids == [
        "Bambu PLA Basic @BBL A1M",
        "Bambu PLA Basic @BBL A1M",
    ]
    assert filament_map is None
    # Resolver was called with the post-override list (slot 0 already
    # holds the user's pick, slot 1 still holds the carry-over name).
    assert captured["resolve_body"] == {
        "machine_id": "GM020",
        "process_name": "",
        "filament_names": [
            "Bambu PLA Basic @BBL A1M",
            "Bambu PLA Basic @BBL P2S",
        ],
        "plate_type": "",
    }


@pytest.mark.asyncio
async def test_user_override_wins_over_resolver():
    """When the user explicitly chose a name the resolver flags as
    incompatible, we still pass it through — the user's pick is sacred."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/inspect"):
            return _inspect_response([
                {"slot": 0, "settings_id": "Bambu PLA Basic @BBL A1M"},
            ])
        if request.method == "POST" and request.url.path == "/profiles/resolve-for-machine":
            return httpx.Response(200, json={
                "machine_id": "GM020", "machine_name": "x",
                "filaments": [
                    {"slot": 0, "requested": "Bambu PLA Basic @BBL P2S",
                     "setting_id": "", "name": "Bambu PLA Basic @BBL A1M",
                     "alias": "Bambu PLA Basic", "match": "alias"},
                ],
            })
        return httpx.Response(404, json={"code": "unmocked"})

    client = SlicerClient("http://test", transport=httpx.MockTransport(_handler))
    filament_ids, _ = await client._normalize_filament_selection(
        "tok-abc",
        # User explicitly wants P2S — even though resolver would swap.
        {"0": "Bambu PLA Basic @BBL P2S"},
        machine_profile="GM020",
    )
    assert filament_ids == ["Bambu PLA Basic @BBL P2S"]


@pytest.mark.asyncio
async def test_unchanged_match_left_alone():
    """If every slot is already compatible, the resolver's `unchanged`
    match leaves names exactly as they were."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/inspect"):
            return _inspect_response([
                {"slot": 0, "settings_id": "Bambu PLA Basic @BBL A1M"},
                {"slot": 1, "settings_id": "Bambu PETG @BBL A1M"},
            ])
        if request.method == "POST" and request.url.path == "/profiles/resolve-for-machine":
            return httpx.Response(200, json={
                "machine_id": "GM020", "machine_name": "x",
                "filaments": [
                    {"slot": 0, "requested": "Bambu PLA Basic @BBL A1M",
                     "setting_id": "GFSA00_02", "name": "Bambu PLA Basic @BBL A1M",
                     "alias": "Bambu PLA Basic", "match": "unchanged"},
                    {"slot": 1, "requested": "Bambu PETG @BBL A1M",
                     "setting_id": "GFSL99_02", "name": "Bambu PETG @BBL A1M",
                     "alias": "Bambu PETG", "match": "unchanged"},
                ],
            })
        return httpx.Response(404, json={"code": "unmocked"})

    client = SlicerClient("http://test", transport=httpx.MockTransport(_handler))
    filament_ids, _ = await client._normalize_filament_selection(
        "tok-abc",
        {},  # no overrides
        machine_profile="GM020",
    )
    assert filament_ids == [
        "Bambu PLA Basic @BBL A1M",
        "Bambu PETG @BBL A1M",
    ]


@pytest.mark.asyncio
async def test_no_compat_match_leaves_authored_name():
    """If the resolver returns `none` for a slot, we keep the authored
    name — let the slicer surface its `filament_machine_mismatch` so the
    operator sees the real problem rather than a silent default swap."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/inspect"):
            return _inspect_response([
                {"slot": 0, "settings_id": "Some Exotic @SomeOEM"},
            ])
        if request.method == "POST" and request.url.path == "/profiles/resolve-for-machine":
            return httpx.Response(200, json={
                "machine_id": "GM020", "machine_name": "x",
                "filaments": [
                    {"slot": 0, "requested": "Some Exotic @SomeOEM",
                     "setting_id": "", "name": "",
                     "alias": "", "match": "none"},
                ],
            })
        return httpx.Response(404, json={"code": "unmocked"})

    client = SlicerClient("http://test", transport=httpx.MockTransport(_handler))
    filament_ids, _ = await client._normalize_filament_selection(
        "tok-abc",
        {},
        machine_profile="GM020",
    )
    assert filament_ids == ["Some Exotic @SomeOEM"]


@pytest.mark.asyncio
async def test_resolver_outage_keeps_authored_names():
    """Resolver failure shouldn't fail the slice — fall through with the
    authored names so the slicer's own mismatch error gives the operator
    the actionable signal."""

    call_log: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/inspect"):
            return _inspect_response([
                {"slot": 0, "settings_id": "Bambu PLA Basic @BBL P2S"},
            ])
        if request.method == "POST" and request.url.path == "/profiles/resolve-for-machine":
            call_log.append("resolve")
            return httpx.Response(503, text="upstream down")
        return httpx.Response(404, json={"code": "unmocked"})

    client = SlicerClient("http://test", transport=httpx.MockTransport(_handler))
    filament_ids, _ = await client._normalize_filament_selection(
        "tok-abc",
        {},
        machine_profile="GM020",
    )
    assert filament_ids == ["Bambu PLA Basic @BBL P2S"]
    assert call_log == ["resolve"]


@pytest.mark.asyncio
async def test_no_machine_profile_skips_resolver():
    """Older callers / explicit list-form callers don't pass a machine —
    behave exactly like before (no resolver call)."""

    call_log: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/inspect"):
            return _inspect_response([
                {"slot": 0, "settings_id": "Bambu PLA Basic @BBL P2S"},
            ])
        if "resolve-for-machine" in request.url.path:
            call_log.append("resolve")
            return httpx.Response(500)
        return httpx.Response(404)

    client = SlicerClient("http://test", transport=httpx.MockTransport(_handler))
    filament_ids, _ = await client._normalize_filament_selection(
        "tok-abc",
        {},
        # machine_profile omitted
    )
    assert filament_ids == ["Bambu PLA Basic @BBL P2S"]
    assert call_log == []  # resolver not called


@pytest.mark.asyncio
async def test_list_form_passes_through():
    """List-form input is treated as the user explicitly setting all
    slots — no resolver involvement, matching the GUI's "user typed it
    in" path."""
    client = SlicerClient(
        "http://test",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
    )
    filament_ids, filament_map = await client._normalize_filament_selection(
        "tok-irrelevant",
        ["Bambu PLA Basic @BBL A1M", "Bambu PETG @BBL A1M"],
        machine_profile="GM020",
    )
    assert filament_ids == ["Bambu PLA Basic @BBL A1M", "Bambu PETG @BBL A1M"]
    assert filament_map is None
