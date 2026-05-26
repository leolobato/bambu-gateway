"""Tests for the Python PluginHost using the Python fake host."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from app.cloud.plugin_host import PluginHost, PluginHostError


FAKE_HOST = Path(__file__).parent / "cloud_fake_host.py"


@pytest.fixture
def fake_host_cmd():
    return [sys.executable, str(FAKE_HOST)]


async def test_plugin_host_round_trips_echo(fake_host_cmd):
    async with PluginHost(cmd=fake_host_cmd) as host:
        result = await host.call("echo", {"x": 1, "y": "two"})
        assert result == {"x": 1, "y": "two"}


async def test_plugin_host_supports_concurrent_calls(fake_host_cmd):
    async with PluginHost(cmd=fake_host_cmd) as host:
        results = await asyncio.gather(
            host.call("echo", {"i": 0}),
            host.call("echo", {"i": 1}),
            host.call("echo", {"i": 2}),
        )
    assert sorted(r["i"] for r in results) == [0, 1, 2]


async def test_plugin_host_surfaces_rpc_errors(fake_host_cmd):
    async with PluginHost(cmd=fake_host_cmd) as host:
        with pytest.raises(PluginHostError) as exc_info:
            await host.call("does_not_exist", {})
        assert "unknown method" in str(exc_info.value)


async def test_plugin_host_init_and_change_user(fake_host_cmd, tmp_path):
    record_file = tmp_path / "requests.jsonl"
    async with PluginHost(
        cmd=fake_host_cmd,
        env={"FAKE_HOST_RECORD_FILE": str(record_file)},
    ) as host:
        bootstrap = await host.call("init_plugin", {})
        assert bootstrap == {"bootstrap_rc": 0}
        result = await host.call(
            "change_user",
            {"canonical_login": json.dumps(
                {"command": "user_login",
                 "data": {"token": "...", "user_id": "42"}}
            )},
        )
        assert result == {"rc": 0}

    # Confirm the fake host saw both requests in order.
    seen = [json.loads(line) for line in record_file.read_text().splitlines()]
    assert [r["method"] for r in seen] == ["init_plugin", "change_user"]


async def test_plugin_host_raises_when_subprocess_dies(fake_host_cmd):
    async with PluginHost(cmd=fake_host_cmd) as host:
        await host.call("echo", {})  # warm up
        # Kill the subprocess from underneath; subsequent calls must fail.
        host._proc.kill()
        await host._proc.wait()
        with pytest.raises(PluginHostError):
            await host.call("echo", {})
