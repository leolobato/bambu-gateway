"""Tests for GET /api/ams ?printer_id= routing."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import main as app_main


@pytest.fixture
def client(monkeypatch):
    """Build a TestClient with a stubbed printer_service that knows two printers."""
    class _StubService:
        def __init__(self) -> None:
            self._known = {"DEFAULT01", "OTHER02"}

        def default_printer_id(self) -> str:
            return "DEFAULT01"

        async def get_ams_info_async(self, pid: str):
            if pid not in self._known:
                return None
            # (trays, units, vt_tray) — empty is fine for routing test.
            return [], [], None

        def get_client(self, pid: str):
            # _resolve_printer_id calls this to verify existence
            return object() if pid in self._known else None

    service = _StubService()
    monkeypatch.setattr(app_main, "printer_service", service)

    # Skip the slicer-profile fetch — it would try to hit OrcaSlicer over HTTP.
    async def _no_filaments(_pid):
        return [], ""

    monkeypatch.setattr(app_main, "_get_machine_slicer_filaments", _no_filaments)

    return TestClient(app_main.app)


def test_getAms_noQuery_usesDefaultPrinter(client):
    """Without ?printer_id=, the route resolves the default printer."""
    response = client.get("/api/ams")
    assert response.status_code == 200
    assert response.json()["printer_id"] == "DEFAULT01"


def test_getAms_explicitPrinterId_usesThatPrinter(client):
    """With ?printer_id=OTHER02, the response reports that printer."""
    response = client.get("/api/ams?printer_id=OTHER02")
    assert response.status_code == 200
    assert response.json()["printer_id"] == "OTHER02"


def test_getAms_unknownPrinterId_returns404(client):
    """Bogus ?printer_id= must 404 instead of silently falling back."""
    response = client.get("/api/ams?printer_id=NOPE99")
    assert response.status_code == 404
