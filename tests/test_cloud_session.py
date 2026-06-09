"""Tests for establishing the plugin's cloud MQTT session (connect + subscribe)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.cloud.plugin_host import PluginHost
from app.cloud.session import establish_session, subscribe_printers

FAKE_HOST = Path(__file__).parent / "cloud_fake_host.py"


def _methods(record_file: Path) -> list[str]:
    return [
        json.loads(line)["method"]
        for line in record_file.read_text().splitlines()
    ]


def _requests(record_file: Path) -> list[dict]:
    return [json.loads(line) for line in record_file.read_text().splitlines()]


async def test_establish_session_connects_then_subscribes(tmp_path):
    record = tmp_path / "rpc.jsonl"
    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={"FAKE_HOST_RECORD_FILE": str(record)},
    ) as host:
        ok = await establish_session(host=host, dev_ids=["DEV1", "DEV2"])

    assert ok
    methods = _methods(record)
    assert methods.index("connect_server") < methods.index("start_subscribe")
    assert methods.index("start_subscribe") < methods.index("add_subscribe")

    reqs = _requests(record)
    start_sub = next(r for r in reqs if r["method"] == "start_subscribe")
    assert start_sub["params"]["module"] == "app"
    add_sub = next(r for r in reqs if r["method"] == "add_subscribe")
    assert add_sub["params"]["dev_ids"] == ["DEV1", "DEV2"]


async def test_establish_session_aborts_when_connect_fails(tmp_path):
    record = tmp_path / "rpc.jsonl"
    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={
            "FAKE_HOST_RECORD_FILE": str(record),
            "FAKE_HOST_CONNECT_SERVER_RC": "-1",
        },
    ) as host:
        ok = await establish_session(host=host, dev_ids=["DEV1"])

    assert not ok
    methods = _methods(record)
    assert "connect_server" in methods
    assert "start_subscribe" not in methods
    assert "add_subscribe" not in methods


async def test_subscribe_printers_with_no_devices_skips_rpc(tmp_path):
    record = tmp_path / "rpc.jsonl"
    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={"FAKE_HOST_RECORD_FILE": str(record)},
    ) as host:
        ok = await subscribe_printers(host=host, dev_ids=[])

    assert ok
    assert not record.exists() or "add_subscribe" not in _methods(record)


def test_lifespan_establishes_session_when_already_signed_in(cloud_app_factory):
    with cloud_app_factory(
        printers=[{"serial": "DEVA", "ip": "10.0.0.5"}],
        fake_env={"FAKE_HOST_USER_LOGGED_IN": "1"},
    ) as client:
        methods = _methods(client.rpc_record_file)
        assert "connect_server" in methods
        assert "start_subscribe" in methods
        reqs = _requests(client.rpc_record_file)
        add_sub = next(r for r in reqs if r["method"] == "add_subscribe")
        assert add_sub["params"]["dev_ids"] == ["DEVA"]


def test_lifespan_skips_session_when_not_signed_in(cloud_app_factory):
    with cloud_app_factory(
        printers=[{"serial": "DEVA", "ip": "10.0.0.5"}],
        fake_env={},
    ) as client:
        methods = _methods(client.rpc_record_file)
        assert "is_user_login" in methods
        assert "connect_server" not in methods


def test_paste_login_establishes_session(cloud_app_factory, monkeypatch):
    """A successful paste login must connect + subscribe so status starts flowing."""
    import httpx
    from unittest.mock import patch

    def profile_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"uidStr": "42", "name": "Alice", "account": "a@b"},
        )

    with cloud_app_factory(
        printers=[{"serial": "DEVA", "ip": "10.0.0.5"}],
        fake_env={"FAKE_HOST_USER_LOGGED_IN": "1"},
    ) as client:
        # Ignore whatever the lifespan already recorded; only inspect calls
        # made after this point.
        before = len(_methods(client.rpc_record_file))
        with patch(
            "app.cloud.auth_routes._make_http_client",
            return_value=httpx.AsyncClient(
                transport=httpx.MockTransport(profile_handler)
            ),
        ):
            resp = client.post(
                "/api/cloud/auth/paste",
                json={"pasted_url": "http://localhost:13618/?ticket=tk_abc"},
            )
        assert resp.status_code == 200, resp.text
        after = _methods(client.rpc_record_file)[before:]
        assert "connect_server" in after
        assert "add_subscribe" in after
