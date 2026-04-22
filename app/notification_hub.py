"""Detects notification-worthy state changes between printer snapshots."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from typing import Protocol

from app.apns_client import ApnsResult
from app.device_store import DeviceStore
from app.hms_codes import pause_reason
from app.models import PrinterState, PrinterStatus
from app.notification_events import EventType, NotificationEvent

logger = logging.getLogger(__name__)


_ACTIVE_STATES = {
    PrinterState.printing, PrinterState.paused, PrinterState.preparing,
}


def detect_events(
    prev: PrinterStatus, new: PrinterStatus,
) -> list[NotificationEvent]:
    events: list[NotificationEvent] = []

    if prev.state != new.state:
        transition_event = _state_transition_event(prev.state, new.state)
        if transition_event is not None:
            events.append(NotificationEvent(
                event_type=transition_event,
                printer_id=new.id,
                snapshot=new,
                prev_snapshot=prev if transition_event == EventType.print_paused else None,
            ))

    # Offline-while-active
    if prev.online and not new.online and prev.state in _ACTIVE_STATES:
        events.append(NotificationEvent(
            event_type=EventType.printer_offline_active,
            printer_id=new.id,
            snapshot=new,
        ))

    # HMS: only emit for codes newly present
    prev_attrs = {c.attr for c in prev.hms_codes}
    for code in new.hms_codes:
        if code.attr not in prev_attrs:
            events.append(NotificationEvent(
                event_type=EventType.hms_warning,
                printer_id=new.id,
                snapshot=new,
                hms_code=code.attr,
            ))

    # Progress ticks — only while printing, no state transition in the same diff
    if (
        not events
        and prev.state == PrinterState.printing
        and new.state == PrinterState.printing
        and new.online
    ):
        if _is_progress_tick(prev, new):
            events.append(NotificationEvent(
                event_type=EventType.progress_tick,
                printer_id=new.id,
                snapshot=new,
            ))

    return events


def _state_transition_event(
    prev: PrinterState, new: PrinterState,
) -> EventType | None:
    if new == PrinterState.printing:
        if prev == PrinterState.paused:
            return EventType.print_resumed
        return EventType.print_started
    if new == PrinterState.paused and prev == PrinterState.printing:
        return EventType.print_paused
    if new == PrinterState.finished:
        return EventType.print_finished
    if new == PrinterState.cancelled:
        return EventType.print_cancelled
    if new == PrinterState.error:
        return EventType.print_failed
    return None


def _is_progress_tick(prev: PrinterStatus, new: PrinterStatus) -> bool:
    prev_job = prev.job
    new_job = new.job
    if new_job is None:
        return False
    prev_progress = prev_job.progress if prev_job else 0
    prev_layer = prev_job.current_layer if prev_job else 0
    prev_remaining = prev_job.remaining_minutes if prev_job else 0

    if abs(new_job.progress - prev_progress) >= 1:
        return True
    if new_job.current_layer != prev_layer:
        return True
    if abs(new_job.remaining_minutes - prev_remaining) >= 5:
        return True
    return False


_DEDUPE_SECONDS = 30.0  # also covers the "error oscillation" case from the spec
_PROGRESS_THROTTLE_SECONDS = 10.0


class _ApnsProtocol(Protocol):
    async def send_alert(self, **kwargs) -> ApnsResult: ...
    async def send_live_activity_update(self, **kwargs) -> ApnsResult: ...
    async def send_live_activity_start(self, **kwargs) -> ApnsResult: ...
    async def send_live_activity_end(self, **kwargs) -> ApnsResult: ...


_ALERT_COPY: dict[EventType, tuple[str, str]] = {
    EventType.print_paused: ("Print paused", "{printer} paused at {progress}%"),
    EventType.print_failed: ("Print failed", "{printer} stopped with an error"),
    EventType.print_cancelled: ("Print cancelled", "{printer} cancelled"),
    EventType.print_finished: ("Print complete", "{printer} finished {file}"),
    EventType.printer_offline_active: (
        "Printer offline", "{printer} lost connection during a print",
    ),
    EventType.hms_warning: ("Printer warning", "{printer} reported code {code}"),
}


_TERMINAL_STATES_EVENT_TYPES = {
    EventType.print_finished, EventType.print_cancelled, EventType.print_failed,
}


def _content_state_from(status: PrinterStatus) -> dict:
    job = status.job
    return {
        "state": status.state.value,
        "stageName": status.stage_name or "",
        "progress": (job.progress / 100.0) if job else 0.0,
        "remainingMinutes": job.remaining_minutes if job else 0,
        "currentLayer": job.current_layer if job else 0,
        "totalLayers": job.total_layers if job else 0,
        "updatedAt": int(time.time()),
    }


class NotificationHub:
    """Serialises event detection + APNs dispatch on a background thread."""

    def __init__(self, apns: _ApnsProtocol, device_store: DeviceStore) -> None:
        self._apns = apns
        self._store = device_store
        self._queue: queue.Queue[NotificationEvent | None] = queue.Queue()
        self._dedupe: dict[tuple[str, str], float] = {}
        self._last_progress: dict[str, float] = {}
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        # Skip the very first status change per printer to avoid spurious
        # notifications on startup (e.g. leftover "finished" state from a
        # previous session being "discovered" as a transition).
        self._seen_printers: set[str] = set()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, name="notification-hub", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    def on_status_change(self, prev: PrinterStatus, new: PrinterStatus) -> None:
        """Invoked from the MQTT thread. Enqueues detected events."""
        printer_id = new.id
        first_time = printer_id not in self._seen_printers
        self._seen_printers.add(printer_id)
        if first_time:
            return  # skip events from initial snapshot
        try:
            for event in detect_events(prev, new):
                self._queue.put(event)
        except Exception:
            logger.exception("detect_events raised")

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            while self._running:
                event = self._queue.get()
                if event is None:
                    break
                try:
                    self._loop.run_until_complete(self._handle(event))
                except Exception:
                    logger.exception("NotificationHub handler failed")
        finally:
            self._loop.close()
            self._loop = None

    async def _handle(self, event: NotificationEvent) -> None:
        if self._is_deduped(event):
            return
        if event.event_type == EventType.progress_tick and self._is_throttled(event):
            return

        if event.event_type == EventType.progress_tick:
            await self._dispatch_live_activity_update(event)
            return

        await self._dispatch_event(event)

    def _is_deduped(self, event: NotificationEvent) -> bool:
        now = time.monotonic()
        expired = [k for k, ts in self._dedupe.items() if now - ts > _DEDUPE_SECONDS]
        for k in expired:
            del self._dedupe[k]
        if event.event_type == EventType.progress_tick:
            return False  # throttled separately
        key = (event.printer_id, event.event_type.value + ":" + event.hms_code)
        if key in self._dedupe:
            return True
        self._dedupe[key] = now
        return False

    def _is_throttled(self, event: NotificationEvent) -> bool:
        now = time.monotonic()
        last = self._last_progress.get(event.printer_id, 0.0)
        if now - last < _PROGRESS_THROTTLE_SECONDS:
            return True
        self._last_progress[event.printer_id] = now
        return False

    async def _dispatch_event(self, event: NotificationEvent) -> None:
        if event.event_type in _ALERT_COPY:
            await self._send_alerts(event)

        if event.event_type == EventType.print_started:
            await self._send_push_to_start(event)
        elif event.event_type in _TERMINAL_STATES_EVENT_TYPES:
            await self._send_live_activity_end(event)
        elif event.event_type in {
            EventType.print_paused, EventType.print_resumed,
            EventType.printer_offline_active,
        }:
            await self._dispatch_live_activity_update(event)

    async def _send_alerts(self, event: NotificationEvent) -> None:
        title_tpl, body_tpl = _ALERT_COPY[event.event_type]
        subscribers = self._store.subscribers_for_printer(event.printer_id)
        layer = event.snapshot.job.current_layer if event.snapshot.job else 0
        progress = event.snapshot.job.progress if event.snapshot.job else 0
        file_name = event.snapshot.job.file_name if event.snapshot.job else ""
        title = title_tpl.format(printer=event.snapshot.name)
        body = body_tpl.format(
            printer=event.snapshot.name,
            layer=layer, progress=progress, file=file_name, code=event.hms_code,
        )
        if event.event_type == EventType.print_paused and event.prev_snapshot is not None:
            reason = pause_reason(
                prev_hms=event.prev_snapshot.hms_codes,
                new_hms=event.snapshot.hms_codes,
                prev_print_error=event.prev_snapshot.print_error,
                new_print_error=event.snapshot.print_error,
            )
            if reason:
                body = f"{body}: {reason}"
        for dev in subscribers:
            if not dev.device_token:
                continue
            result = await self._apns.send_alert(
                device_token=dev.device_token,
                title=title, body=body,
                event_type=event.event_type.value,
                printer_id=event.printer_id,
            )
            self._handle_result(result, dev.device_token)

    async def _send_push_to_start(self, event: NotificationEvent) -> None:
        snapshot = event.snapshot
        subscribers = self._store.subscribers_for_printer(event.printer_id)
        existing_device_ids = {
            a.device_id
            for a in self._store.list_activities_for_printer(event.printer_id)
        }
        attributes = {
            "printerId": snapshot.id,
            "printerName": snapshot.name,
            "fileName": snapshot.job.file_name if snapshot.job else "",
            "thumbnailData": None,
        }
        content = _content_state_from(snapshot)
        for dev in subscribers:
            if dev.id in existing_device_ids:
                continue
            if not dev.live_activity_start_token:
                continue
            result = await self._apns.send_live_activity_start(
                start_token=dev.live_activity_start_token,
                attributes_type="PrintActivityAttributes",
                attributes=attributes,
                content_state=content,
            )
            self._handle_result(result, dev.live_activity_start_token)

    async def _dispatch_live_activity_update(
        self, event: NotificationEvent,
    ) -> None:
        activities = self._store.list_activities_for_printer(event.printer_id)
        content = _content_state_from(event.snapshot)
        for act in activities:
            result = await self._apns.send_live_activity_update(
                activity_token=act.activity_update_token, content_state=content,
            )
            self._handle_result(result, act.activity_update_token)

    async def _send_live_activity_end(self, event: NotificationEvent) -> None:
        activities = self._store.list_activities_for_printer(event.printer_id)
        content = _content_state_from(event.snapshot)
        dismissal = (
            4 * 3600 if event.event_type == EventType.print_finished else 0
        )
        for act in activities:
            result = await self._apns.send_live_activity_end(
                activity_token=act.activity_update_token,
                content_state=content,
                dismissal_seconds_from_now=dismissal,
            )
            self._handle_result(result, act.activity_update_token)
            self._store.remove_activity(act.device_id, act.printer_id)

    def _handle_result(self, result: ApnsResult, token: str) -> None:
        if result.token_invalid and token:
            self._store.invalidate_token(token)
