"""Form-field validation and round-trip for process_overrides on print routes."""
from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.slicer_client import SliceResult


_FAKE_3MF = b"PK\x03\x04\x14\x00FAKE3MF"


@pytest.fixture
def configured_app(monkeypatch, tmp_path):
    """Wire up enough mocks that /api/print can run end-to-end (slicing path)."""
    fake_slicer = AsyncMock()
    fake_slicer.slice.return_value = SliceResult(
        content=b"OUTPUT_BYTES",
        settings_transfer_status="applied",
        settings_transferred=[],
        filament_transfers=[],
        estimate=None,
        process_overrides_applied=[
            {"key": "layer_height", "value": "0.16", "previous": "0.20"},
        ],
    )
    monkeypatch.setattr(app_main, "slicer_client", fake_slicer)

    fake_info = MagicMock()
    fake_info.has_gcode = False
    fake_info.filaments = [
        MagicMock(setting_id="Bambu PLA Basic", index=0, used=True),
    ]
    fake_info.process_modifications = MagicMock(values={})

    async def fake_parse(*a, **kw):
        return fake_info

    monkeypatch.setattr(app_main, "parse_3mf_via_slicer", fake_parse)

    async def fake_resolve(project_ids, raw, printer_id, used_filament_indices=None):
        return ["Bambu PLA Basic"], None

    monkeypatch.setattr(
        app_main, "_resolve_slice_filament_payload", fake_resolve,
    )
    return TestClient(app_main.app), fake_slicer


def test_print_slice_only_round_trips_process_overrides(configured_app):
    client, fake_slicer = configured_app
    resp = client.post(
        "/api/print",
        files={"file": ("test.3mf", io.BytesIO(_FAKE_3MF), "application/octet-stream")},
        data={
            "machine_profile": "GM004",
            "process_profile": "GP004",
            "process_overrides": json.dumps({"layer_height": "0.16"}),
            "slice_only": "true",
        },
    )
    assert resp.status_code == 200
    # Verify SlicerClient.slice was called with the override dict.
    _, kwargs = fake_slicer.slice.call_args
    assert kwargs["process_overrides"] == {"layer_height": "0.16"}
    # process_overrides_applied surfaces in the response header.
    header = resp.headers.get("X-Settings-Transfer-Status")
    assert header == "applied"


def test_print_omits_process_overrides_when_empty_string(configured_app):
    client, fake_slicer = configured_app
    resp = client.post(
        "/api/print",
        files={"file": ("test.3mf", io.BytesIO(_FAKE_3MF), "application/octet-stream")},
        data={
            "machine_profile": "GM004",
            "process_profile": "GP004",
            "process_overrides": "",
            "slice_only": "true",
        },
    )
    assert resp.status_code == 200
    _, kwargs = fake_slicer.slice.call_args
    assert kwargs["process_overrides"] is None


def test_print_rejects_invalid_json(configured_app):
    client, _ = configured_app
    resp = client.post(
        "/api/print",
        files={"file": ("test.3mf", io.BytesIO(_FAKE_3MF), "application/octet-stream")},
        data={
            "machine_profile": "GM004",
            "process_profile": "GP004",
            "process_overrides": "{not json",
            "slice_only": "true",
        },
    )
    assert resp.status_code == 400
    assert "Invalid process_overrides JSON" in resp.json()["detail"]


def test_print_rejects_non_object_json(configured_app):
    client, _ = configured_app
    resp = client.post(
        "/api/print",
        files={"file": ("test.3mf", io.BytesIO(_FAKE_3MF), "application/octet-stream")},
        data={
            "machine_profile": "GM004",
            "process_profile": "GP004",
            "process_overrides": "[\"not\", \"an\", \"object\"]",
            "slice_only": "true",
        },
    )
    assert resp.status_code == 400
    assert "must be a JSON object" in resp.json()["detail"]


def test_print_rejects_non_string_value(configured_app):
    client, _ = configured_app
    resp = client.post(
        "/api/print",
        files={"file": ("test.3mf", io.BytesIO(_FAKE_3MF), "application/octet-stream")},
        data={
            "machine_profile": "GM004",
            "process_profile": "GP004",
            "process_overrides": json.dumps({"layer_height": 0.16}),
            "slice_only": "true",
        },
    )
    assert resp.status_code == 400
    assert "values must be strings" in resp.json()["detail"]
