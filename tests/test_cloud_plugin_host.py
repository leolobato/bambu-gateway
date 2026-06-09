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


async def test_plugin_host_handles_responses_larger_than_64kib(fake_host_cmd):
    """bridge.poll_events can return several full push_all payloads in one
    JSONL line; asyncio's default 64KiB stream limit killed the reader and
    every later call hung forever."""
    async with PluginHost(cmd=fake_host_cmd) as host:
        big = "x" * (256 * 1024)
        result = await host.call("echo", {"blob": big})
        assert result["blob"] == big
        # The host must still be usable afterwards.
        assert await host.call("echo", {"ok": 1}) == {"ok": 1}


async def test_plugin_host_call_fails_fast_when_reader_is_dead(fake_host_cmd):
    """If the read loop dies while the process is alive, calls must raise
    instead of awaiting a future nothing will ever resolve."""
    async with PluginHost(cmd=fake_host_cmd) as host:
        await host.call("echo", {})  # warm up
        host._reader_task.cancel()
        await asyncio.sleep(0)  # let the cancellation land
        with pytest.raises(PluginHostError):
            await host.call("echo", {})


async def test_plugin_host_call_times_out(fake_host_cmd):
    """A host that accepts a request but never answers must not hang the
    caller forever."""
    async with PluginHost(
        cmd=fake_host_cmd, env={"FAKE_HOST_HANG_METHOD": "echo"},
    ) as host:
        with pytest.raises(PluginHostError) as exc_info:
            await host.call("echo", {}, timeout=0.2)
        assert "timed out" in str(exc_info.value)
