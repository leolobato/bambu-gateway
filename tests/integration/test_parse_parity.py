"""Side-by-side parity: parse_3mf_via_slicer vs the old parse_3mf.

Runs against the shared fixture set. Skipped when orcaslicer-cli isn't
reachable. Once Task 9 deletes the old parser this test gets removed
(or repurposed against a captured golden-output JSON).

Known divergences (stripped from comparison):
- ``plates[*].thumbnail``: URL-fetch vs in-place base64 (see _fetch_main_thumbnails).
- ``plates[*].objects``: inspect doesn't currently surface object IDs (Phase 4 follow-up).
- ``print_profile.print_settings_id`` / ``print_profile.layer_height``: inspect doesn't
  return these fields; new adapter emits empty strings while legacy extracts them from
  the 3MF's project.config XML. Callers that need these should read them from the slice
  request/response instead — see Phase 3 follow-up.
- ``printer.printer_settings_id``: same gap — inspect surfaces ``printer_model`` and
  ``printer_variant`` (nozzle diameter) but not the profile setting_id string (e.g.
  "Bambu Lab A1 mini 0.4 nozzle"). Legacy extracted it from the 3MF's project.config.
  Phase 3 follow-up: add to inspect response or read from the slice response.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from app.parse_3mf import parse_3mf_via_slicer  # new
from app.parse_3mf_legacy import parse_3mf as parse_3mf_old  # staged copy
from app.slicer_client import SlicerClient

API = os.environ.get("ORCASLICER_API_URL", "http://localhost:8000")
FIX_DIR = Path(__file__).resolve().parents[3] / "_fixture"


def _reachable() -> bool:
    try:
        return httpx.get(f"{API}/health", timeout=2.0).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason=f"slicer at {API} unreachable")


@pytest.mark.asyncio
async def test_fixture_01_parity():
    fp = FIX_DIR / "01" / "reference-benchy-orca-no-filament-custom-settings.3mf"
    if not fp.exists():
        pytest.skip("fixture missing")
    data = fp.read_bytes()

    old = parse_3mf_old(data, plate_id=None)
    client = SlicerClient(API)
    new = await parse_3mf_via_slicer(
        data, client, plate_id=None, include_thumbnails=False,
    )

    # Compare wire shape, ignoring fields we know diverge intentionally:
    # - thumbnails (URL-fetch vs in-place base64 — see _fetch_main_thumbnails)
    # - PlateInfo.objects (inspect doesn't currently surface object IDs)
    # - print_profile fields (print_settings_id, layer_height) — inspect doesn't
    #   return these; adapter emits empty strings.
    def _strip(info):
        d = info.model_dump()
        for p in d.get("plates", []):
            p.pop("thumbnail", None)
            p.pop("objects", None)
        if "print_profile" in d:
            d["print_profile"].pop("print_settings_id", None)
            d["print_profile"].pop("layer_height", None)
        if "printer" in d:
            # inspect doesn't return the printer profile setting_id — adapter emits "".
            # Legacy extracted "Bambu Lab A1 mini 0.4 nozzle" from project.config XML.
            d["printer"].pop("printer_settings_id", None)
        return d

    assert _strip(new) == _strip(old)
