"""Tests for the APNs HTTP/2 client."""

from __future__ import annotations

import json

import httpx
import pytest

from app.apns_client import ApnsClient, ApnsPushType, ApnsResult


class StubSigner:
    def current_token(self) -> str:
        return "stub-jwt"


def _make_client(handler, env: str = "production") -> ApnsClient:
    transport = httpx.MockTransport(handler)
    return ApnsClient(
        signer=StubSigner(),
        bundle_id="org.example.app",
        environment=env,
        transport=transport,
    )


async def test_alert_push_sets_headers_and_payload():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200)

    client = _make_client(handler)
    result = await client.send_alert(
        device_token="abc123",
        title="Print paused",
        body="X1C paused on layer 42",
        event_type="print_paused",
        printer_id="P01",
    )
    assert result.ok is True
    assert result.token_invalid is False
    assert captured["url"].endswith("/3/device/abc123")
    assert "api.push.apple.com" in captured["url"]
    assert captured["headers"]["apns-push-type"] == "alert"
    assert captured["headers"]["apns-topic"] == "org.example.app"
    assert captured["headers"]["authorization"] == "bearer stub-jwt"
    assert captured["body"]["aps"]["alert"]["title"] == "Print paused"
    assert captured["body"]["printer_id"] == "P01"
    assert captured["body"]["event_type"] == "print_paused"


async def test_sandbox_environment_hits_sandbox_host():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200)

    client = _make_client(handler, env="sandbox")
    await client.send_alert(
        device_token="abc", title="t", body="b",
        event_type="print_paused", printer_id="P01",
    )
    assert "api.sandbox.push.apple.com" in captured["url"]


async def test_live_activity_update_uses_liveactivity_push_type():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200)

    client = _make_client(handler)
    await client.send_live_activity_update(
        activity_token="act123",
        content_state={"progress": 0.42, "state": "printing"},
        stale_after_seconds=3600,
    )
    assert captured["headers"]["apns-push-type"] == "liveactivity"
    assert captured["headers"]["apns-topic"] == "org.example.app.push-type.liveactivity"
    assert captured["body"]["aps"]["event"] == "update"
    assert captured["body"]["aps"]["content-state"]["progress"] == 0.42


async def test_410_unregistered_marks_token_invalid():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            410, json={"reason": "Unregistered"},
        )

    client = _make_client(handler)
    result = await client.send_alert(
        device_token="dead", title="t", body="b",
        event_type="print_paused", printer_id="P01",
    )
    assert result.ok is False
    assert result.token_invalid is True
    assert result.reason == "Unregistered"


async def test_500_error_not_token_invalid():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"reason": "InternalServerError"})

    client = _make_client(handler)
    result = await client.send_alert(
        device_token="tok", title="t", body="b",
        event_type="print_paused", printer_id="P01",
    )
    assert result.ok is False
    assert result.token_invalid is False


async def test_live_activity_start_shape_and_priority():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200)

    attributes = {"printer_id": "P01", "printer_name": "X1C"}
    content_state = {"progress": 0.0, "state": "starting", "layer": 0}

    client = _make_client(handler)
    result = await client.send_live_activity_start(
        start_token="start-tok-xyz",
        attributes_type="PrintActivityAttributes",
        attributes=attributes,
        content_state=content_state,
        stale_after_seconds=3600,
    )

    assert result.ok is True
    assert captured["url"].endswith("/3/device/start-tok-xyz")
    assert captured["headers"]["apns-push-type"] == "liveactivity"
    assert captured["headers"]["apns-topic"] == "org.example.app.push-type.liveactivity"
    assert captured["headers"]["apns-priority"] == "10"

    aps = captured["body"]["aps"]
    assert aps["event"] == "start"
    assert aps["attributes-type"] == "PrintActivityAttributes"
    assert aps["attributes"] == attributes
    assert aps["content-state"] == content_state
    assert aps["stale-date"] > aps["timestamp"]


async def test_live_activity_end_shape_and_dismissal():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200)

    content_state = {"progress": 1.0, "state": "finished", "layer": 250}

    client = _make_client(handler)
    result = await client.send_live_activity_end(
        activity_token="act-tok-end",
        content_state=content_state,
        dismissal_seconds_from_now=3600,
    )

    assert result.ok is True
    assert captured["url"].endswith("/3/device/act-tok-end")
    assert captured["headers"]["apns-push-type"] == "liveactivity"
    assert captured["headers"]["apns-priority"] == "10"

    aps = captured["body"]["aps"]
    assert aps["event"] == "end"
    assert aps["content-state"] == content_state
    assert aps["dismissal-date"] - aps["timestamp"] == 3600
