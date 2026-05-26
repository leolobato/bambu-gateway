"""Tests for the cloud auth flow."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse, unquote

import pytest

from app.cloud.auth import build_signin_url


def test_signin_url_us_region_uses_bambulab_com():
    url = build_signin_url(region="US")
    assert url.startswith("https://bambulab.com/sign-in")


def test_signin_url_cn_region_uses_bambulab_cn():
    url = build_signin_url(region="CN")
    assert "bambulab.cn" in url


def test_signin_url_carries_ticket_flow_markers():
    # OrcaSlicer's pjarczak_browser_login_url builder uses
    # `slicerLoginType=ticket` so Bambu redirects with `?ticket=<val>`
    # (Phase A discovery, 2026-05-27-bambu-auth-discovery.md §A.1).
    url = build_signin_url(region="US")
    qs = parse_qs(urlparse(url).query)
    # Outer URL has `to=<encoded inner URL>`; inner URL carries the
    # ticket-flow markers.
    inner = unquote(qs["to"][0])
    inner_qs = parse_qs(urlparse(inner).query)
    assert inner_qs["slicerLoginType"] == ["ticket"]
    assert inner_qs["redirect_url"] == ["http://localhost:13618"]
    assert inner_qs["openBy"] == ["suite"]


def test_signin_url_outer_query_includes_from_and_source():
    url = build_signin_url(region="US")
    qs = parse_qs(urlparse(url).query)
    assert qs["from"] == ["studio"]
    assert qs["source"] == ["portal"]


from app.cloud.auth import PasteParseError, parse_paste_url


def test_parse_paste_url_extracts_ticket():
    pasted = "http://localhost:13618/?ticket=tk_abc123"
    assert parse_paste_url(pasted) == "tk_abc123"


def test_parse_paste_url_accepts_ticket_with_extra_params():
    # Bambu may attach extra params; we only care about `ticket`.
    pasted = "http://localhost:13618/?ticket=tk_abc&from=studio"
    assert parse_paste_url(pasted) == "tk_abc"


def test_parse_paste_url_accepts_fragment_style():
    # Some OAuth flows put values in the fragment (#) instead of the query.
    pasted = "http://localhost:13618/#ticket=tk_abc"
    assert parse_paste_url(pasted) == "tk_abc"


def test_parse_paste_url_rejects_missing_ticket():
    with pytest.raises(PasteParseError):
        parse_paste_url("http://localhost:13618/?foo=bar")


def test_parse_paste_url_rejects_obviously_wrong_input():
    with pytest.raises(PasteParseError):
        parse_paste_url("https://example.com/?ticket=x")
    with pytest.raises(PasteParseError):
        parse_paste_url("not a url")


import httpx

from app.cloud.auth import fetch_profile, ProfileFetchError


async def test_fetch_profile_returns_profile_json():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            200,
            json={
                "uidStr": "42",
                "name": "Alice",
                "account": "alice@example.com",
                "avatar": "https://cdn/avatar.png",
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        profile = await fetch_profile(
            client=client, region="US", access_token="at_xyz"
        )

    assert profile["uidStr"] == "42"
    req = seen_requests[0]
    assert req.headers["Authorization"] == "Bearer at_xyz"
    assert req.url.path == "/v1/user-service/u/info"


async def test_fetch_profile_raises_on_non_200():
    def handler(request):
        return httpx.Response(401, json={"error": "invalid_token"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ProfileFetchError):
            await fetch_profile(client=client, region="US", access_token="bad")


import json

from app.cloud.auth import build_canonical_login


def test_build_canonical_login_assembles_token_and_profile_fields():
    tokens = {
        "access_token": "at",
        "refresh_token": "rt",
        "expires_in": "3600",
        "refresh_expires_in": "86400",
    }
    profile = {
        "uidStr": "42",
        "name": "Alice",
        "account": "alice@example.com",
        "avatar": "https://cdn/avatar.png",
    }
    payload = build_canonical_login(tokens=tokens, profile=profile)
    obj = json.loads(payload)
    assert obj["command"] == "user_login"
    d = obj["data"]
    assert d["token"] == "at"
    assert d["access_token"] == "at"
    assert d["refresh_token"] == "rt"
    assert d["expires_in"] == "3600"
    assert d["refresh_expires_in"] == "86400"
    assert d["user_id"] == "42"
    assert d["uidStr"] == "42"
    assert d["user"]["id"] == "42"
    assert d["user"]["uid"] == "42"
    assert d["user"]["uidStr"] == "42"
    assert d["user"]["name"] == "Alice"
    assert d["user"]["account"] == "alice@example.com"
    assert d["user"]["avatar"] == "https://cdn/avatar.png"


def test_build_canonical_login_accepts_uid_in_other_field_names():
    payload = build_canonical_login(
        tokens={
            "access_token": "at", "refresh_token": "rt",
            "expires_in": "1", "refresh_expires_in": "1",
        },
        profile={"uid": "99", "name": "Bob"},
    )
    obj = json.loads(payload)
    assert obj["data"]["user_id"] == "99"


def test_build_canonical_login_accepts_camelcase_token_keys():
    # The plugin's get_my_token may return either camelCase or snake_case;
    # Phase 0 §Q11.4 says HttpServer normalises via json_string_first.
    payload = build_canonical_login(
        tokens={"accessToken": "at", "refreshToken": "rt",
                "expiresIn": "1", "refreshExpiresIn": "1"},
        profile={"uidStr": "1"},
    )
    obj = json.loads(payload)
    assert obj["data"]["token"] == "at"
    assert obj["data"]["refresh_token"] == "rt"


def test_build_canonical_login_rejects_missing_uid():
    with pytest.raises(ValueError):
        build_canonical_login(
            tokens={"access_token": "at", "refresh_token": "rt",
                    "expires_in": "1", "refresh_expires_in": "1"},
            profile={"name": "x"},
        )
