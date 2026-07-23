from __future__ import annotations

import asyncio
import json

import pytest

from app.cloud.cloud_printer import CloudPrinterClient
from app.config import PrinterConfig
from app.mqtt_client import BambuMQTTClient
from app.print_tracking import PrintEventBroker


@pytest.mark.asyncio
async def test_new_subscriber_gets_accumulated_snapshot_and_fanout():
    broker = PrintEventBroker("S1")
    broker.update({
        "task_id": "10",
        "subtask_id": "20",
        "subtask_name": "cube.3mf",
        "gcode_state": "RUNNING",
        "layer_num": 3,
        "total_layer_num": 100,
        "ams_mapping": [2, 0],
    })
    broker.update({"layer_num": 4, "ams": {"tray_now": 2}})

    async with broker.subscribe() as first, broker.subscribe() as second:
        a = await asyncio.wait_for(first.get(), 0.5)
        b = await asyncio.wait_for(second.get(), 0.5)

    assert a == b
    assert a["schema_version"] == 1
    assert a["job_key"] == "printer:10:20:cube.3mf"
    assert a["layer"] == {"current": 4, "total": 100}
    assert a["ams_mapping"] == [2, 0]
    assert a["active_tray"] == 2
    assert broker.subscriber_count == 0


@pytest.mark.asyncio
async def test_bounded_queue_replaces_stale_snapshot_with_latest():
    broker = PrintEventBroker("S1", max_queue_size=1)
    async with broker.subscribe() as queue:
        await queue.get()  # initial empty snapshot
        broker.update({"gcode_state": "RUNNING", "layer_num": 1})
        broker.update({"layer_num": 9})
        await asyncio.sleep(0)
        snapshot = await queue.get()
    assert snapshot["layer"]["current"] == 9


@pytest.mark.asyncio
async def test_subscription_holds_and_releases_connection_lease():
    calls: list[str] = []
    broker = PrintEventBroker(
        "S1",
        acquire_connection=lambda: calls.append("acquire"),
        release_connection=lambda: calls.append("release"),
    )
    async with broker.subscribe():
        assert calls == ["acquire"]
    assert calls == ["acquire", "release"]


def test_registered_source_is_associated_with_reported_job_identity():
    broker = PrintEventBroker("S1")
    pending = broker.register_source(
        data=b"3mf",
        filename="cube.3mf",
        plate_id=1,
        ams_mapping=[1, 3],
    )
    assert broker.source_for(pending).data == b"3mf"

    snapshot = broker.update({
        "task_id": "88",
        "subtask_id": "99",
        "gcode_state": "RUNNING",
        "subtask_name": "cube.3mf",
    })
    # Gateway registrations remain the stable identity even if firmware later
    # supplies task ids, avoiding a false job replacement in consumers.
    assert snapshot["job_key"] == pending
    assert snapshot["ams_mapping"] == [1, 3]
    assert snapshot["gcode_entry"] == "Metadata/plate_1.gcode"
    assert snapshot["source"]["available"] is True
    assert broker.source_for(snapshot["job_key"]).data == b"3mf"


def test_external_job_without_source_is_explicitly_unavailable():
    snapshot = PrintEventBroker("S1").update({
        "gcode_state": "RUNNING",
        "subtask_name": "cloud-only.3mf",
        "layer_num": 12,
    })
    assert snapshot["job_key"] == "printer:name:cloud-only.3mf"
    assert snapshot["source"]["available"] is False
    assert "retrievable" in snapshot["source"]["reason"]


def test_replacement_job_does_not_inherit_previous_job_snapshot_fields():
    broker = PrintEventBroker("S1")
    first = broker.update({
        "task_id": "10",
        "subtask_id": "20",
        "subtask_name": "first.3mf",
        "gcode_state": "RUNNING",
        "layer_num": 8,
        "total_layer_num": 12,
        "ams_mapping": [1, 3],
        "ams": {"tray_now": 3},
    })
    broker.update({"gcode_state": "FINISH"})

    second = broker.update({
        "task_id": "11",
        "subtask_id": "21",
        "subtask_name": "second.3mf",
        "gcode_state": "RUNNING",
        "layer_num": 0,
        "total_layer_num": 20,
    })

    assert second["job_key"] != first["job_key"]
    assert second["job_key"] == "printer:11:21:second.3mf"
    assert second["ams_mapping"] == []
    assert second["active_tray"] is None
    assert second["layer"] == {"current": 0, "total": 20}


@pytest.mark.asyncio
async def test_lan_and_cloud_feed_identical_snapshot_schema(monkeypatch):
    report = {
        "gcode_state": "RUNNING",
        "task_id": "1",
        "subtask_name": "same.3mf",
        "layer_num": 7,
        "total_layer_num": 20,
        "ams_mapping": [0, 2],
        "ams": {"tray_now": "2"},
    }
    lan_broker = PrintEventBroker("S1")
    cloud_broker = PrintEventBroker("S1")

    lan = BambuMQTTClient(PrinterConfig(
        ip="127.0.0.1", access_code="x", serial="S1",
    ))
    lan.set_print_report_callback(lan_broker.update)
    monkeypatch.setattr(lan, "_update_status", lambda payload: None)
    lan._on_message(None, None, type("Message", (), {
        "payload": json.dumps({"print": report}).encode(),
        "topic": "device/S1/report",
    })())

    cloud = CloudPrinterClient(dev_id="S1")
    cloud.set_print_report_callback(cloud_broker.update)
    await cloud.handle_event({
        "kind": "OnMessage",
        "dev_id": "S1",
        "payload": json.dumps({"print": report}),
    })

    lan_snapshot = lan_broker.snapshot()
    cloud_snapshot = cloud_broker.snapshot()
    lan_snapshot.pop("updated_at")
    cloud_snapshot.pop("updated_at")
    assert lan_snapshot == cloud_snapshot
