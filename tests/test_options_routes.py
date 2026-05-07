"""Route tests for /api/slicer/options/process[/layout]."""
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


def test_options_process_proxies_response(client_with_slicer):
    client, fake = client_with_slicer
    catalogue = {"version": "2.3.2-41", "options": {"layer_height": {"key": "layer_height"}}}
    fake.get_process_options.return_value = catalogue

    resp = client.get("/api/slicer/options/process")

    assert resp.status_code == 200
    assert resp.json() == catalogue
    fake.get_process_options.assert_awaited_once()


def test_options_process_layout_proxies_response(client_with_slicer):
    client, fake = client_with_slicer
    layout = {"version": "2.3.2-41", "allowlist_revision": "2026-05-06.1", "pages": []}
    fake.get_process_layout.return_value = layout

    resp = client.get("/api/slicer/options/process/layout")

    assert resp.status_code == 200
    assert resp.json() == layout
    fake.get_process_layout.assert_awaited_once()


def test_options_process_returns_400_when_slicer_unconfigured(client_no_slicer):
    resp = client_no_slicer.get("/api/slicer/options/process")
    assert resp.status_code == 400
    assert "Slicer not configured" in resp.json()["detail"]


def test_options_process_layout_returns_400_when_slicer_unconfigured(client_no_slicer):
    resp = client_no_slicer.get("/api/slicer/options/process/layout")
    assert resp.status_code == 400
    assert "Slicer not configured" in resp.json()["detail"]


def test_options_process_propagates_slicing_error_as_502(client_with_slicer):
    client, fake = client_with_slicer
    fake.get_process_options.side_effect = SlicingError(
        "Slicer returned 503: options cache failed to populate"
    )

    resp = client.get("/api/slicer/options/process")

    assert resp.status_code == 502
    assert "options cache" in resp.json()["detail"]
