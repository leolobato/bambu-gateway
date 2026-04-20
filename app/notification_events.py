"""Event record types emitted by NotificationHub."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.models import PrinterStatus


class EventType(str, Enum):
    print_started = "print_started"
    print_paused = "print_paused"
    print_resumed = "print_resumed"
    print_finished = "print_finished"
    print_cancelled = "print_cancelled"
    print_failed = "print_failed"
    printer_offline_active = "printer_offline_active"
    hms_warning = "hms_warning"
    progress_tick = "progress_tick"


@dataclass
class NotificationEvent:
    event_type: EventType
    printer_id: str
    snapshot: PrinterStatus
    hms_code: str = ""  # populated for hms_warning
