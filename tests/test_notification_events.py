"""Tests for NotificationHub event detection (diff rules only)."""

from __future__ import annotations

import pytest

from app.models import (
    HMSCode, PrinterState, PrinterStatus, PrintJob,
)
from app.notification_events import EventType, NotificationEvent
from app.notification_hub import detect_events


def _status(
    state: PrinterState = PrinterState.idle,
    online: bool = True,
    progress: int = 0,
    layer: int = 0,
    remaining: int = 0,
    hms: list[HMSCode] | None = None,
) -> PrinterStatus:
    return PrinterStatus(
        id="P01", name="X1C", online=online, state=state,
        job=PrintJob(progress=progress, current_layer=layer, remaining_minutes=remaining) if progress or layer or remaining else None,
        hms_codes=hms or [],
    )


def _types(events: list[NotificationEvent]) -> list[EventType]:
    return [e.event_type for e in events]


def test_idle_to_printing_emits_print_started():
    prev = _status(PrinterState.idle)
    new = _status(PrinterState.printing, progress=1)
    assert _types(detect_events(prev, new)) == [EventType.print_started]


def test_printing_to_paused_emits_print_paused():
    prev = _status(PrinterState.printing, progress=30)
    new = _status(PrinterState.paused, progress=30)
    assert _types(detect_events(prev, new)) == [EventType.print_paused]


def test_paused_to_printing_emits_print_resumed_not_started():
    prev = _status(PrinterState.paused, progress=30)
    new = _status(PrinterState.printing, progress=30)
    assert _types(detect_events(prev, new)) == [EventType.print_resumed]


def test_printing_to_finished_emits_print_finished():
    prev = _status(PrinterState.printing, progress=99)
    new = _status(PrinterState.finished, progress=100)
    events = detect_events(prev, new)
    assert EventType.print_finished in _types(events)


def test_printing_to_cancelled_emits_print_cancelled():
    prev = _status(PrinterState.printing, progress=50)
    new = _status(PrinterState.cancelled, progress=50)
    assert _types(detect_events(prev, new)) == [EventType.print_cancelled]


def test_printing_to_error_emits_print_failed():
    prev = _status(PrinterState.printing, progress=50)
    new = _status(PrinterState.error, progress=50)
    assert _types(detect_events(prev, new)) == [EventType.print_failed]


def test_online_to_offline_while_printing_emits_printer_offline_active():
    prev = _status(PrinterState.printing, progress=50, online=True)
    new = _status(PrinterState.printing, progress=50, online=False)
    assert _types(detect_events(prev, new)) == [EventType.printer_offline_active]


def test_online_to_offline_while_idle_emits_nothing():
    prev = _status(PrinterState.idle, online=True)
    new = _status(PrinterState.idle, online=False)
    assert detect_events(prev, new) == []


def test_progress_tick_on_1pct_change():
    prev = _status(PrinterState.printing, progress=50, layer=100, remaining=60)
    new = _status(PrinterState.printing, progress=51, layer=100, remaining=60)
    assert _types(detect_events(prev, new)) == [EventType.progress_tick]


def test_progress_tick_on_layer_change():
    prev = _status(PrinterState.printing, progress=50, layer=100, remaining=60)
    new = _status(PrinterState.printing, progress=50, layer=101, remaining=60)
    assert _types(detect_events(prev, new)) == [EventType.progress_tick]


def test_progress_tick_on_5min_remaining_change():
    prev = _status(PrinterState.printing, progress=50, layer=100, remaining=60)
    new = _status(PrinterState.printing, progress=50, layer=100, remaining=54)
    assert _types(detect_events(prev, new)) == [EventType.progress_tick]


def test_no_progress_tick_for_small_change():
    prev = _status(PrinterState.printing, progress=50, layer=100, remaining=60)
    new = _status(PrinterState.printing, progress=50, layer=100, remaining=59)
    assert detect_events(prev, new) == []


def test_new_hms_code_emits_hms_warning():
    prev = _status(PrinterState.printing, progress=50)
    new = _status(
        PrinterState.printing, progress=50,
        hms=[HMSCode(attr="0300200000010001", code="0001")],
    )
    events = detect_events(prev, new)
    hms_events = [e for e in events if e.event_type == EventType.hms_warning]
    assert len(hms_events) == 1
    assert hms_events[0].hms_code == "0300200000010001"


def test_existing_hms_code_does_not_re_emit():
    prev = _status(
        PrinterState.printing, progress=50,
        hms=[HMSCode(attr="AAAA", code="BBBB")],
    )
    new = _status(
        PrinterState.printing, progress=50,
        hms=[HMSCode(attr="AAAA", code="BBBB")],
    )
    events = [e for e in detect_events(prev, new) if e.event_type == EventType.hms_warning]
    assert events == []


def test_cleared_hms_does_not_emit():
    prev = _status(
        PrinterState.printing, progress=50,
        hms=[HMSCode(attr="AAAA", code="BBBB")],
    )
    new = _status(PrinterState.printing, progress=50, hms=[])
    events = [e for e in detect_events(prev, new) if e.event_type == EventType.hms_warning]
    assert events == []
