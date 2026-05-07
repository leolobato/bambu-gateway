"""Live override slice against orcaslicer-cli.

Slices a known fixture with a process_overrides dict; verifies
process_overrides_applied comes back populated. Skipped when the
slicer or fixture is missing.
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


pytestmark = [
    pytest.mark.skipif(
        not _reachable(),
        reason=f"orcaslicer-cli unreachable at {API}",
    ),
    pytest.mark.skipif(
        not FIXTURE.exists(),
        reason=f"fixture not found: {FIXTURE}",
    ),
]


async def _resolve_profiles_for_fixture(client: SlicerClient) -> dict:
    """Inspect the fixture and resolve its authored profiles to setting_ids.

    Returns a dict with keys: machine_id, process_id, filament_settings_ids
    suitable for passing to client.slice().
    """
    upload = await client.upload_3mf(FIXTURE.read_bytes(), filename=FIXTURE.name)
    insp = await client.inspect(upload["token"])

    # Authored printer name -> setting_id via /profiles/machines.
    printer_name = (insp.get("printer_settings_id") or "").strip()
    machines = await client.get_profiles("machines")
    machine = next(
        (m for m in machines if m.get("name") == printer_name),
        None,
    ) or (machines[0] if machines else None)
    assert machine is not None, "slicer has no machine profiles"
    machine_id = machine["setting_id"]

    # Authored process name -> setting_id via /profiles/processes filtered by machine.
    process_name = (insp.get("print_settings_id") or "").strip()
    processes = await client.get_profiles("processes", machine=machine_id)
    process = next(
        (p for p in processes if p.get("name") == process_name),
        None,
    ) or (processes[0] if processes else None)
    assert process is not None, "slicer has no process profiles"
    process_id = process["setting_id"]

    # Authored filament names from the 3MF, used as-is (slice uses them by name).
    filament_settings_ids = [
        (f.get("settings_id") or "").strip()
        for f in insp.get("filaments", [])
    ] or ["Bambu PLA Basic"]

    return {
        "machine_id": machine_id,
        "process_id": process_id,
        "filament_settings_ids": filament_settings_ids,
    }


@pytest.mark.asyncio
async def test_slice_with_process_overrides_returns_applied_list():
    client = SlicerClient(API)
    profiles = await _resolve_profiles_for_fixture(client)

    # Pick an override unlikely to clash with the fixture's existing
    # layer_height. We assert "previous != value" rather than a specific
    # value, which makes the test robust to fixture changes.
    result = await client.slice(
        FIXTURE.read_bytes(),
        filename=FIXTURE.name,
        machine_profile=profiles["machine_id"],
        process_profile=profiles["process_id"],
        filament_profiles=profiles["filament_settings_ids"],
        process_overrides={"layer_height": "0.16"},
    )

    assert result.process_overrides_applied, (
        "Expected at least one applied override; got empty list"
    )
    entry = result.process_overrides_applied[0]
    assert entry["key"] == "layer_height"
    assert entry["value"] == "0.16"
    assert entry["previous"] != "0.16", (
        "previous should be the value before override"
    )
