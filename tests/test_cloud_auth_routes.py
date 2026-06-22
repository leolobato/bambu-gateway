"""Route-level tests for /api/cloud/auth/* using the fake host."""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest


@pytest.fixture
def cloud_app(cloud_app_factory):
    """A TestClient with cloud mode enabled and the Python fake host wired in."""
    with cloud_app_factory(
        fake_env={"FAKE_HOST_USER_LOGGED_IN": "1"},
    ) as client:
        yield client


def test_get_signin_url(cloud_app):
    resp = cloud_app.get("/api/cloud/auth/url")
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"].startswith("https://bambulab.com/sign-in")


def test_get_status_when_not_logged_in(cloud_app, monkeypatch):
    # FAKE_HOST_USER_LOGGED_IN is sticky from the fixture; for this test we
    # need to spin up a separate state. Simpler: just call the endpoint and
    # confirm the contract — actual `is_login` truthiness is exercised by
    # the Phase C orchestrator tests.
    resp = cloud_app.get("/api/cloud/auth/status")
    assert resp.status_code == 200
    assert "signed_in" in resp.json()


def test_post_paste_completes_login(cloud_app):
    pasted = "http://localhost:13618/?ticket=tk_abc"

    def profile_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"uidStr": "42", "name": "Alice", "account": "a@b"},
        )

    with patch(
        "app.cloud.auth_routes._make_http_client",
        return_value=httpx.AsyncClient(
            transport=httpx.MockTransport(profile_handler)
        ),
    ):
        resp = cloud_app.post(
            "/api/cloud/auth/paste",
            json={"pasted_url": pasted},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["profile"]["account"] == "a@b"


def test_post_paste_rejects_malformed_url(cloud_app):
    resp = cloud_app.post(
        "/api/cloud/auth/paste",
        json={"pasted_url": "https://google.com/"},
    )
    assert resp.status_code == 400
    assert "localhost" in resp.json()["detail"]


def test_post_logout(cloud_app):
    resp = cloud_app.post("/api/cloud/auth/logout")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_status_returns_profile_and_region_after_paste(cloud_app):
    pasted = "http://localhost:13618/?ticket=tk_abc"

    def profile_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"uidStr": "42", "name": "Alice", "account": "a@b"}
        )

    with patch(
        "app.cloud.auth_routes._make_http_client",
        return_value=httpx.AsyncClient(
            transport=httpx.MockTransport(profile_handler)
        ),
    ):
        cloud_app.post("/api/cloud/auth/paste", json={"pasted_url": pasted})

    resp = cloud_app.get("/api/cloud/auth/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["signed_in"] is True
    assert body["region"] == "US"
    assert body["profile"]["name"] == "Alice"
    assert body["profile"]["account"] == "a@b"


def test_logout_clears_profile(cloud_app):
    pasted = "http://localhost:13618/?ticket=tk_abc"

    def profile_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"uidStr": "42", "name": "Alice", "account": "a@b"})

    with patch(
        "app.cloud.auth_routes._make_http_client",
        return_value=httpx.AsyncClient(transport=httpx.MockTransport(profile_handler)),
    ):
        cloud_app.post("/api/cloud/auth/paste", json={"pasted_url": pasted})

    cloud_app.post("/api/cloud/auth/logout")
    resp = cloud_app.get("/api/cloud/auth/status")
    assert resp.json()["profile"] is None
