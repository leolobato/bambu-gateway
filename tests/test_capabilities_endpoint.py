"""Tests for the version field on GET /api/capabilities."""

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


def test_getCapabilities_includesVersion(client):
    res = client.get("/api/capabilities")
    assert res.status_code == 200
    body = res.json()
    assert "version" in body
    # The exact string is whatever the FastAPI app declares; just check it's
    # a non-empty semver-ish value.
    assert isinstance(body["version"], str)
    assert body["version"]
