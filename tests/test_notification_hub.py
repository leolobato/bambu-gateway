"""Tests for NotificationHub dispatch, dedupe, and throttle."""

from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from app.apns_client import ApnsResult
from app.device_store import ActiveActivity, DeviceRecord, DeviceStore
from app.models import PrinterState, PrinterStatus, PrintJob
from app.notification_hub import NotificationHub
from app.slice_jobs import SliceJob, SliceJobStore


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


def _make_hub(
    tmp_path, apns: FakeApns, slice_store: SliceJobStore | None = None,
) -> tuple[NotificationHub, DeviceStore, SliceJobStore]:
    store = DeviceStore(tmp_path / "devices.json")
    if slice_store is None:
        (tmp_path / "slice_jobs").mkdir(exist_ok=True)
        slice_store = SliceJobStore(tmp_path / "slice_jobs.json")
    hub = NotificationHub(
        apns=apns, device_store=store, slice_store=slice_store,
    )
    hub.start()
    hub._seen_printers.add("P01")  # skip first-status guard for tests
    return hub, store, slice_store


def _wait_for(predicate, timeout: float = 1.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timeout waiting for predicate")


def test_pause_transition_sends_alert_to_subscribed_devices(tmp_path):
    apns = FakeApns()
    hub, store, _slice_store = _make_hub(tmp_path, apns)
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
    hub, store, _slice_store = _make_hub(tmp_path, apns)
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
    hub, store, _slice_store = _make_hub(tmp_path, apns)
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
    hub, store, _slice_store = _make_hub(tmp_path, apns)
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
    hub, store, _slice_store = _make_hub(tmp_path, apns)
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
    hub, store, _slice_store = _make_hub(tmp_path, apns)
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
    hub, store, _slice_store = _make_hub(tmp_path, apns)
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


def _seed_thumbnail_job(slice_store: SliceJobStore, filename: str) -> None:
    """Synchronously seed a SliceJob with a real PNG thumbnail."""
    import asyncio
    img = Image.new("RGB", (256, 256), (200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    job = SliceJob.new(
        filename=filename,
        machine_profile="GM014",
        process_profile="0.20mm",
        filament_profiles={"0": "GFL99"},
        plate_id=1,
        plate_type="",
        project_filament_count=1,
        printer_id="P01",
        auto_print=False,
        input_path=Path(slice_store._blob_dir) / f"{filename}.in.3mf",
    )
    job.thumbnail = data_url
    asyncio.run(slice_store.upsert(job))


def test_print_started_includes_thumbnail_when_slice_job_matches(tmp_path):
    apns = FakeApns()
    hub, store, slice_store = _make_hub(tmp_path, apns)
    _seed_thumbnail_job(slice_store, "test.3mf")
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
        attributes = apns.starts[0]["attributes"]
        thumb = attributes.get("thumbnailData")
        assert isinstance(thumb, str)
        assert len(thumb) > 0
        assert len(thumb) <= 2400
        # Decoded bytes must be a JPEG.
        assert base64.b64decode(thumb)[:3] == b"\xff\xd8\xff"
    finally:
        hub.stop()


def test_print_started_thumbnail_is_none_when_no_slice_job_matches(tmp_path):
    apns = FakeApns()
    hub, store, _slice_store = _make_hub(tmp_path, apns)
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
        assert apns.starts[0]["attributes"]["thumbnailData"] is None
    finally:
        hub.stop()
