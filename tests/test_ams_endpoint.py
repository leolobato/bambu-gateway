"""Tests for GET /api/ams ?printer_id= routing."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import main as app_main


@pytest.fixture
def trays():
    return []


@pytest.fixture
def client(monkeypatch, trays):
    """Build a TestClient with a stubbed printer_service that knows two printers."""
    class _StubService:
        def __init__(self) -> None:
            self._known = {"DEFAULT01", "OTHER02"}

        def default_printer_id(self) -> str:
            return "DEFAULT01"

        async def get_ams_info_async(self, pid: str):
            if pid not in self._known:
                return None
            return trays, [], None

        def get_client(self, pid: str):
            # _resolve_printer_id calls this to verify existence
            return object() if pid in self._known else None

        def get_status(self, pid: str):
            if pid not in self._known:
                return None
            from app.models import PrinterStatus

            return PrinterStatus(id=pid, name="Printer", online=True)

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


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ({"remain": 0, "remain_g": 0, "tray_weight": "0"}, None),
        ({"remain": 30, "remain_g": 0, "tray_weight": "1000"}, None),
        ({"remain": 30, "remain_g": 280, "tray_weight": "1000"}, 280),
        ({"remain_g": 280}, 280),
        ({"remain": 33, "remain_g": -1, "tray_weight": "750"}, 247),
        ({"remain": 50, "tray_weight": "1000"}, 500),
        ({"remain": 50, "tray_weight": "0"}, None),
        ({"remain": 101, "tray_weight": "1000"}, None),
        ({"remain": -1, "tray_weight": "1000"}, None),
        ({"remain": 50, "tray_weight": "unknown"}, None),
        ({}, None),
    ],
)
def test_get_ams_exposes_usable_remaining_grams_without_changing_raw_values(
    client, trays, reported, expected,
):
    trays.append({"slot": 3, "ams_id": 0, "tray_id": 3, "tray_type": "PLA", **reported})

    response = client.get("/api/ams")

    assert response.status_code == 200
    tray = response.json()["trays"][0]
    assert tray["remaining_grams"] == expected
    for key, value in reported.items():
        assert tray[key] == value
