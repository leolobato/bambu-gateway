"""Tests for CloudPrinterClient — receives OnMessage events, updates PrinterStatus."""
from __future__ import annotations

import json

import pytest

from app.cloud.cloud_printer import CloudPrinterClient
from app.models import PrinterState


def test_cloud_printer_client_starts_with_unknown_status():
    client = CloudPrinterClient(dev_id="DEV1", name="Test Printer")
    status = client.get_status()
    assert status is not None
    # Default state: offline (no MQTT reports yet).
    assert status.online is False
    assert status.state == PrinterState.offline


async def test_cloud_printer_client_updates_from_on_message_event():
    client = CloudPrinterClient(dev_id="DEV1", name="Test Printer")
    # Mimic the payload shape Bambu's MQTT broadcasts.
    # gcode_state → drives status.state via determine_state()
    # mc_percent   → stored in status.job.progress
    payload = {
        "print": {
            "gcode_state": "RUNNING",
            "mc_percent": 42,
            "subtask_name": "benchy.3mf",
        }
    }
    await client.handle_event({
        "kind": "OnMessage",
        "dev_id": "DEV1",
        "payload": json.dumps(payload),
    })
    status = client.get_status()
    # RUNNING gcode_state → PrinterState.printing (or preparing depending on
    # stg_cur; without stg_cur it resolves to printing)
    assert status.state in (PrinterState.printing, PrinterState.preparing)
    # mc_percent maps to job.progress
    assert status.job is not None
    assert status.job.progress == 42


async def test_cloud_printer_client_ignores_events_for_other_devices():
    client = CloudPrinterClient(dev_id="DEV1", name="Test Printer")
    await client.handle_event({
        "kind": "OnMessage",
        "dev_id": "DEV2",
        "payload": json.dumps({"print": {"gcode_state": "FAILED"}}),
    })
    # No update applied — state remains offline.
    assert client.get_status().state == PrinterState.offline


async def test_cloud_printer_client_ignores_non_on_message_events():
    client = CloudPrinterClient(dev_id="DEV1", name="Test Printer")
    await client.handle_event({
        "kind": "SomethingElse",
        "dev_id": "DEV1",
        "payload": json.dumps({"print": {"gcode_state": "RUNNING"}}),
    })
    assert client.get_status().state == PrinterState.offline


async def test_cloud_printer_client_handles_invalid_json_payload_gracefully():
    client = CloudPrinterClient(dev_id="DEV1", name="Test Printer")
    # Should not raise; status remains unchanged.
    await client.handle_event({
        "kind": "OnMessage",
        "dev_id": "DEV1",
        "payload": "this is not valid json {{{",
    })
    assert client.get_status().state == PrinterState.offline
