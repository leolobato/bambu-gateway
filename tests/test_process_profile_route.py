"""Route tests for GET /api/slicer/processes/{setting_id}."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.slicer_client import SlicingError


@pytest.fixture
def client_with_slicer(monkeypatch):
    fake = AsyncMock()
    monkeypatch.setattr(app_main, "slicer_client", fake)
    return TestClient(app_main.app), fake


@pytest.fixture
def client_no_slicer(monkeypatch):
    monkeypatch.setattr(app_main, "slicer_client", None)
    return TestClient(app_main.app)


def test_process_profile_proxies_response(client_with_slicer):
    client, fake = client_with_slicer
    payload = {"layer_height": "0.16", "sparse_infill_density": "20%"}
    fake.get_process_profile.return_value = payload

    resp = client.get("/api/slicer/processes/abc-123")

    assert resp.status_code == 200
    assert resp.json() == payload
    fake.get_process_profile.assert_awaited_once_with("abc-123")


def test_process_profile_returns_400_when_slicer_unconfigured(client_no_slicer):
    resp = client_no_slicer.get("/api/slicer/processes/abc-123")
    assert resp.status_code == 400
    assert "Slicer not configured" in resp.json()["detail"]


def test_process_profile_propagates_slicing_error_as_502(client_with_slicer):
    client, fake = client_with_slicer
    fake.get_process_profile.side_effect = SlicingError("Slicer returned 404: profile not found")

    resp = client.get("/api/slicer/processes/abc-123")

    assert resp.status_code == 502
    assert "404" in resp.json()["detail"]
