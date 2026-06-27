"""Form-field validation and gateway endpoints for filament_overrides."""
from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main as app_main
from app.main import _parse_filament_overrides_form


# ---------------------------------------------------------------------------
# Parser unit tests (no HTTP needed)
# ---------------------------------------------------------------------------

def test_parse_filament_overrides_valid():
    raw = '{"0": {"nozzle_temperature": "230"}, "1": {"filament_flow_ratio": "0.95"}}'
    assert _parse_filament_overrides_form(raw) == {
        "0": {"nozzle_temperature": "230"},
        "1": {"filament_flow_ratio": "0.95"},
    }


def test_parse_filament_overrides_empty_is_none():
    assert _parse_filament_overrides_form("") is None


def test_parse_filament_overrides_bad_json():
    with pytest.raises(HTTPException) as exc:
        _parse_filament_overrides_form("{not json")
    assert exc.value.status_code == 400


def test_parse_filament_overrides_outer_not_object():
    with pytest.raises(HTTPException):
        _parse_filament_overrides_form('["a"]')


def test_parse_filament_overrides_inner_not_object():
    with pytest.raises(HTTPException):
        _parse_filament_overrides_form('{"0": "230"}')


def test_parse_filament_overrides_inner_value_not_string():
    with pytest.raises(HTTPException):
        _parse_filament_overrides_form('{"0": {"nozzle_temperature": 230}}')


# ---------------------------------------------------------------------------
# Proxy endpoint tests
# ---------------------------------------------------------------------------

@pytest.fixture
def client_without_slicer(monkeypatch):
    """TestClient with slicer_client explicitly None (no slicer configured)."""
    monkeypatch.setattr(app_main, "slicer_client", None)
    return TestClient(app_main.app)


def test_options_filament_proxy_requires_slicer(client_without_slicer):
    resp = client_without_slicer.get("/api/slicer/options/filament")
    assert resp.status_code == 400
    assert "Slicer not configured" in resp.json()["detail"]


def test_options_filament_layout_proxy_requires_slicer(client_without_slicer):
    resp = client_without_slicer.get("/api/slicer/options/filament/layout")
    assert resp.status_code == 400
    assert "Slicer not configured" in resp.json()["detail"]


def test_filament_profile_proxy_requires_slicer(client_without_slicer):
    resp = client_without_slicer.get("/api/slicer/filaments/GFL99")
    assert resp.status_code == 400
    assert "Slicer not configured" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Slice-jobs endpoint: filament_overrides validation
# ---------------------------------------------------------------------------

_FAKE_3MF = b"PK\x03\x04\x14\x00FAKE3MF"


@pytest.fixture
def configured_app_for_filaments(monkeypatch, tmp_path):
    """Wire enough mocks so /api/slice-jobs reaches the filament_overrides parser."""
    fake_slicer = AsyncMock()
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

    monkeypatch.setattr(app_main, "_resolve_slice_filament_payload", fake_resolve)

    fake_jobs = AsyncMock()
    monkeypatch.setattr(app_main, "slice_jobs", fake_jobs)

    fake_printer_service = MagicMock()
    monkeypatch.setattr(app_main, "printer_service", fake_printer_service)

    return TestClient(app_main.app)


def test_slice_jobs_rejects_bad_filament_overrides(configured_app_for_filaments):
    """Inner value not an object should return 400 before reaching the slicer."""
    resp = configured_app_for_filaments.post(
        "/api/slice-jobs",
        data={
            "machine_profile": "GM020",
            "process_profile": "GP109",
            "filament_profiles": '["GFL99"]',
            "filament_overrides": '{"0": "230"}',
        },
        files={"file": ("a.3mf", io.BytesIO(_FAKE_3MF), "model/3mf")},
    )
    assert resp.status_code == 400
    assert "filament_overrides" in resp.json()["detail"]


def test_print_rejects_bad_filament_overrides(monkeypatch, tmp_path):
    """POST /api/print should 400 on malformed filament_overrides (inner not object)."""
    from app.slicer_client import SliceResult

    fake_slicer = AsyncMock()
    fake_slicer.slice.return_value = SliceResult(content=b"OUTPUT")
    monkeypatch.setattr(app_main, "slicer_client", fake_slicer)

    fake_info = MagicMock()
    fake_info.has_gcode = False
    fake_info.filaments = [MagicMock(setting_id="Bambu PLA Basic", index=0, used=True)]
    fake_info.process_modifications = MagicMock(values={})

    async def fake_parse(*a, **kw):
        return fake_info

    monkeypatch.setattr(app_main, "parse_3mf_via_slicer", fake_parse)

    async def fake_resolve(project_ids, raw, printer_id, used_filament_indices=None):
        return ["Bambu PLA Basic"], None

    monkeypatch.setattr(app_main, "_resolve_slice_filament_payload", fake_resolve)

    client = TestClient(app_main.app)
    resp = client.post(
        "/api/print",
        files={"file": ("test.3mf", io.BytesIO(_FAKE_3MF), "application/octet-stream")},
        data={
            "machine_profile": "GM004",
            "process_profile": "GP004",
            "filament_overrides": '{"0": "230"}',
            "slice_only": "true",
        },
    )
    assert resp.status_code == 400
    assert "filament_overrides" in resp.json()["detail"]
