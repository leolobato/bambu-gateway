"""Tests for NotificationHub dispatch, dedupe, and throttle."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.apns_client import ApnsResult
from app.device_store import ActiveActivity, DeviceRecord, DeviceStore
from app.models import PrinterState, PrinterStatus, PrintJob
from app.notification_hub import NotificationHub


@dataclass
class FakeApns:
    alerts: list[dict] = field(default_factory=list)
    updates: list[dict] = field(default_factory=list)
    starts: list[dict] = field(default_factory=list)
    ends: list[dict] = field(default_factory=list)
    invalid_token: str | None = None

    async def send_alert(self, **kwargs) -> ApnsResult:
        self.alerts.append(kwargs)
        return self._result(kwargs.get("device_token"))

    async def send_live_activity_update(self, **kwargs) -> ApnsResult:
        self.updates.append(kwargs)
        return self._result(kwargs.get("activity_token"))

    async def send_live_activity_start(self, **kwargs) -> ApnsResult:
        self.starts.append(kwargs)
        return self._result(kwargs.get("start_token"))

    async def send_live_activity_end(self, **kwargs) -> ApnsResult:
        self.ends.append(kwargs)
        return self._result(kwargs.get("activity_token"))

    def _result(self, token: str | None) -> ApnsResult:
        if self.invalid_token and token == self.invalid_token:
            return ApnsResult(
                ok=False, status_code=410,
                reason="Unregistered", token_invalid=True,
            )
        return ApnsResult(ok=True, status_code=200)


def _status(
    state: PrinterState = PrinterState.printing, progress: int = 50,
    online: bool = True,
) -> PrinterStatus:
    return PrinterStatus(
        id="P01", name="X1C", state=state, online=online,
        job=PrintJob(
            file_name="test.3mf", progress=progress,
            current_layer=10, total_layers=100, remaining_minutes=30,
        ),
    )


def _make_hub(tmp_path, apns: FakeApns) -> tuple[NotificationHub, DeviceStore]:
    store = DeviceStore(tmp_path / "devices.json")
    hub = NotificationHub(apns=apns, device_store=store)
    hub.start()
    hub._seen_printers.add("P01")  # skip first-status guard for tests
    return hub, store


def _wait_for(predicate, timeout: float = 1.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timeout waiting for predicate")


def test_pause_transition_sends_alert_to_subscribed_devices(tmp_path):
    apns = FakeApns()
    hub, store = _make_hub(tmp_path, apns)
    store.upsert_device(DeviceRecord(
        id="dev", name="iPhone", device_token="tok",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    try:
        hub.on_status_change(
            _status(PrinterState.printing), _status(PrinterState.paused),
        )
        _wait_for(lambda: len(apns.alerts) == 1)
        assert apns.alerts[0]["device_token"] == "tok"
        assert apns.alerts[0]["event_type"] == "print_paused"
    finally:
        hub.stop()


def test_duplicate_pause_within_30s_is_deduped(tmp_path):
    apns = FakeApns()
    hub, store = _make_hub(tmp_path, apns)
    store.upsert_device(DeviceRecord(
        id="dev", name="iPhone", device_token="tok",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    try:
        prev, new = _status(PrinterState.printing), _status(PrinterState.paused)
        hub.on_status_change(prev, new)
        hub.on_status_change(prev, new)
        _wait_for(lambda: len(apns.alerts) >= 1)
        time.sleep(0.2)
        assert len(apns.alerts) == 1
    finally:
        hub.stop()


def test_progress_tick_throttled_to_once_per_10s_per_printer(tmp_path):
    apns = FakeApns()
    hub, store = _make_hub(tmp_path, apns)
    store.upsert_device(DeviceRecord(
        id="dev", name="iPhone", device_token="tok",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    store.add_activity(ActiveActivity(
        device_id="dev", printer_id="P01", activity_update_token="act-tok",
    ))
    try:
        prev = _status(PrinterState.printing, progress=50)
        for pct in (51, 52, 53, 54, 55):
            hub.on_status_change(
                prev, _status(PrinterState.printing, progress=pct),
            )
            prev = _status(PrinterState.printing, progress=pct)
        _wait_for(lambda: len(apns.updates) >= 1)
        time.sleep(0.2)
        assert len(apns.updates) == 1
    finally:
        hub.stop()


def test_invalid_token_response_is_removed_from_store(tmp_path):
    apns = FakeApns(invalid_token="tok")
    hub, store = _make_hub(tmp_path, apns)
    store.upsert_device(DeviceRecord(
        id="dev", name="iPhone", device_token="tok",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    try:
        hub.on_status_change(
            _status(PrinterState.printing), _status(PrinterState.paused),
        )
        _wait_for(lambda: store.get_device("dev").device_token == "")
    finally:
        hub.stop()


def test_print_started_sends_push_to_start_when_no_activity(tmp_path):
    apns = FakeApns()
    hub, store = _make_hub(tmp_path, apns)
    store.upsert_device(DeviceRecord(
        id="dev", name="iPhone", device_token="tok",
        live_activity_start_token="start-tok", subscribed_printers=["*"],
    ))
    try:
        hub.on_status_change(
            _status(PrinterState.idle, progress=0),
            _status(PrinterState.printing, progress=1),
        )
        _wait_for(lambda: len(apns.starts) == 1)
        assert apns.starts[0]["start_token"] == "start-tok"
    finally:
        hub.stop()


def test_print_started_skips_push_to_start_when_activity_exists(tmp_path):
    apns = FakeApns()
    hub, store = _make_hub(tmp_path, apns)
    store.upsert_device(DeviceRecord(
        id="dev", name="iPhone", device_token="tok",
        live_activity_start_token="start-tok", subscribed_printers=["*"],
    ))
    store.add_activity(ActiveActivity(
        device_id="dev", printer_id="P01", activity_update_token="act",
    ))
    try:
        hub.on_status_change(
            _status(PrinterState.idle, progress=0),
            _status(PrinterState.printing, progress=1),
        )
        time.sleep(0.2)
        assert apns.starts == []
    finally:
        hub.stop()


def test_terminal_state_ends_live_activity(tmp_path):
    apns = FakeApns()
    hub, store = _make_hub(tmp_path, apns)
    store.upsert_device(DeviceRecord(
        id="dev", name="iPhone", device_token="tok",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    store.add_activity(ActiveActivity(
        device_id="dev", printer_id="P01", activity_update_token="act",
    ))
    try:
        hub.on_status_change(
            _status(PrinterState.printing, progress=99),
            _status(PrinterState.finished, progress=100),
        )
        _wait_for(lambda: len(apns.ends) == 1)
        assert apns.ends[0]["activity_token"] == "act"
        # And removed from store
        assert store.list_activities_for_printer("P01") == []
    finally:
        hub.stop()
