"""Tests for device registry persistence."""

from __future__ import annotations

import json

import pytest

from app.device_store import DeviceStore, DeviceRecord, ActiveActivity


@pytest.fixture
def store(tmp_path):
    return DeviceStore(tmp_path / "devices.json")


def test_empty_store_returns_no_devices(store):
    assert store.list_devices() == []


def test_register_persists_to_disk(store, tmp_path):
    store.upsert_device(DeviceRecord(
        id="dev-1", name="iPhone",
        device_token="tok-a",
        live_activity_start_token="start-a",
        subscribed_printers=["*"],
    ))
    raw = json.loads((tmp_path / "devices.json").read_text())
    assert len(raw["devices"]) == 1
    assert raw["devices"][0]["id"] == "dev-1"


def test_upsert_updates_existing_device(store):
    store.upsert_device(DeviceRecord(
        id="dev-1", name="iPhone", device_token="old",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    store.upsert_device(DeviceRecord(
        id="dev-1", name="iPhone", device_token="new",
        live_activity_start_token="start", subscribed_printers=["P01"],
    ))
    devs = store.list_devices()
    assert len(devs) == 1
    assert devs[0].device_token == "new"
    assert devs[0].live_activity_start_token == "start"
    assert devs[0].subscribed_printers == ["P01"]


def test_remove_device_also_removes_its_activities(store):
    store.upsert_device(DeviceRecord(
        id="dev-1", name="iPhone", device_token="tok",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    store.add_activity(ActiveActivity(
        device_id="dev-1", printer_id="P01", activity_update_token="upd",
    ))
    store.remove_device("dev-1")
    assert store.list_devices() == []
    assert store.list_activities_for_printer("P01") == []


def test_invalidate_token_removes_just_that_token(store):
    store.upsert_device(DeviceRecord(
        id="dev-1", name="iPhone", device_token="bad",
        live_activity_start_token="good", subscribed_printers=["*"],
    ))
    store.invalidate_token("bad")
    dev = store.list_devices()[0]
    assert dev.device_token == ""
    assert dev.live_activity_start_token == "good"


def test_invalidate_activity_token_removes_activity(store):
    store.upsert_device(DeviceRecord(
        id="dev-1", name="iPhone", device_token="tok",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    store.add_activity(ActiveActivity(
        device_id="dev-1", printer_id="P01", activity_update_token="bad",
    ))
    store.invalidate_token("bad")
    assert store.list_activities_for_printer("P01") == []


def test_subscribers_for_printer_respects_wildcard_and_explicit(store):
    store.upsert_device(DeviceRecord(
        id="dev-a", name="A", device_token="ta",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    store.upsert_device(DeviceRecord(
        id="dev-b", name="B", device_token="tb",
        live_activity_start_token=None, subscribed_printers=["P02"],
    ))
    p01 = {d.id for d in store.subscribers_for_printer("P01")}
    p02 = {d.id for d in store.subscribers_for_printer("P02")}
    assert p01 == {"dev-a"}
    assert p02 == {"dev-a", "dev-b"}


def test_reload_from_disk(tmp_path):
    s1 = DeviceStore(tmp_path / "devices.json")
    s1.upsert_device(DeviceRecord(
        id="dev-1", name="iPhone", device_token="tok",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    s2 = DeviceStore(tmp_path / "devices.json")
    assert len(s2.list_devices()) == 1
    assert s2.list_devices()[0].id == "dev-1"
