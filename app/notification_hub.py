"""Detects notification-worthy state changes between printer snapshots."""

from __future__ import annotations

from app.models import PrinterState, PrinterStatus
from app.notification_events import EventType, NotificationEvent


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
