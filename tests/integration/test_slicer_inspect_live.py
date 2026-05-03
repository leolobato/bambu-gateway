"""Live HTTP test: gateway's SlicerClient.inspect against running orcaslicer-cli.

Skipped when ``$ORCASLICER_API_URL`` isn't reachable. Uses the shared
benchy fixture in ``../_fixture/01``.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from app.slicer_client import SlicerClient

API = os.environ.get("ORCASLICER_API_URL", "http://localhost:8000")
FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "_fixture"
    / "01"
    / "reference-benchy-orca-no-filament-custom-settings.3mf"
)


def _reachable() -> bool:
    try:
        return httpx.get(f"{API}/health", timeout=2.0).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(),
    reason=f"orcaslicer-cli unreachable at {API}",
)


@pytest.mark.asyncio
async def test_inspect_fixture_01_via_client():
    if not FIXTURE.exists():
        pytest.skip(f"missing fixture: {FIXTURE}")
    client = SlicerClient(API)
    data = FIXTURE.read_bytes()
    upload = await client.upload_3mf(data)
    try:
        insp = await client.inspect(upload["token"])
        assert insp["is_sliced"] is False
        assert insp["plate_count"] == 1
        assert insp["printer_model"] == "Bambu Lab A1 mini"
        assert insp["plates"][0]["used_filament_indices"] == [0]
        assert insp["filaments"][0]["type"] == "PLA"
        assert insp["filaments"][0]["filament_id"] == "GFA00"
    finally:
        await client.delete_token(upload["token"])
