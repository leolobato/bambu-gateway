"""Tests for CloudPrinterClient.send_command + per-route cloud smoke tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.cloud.cloud_printer import CloudPrinterClient
from app.cloud.plugin_host import PluginHost
from app.mqtt_client import build_pause_command


FAKE_HOST = Path(__file__).parent / "cloud_fake_host.py"


# ---------------------------------------------------------------------------
# C.2 — CloudPrinterClient.send_command
# ---------------------------------------------------------------------------


async def test_send_command_forwards_to_send_message(tmp_path):
    """send_command calls send_message with the serialised envelope and returns rc."""
    record_file = tmp_path / "requests.jsonl"
    client = CloudPrinterClient(dev_id="DEV1")
    pause_envelope = build_pause_command()

    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={"FAKE_HOST_RECORD_FILE": str(record_file)},
    ) as host:
        rc = await client.send_command(host=host, envelope=pause_envelope)

    assert rc == 0
    seen = [json.loads(line) for line in record_file.read_text().splitlines()]
    sm_calls = [r for r in seen if r["method"] == "send_message"]
    assert len(sm_calls) == 1
    p = sm_calls[0]["params"]
    assert p["dev_id"] == "DEV1"
    # payload is JSON-serialised — the plugin expects a string
    assert json.loads(p["payload"]) == pause_envelope


async def test_send_command_returns_nonzero_on_plugin_error(tmp_path):
    """FAKE_HOST_SEND_MESSAGE_RC env knob propagates a non-zero rc."""
    client = CloudPrinterClient(dev_id="DEV1")
    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={"FAKE_HOST_SEND_MESSAGE_RC": "-7"},
    ) as host:
        rc = await client.send_command(
            host=host, envelope={"print": {"command": "x"}}
        )
    assert rc == -7


async def test_send_command_passes_qos(tmp_path):
    """qos parameter is forwarded to the send_message call."""
    record_file = tmp_path / "requests.jsonl"
    client = CloudPrinterClient(dev_id="DEV2")

    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={"FAKE_HOST_RECORD_FILE": str(record_file)},
    ) as host:
        await client.send_command(host=host, envelope={"print": {}}, qos=1)

    seen = [json.loads(line) for line in record_file.read_text().splitlines()]
    sm_calls = [r for r in seen if r["method"] == "send_message"]
    assert sm_calls[0]["params"]["qos"] == 1


# ---------------------------------------------------------------------------
# D.1 — Route-level cloud smoke test
# ---------------------------------------------------------------------------


@pytest.fixture
def cloud_control_app(cloud_app_factory):
    """TestClient with cloud mode enabled and a cloud printer registered.

    Uses the real lifespan, so DEV1's CloudPrinterClient is created exactly
    the way production creates it (host attached, registered in both
    app.state.cloud_printers and PrinterService).
    """
    with cloud_app_factory(
        printers=[{"serial": "DEV1", "ip": "10.0.0.9"}],
        fake_env={},
    ) as client:
        yield client


def test_pause_route_dispatches_to_cloud_when_cloud_mode_on(cloud_control_app):
    """POST /pause reaches the fake host's send_message stub and returns ok."""
    resp = cloud_control_app.post("/api/printers/DEV1/pause")
    assert resp.status_code in (200, 204), resp.text


def test_resume_route_dispatches_to_cloud(cloud_control_app):
    resp = cloud_control_app.post("/api/printers/DEV1/resume")
    assert resp.status_code in (200, 204), resp.text


def test_cancel_route_dispatches_to_cloud(cloud_control_app):
    resp = cloud_control_app.post("/api/printers/DEV1/cancel")
    assert resp.status_code in (200, 204), resp.text


def test_speed_route_dispatches_to_cloud(cloud_control_app):
    resp = cloud_control_app.post(
        "/api/printers/DEV1/speed",
        json={"level": 2},
    )
    assert resp.status_code in (200, 204), resp.text


def test_start_drying_route_dispatches_to_cloud(cloud_control_app):
    resp = cloud_control_app.post(
        "/api/printers/DEV1/ams/0/start-drying",
        json={"temperature": 55, "duration_minutes": 480},
    )
    assert resp.status_code in (200, 204), resp.text


def test_stop_drying_route_dispatches_to_cloud(cloud_control_app):
    resp = cloud_control_app.post("/api/printers/DEV1/ams/0/stop-drying")
    assert resp.status_code in (200, 204), resp.text
