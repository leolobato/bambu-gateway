"""Pure helpers for the print-session (agent handoff) endpoints."""
from __future__ import annotations


def resolve_plate_type(
    request_plate: str, printer_default: str, authored_plate: str, machine_default: str,
) -> str:
    """First non-empty of request → printer default → authored → machine default."""
    for candidate in (request_plate, printer_default, authored_plate, machine_default):
        if candidate:
            return candidate
    return ""


def build_handoff_url(base: str, job_id: str) -> str:
    return f"{base.rstrip('/')}/print?reprint={job_id}"
