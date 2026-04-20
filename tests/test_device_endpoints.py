"""Integration tests for the new device registry endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("APNS_KEY_PATH", "")
    monkeypatch.chdir(tmp_path)
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_capabilities_reports_push_disabled(client):
    res = client.get("/api/capabilities")
    assert res.status_code == 200
    body = res.json()
    assert body == {"push": False, "live_activities": False}


def test_device_register_upsert_then_delete(client):
    body = {
        "id": "dev-1", "name": "iPhone",
        "device_token": "tok-a", "subscribed_printers": ["*"],
    }
    res = client.post("/api/devices/register", json=body)
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    body["device_token"] = "tok-b"
    res = client.post("/api/devices/register", json=body)
    assert res.status_code == 200

    res = client.delete("/api/devices/dev-1")
    assert res.status_code == 200


def test_activity_register_requires_known_device(client):
    res = client.post(
        "/api/devices/dev-unknown/activities",
        json={"printer_id": "P01", "activity_update_token": "tok"},
    )
    assert res.status_code == 404


def test_activity_register_and_delete(client):
    client.post("/api/devices/register", json={
        "id": "dev-1", "name": "iPhone", "device_token": "tok",
    })
    res = client.post(
        "/api/devices/dev-1/activities",
        json={"printer_id": "P01", "activity_update_token": "upd"},
    )
    assert res.status_code == 200
    res = client.delete("/api/devices/dev-1/activities/P01")
    assert res.status_code == 200
