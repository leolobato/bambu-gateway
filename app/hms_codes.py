"""Human-readable descriptions for Bambu Health Monitoring System codes.

HMS codes are 4-group hex strings like ``0300_0C00_0002_0002``. Bambu does not
publish an official machine-readable mapping; entries below come from community
observation of printer behaviour. Codes not present here fall through to a
generic "unknown error" label — the gateway logs the raw code at INFO level so
this table can be expanded as new codes are seen in the wild.
"""

from __future__ import annotations

from app.models import HMSCode


HMS_CODE_DESCRIPTIONS: dict[str, str] = {
    # Filament runout / AMS-side filament issues
    "0300_0C00_0002_0001": "AMS filament is missing",
    "0300_0C00_0002_0002": "AMS filament is missing",
    "0300_0D00_0002_0003": "External spool has run out",
    "0300_0F00_0002_0001": "Filament has run out",
    # Filament path / extrusion problems
    "0300_2003_0002_0001": "Filament is broken or tangled",
    "0300_2006_0002_0002": "Filament is tangled in the AMS",
    # Enclosure
    "07FF_2000_0002_000D": "Front cover is open",
    "07FF_2000_0002_0008": "Printer enclosure is open",
}


# Some Bambu errors are reported only via ``print_error`` (a 32-bit int),
# without a parallel HMS entry — AMS spool-stuck is one example. Codes here
# are keyed by their integer value; forums reference them in hex (0x........).
PRINT_ERROR_DESCRIPTIONS: dict[int, str] = {
    0x12008010: "AMS cannot load filament — spool may be stuck",  # 302022672
    0x12008007: "AMS filament load failed",  # 302022663
}


def describe_hms_code(attr: str) -> str | None:
    """Return a human description for an HMS code, or None if unknown."""
    return HMS_CODE_DESCRIPTIONS.get(attr)


def describe_print_error(code: int) -> str | None:
    """Return a human description for a print_error code, or None if unknown."""
    return PRINT_ERROR_DESCRIPTIONS.get(code)


def current_error_description(
    hms_codes: list[HMSCode],
    print_error: int,
) -> str | None:
    """Return a human-readable description of the currently-present error,
    or None if no error signal is active.

    Unlike :func:`pause_reason`, this reads the current snapshot without
    needing a previous one — suited for rendering "the printer is stuck
    because X" in the UI.
    """
    if hms_codes:
        first = hms_codes[0]
        description = describe_hms_code(first.attr)
        if description:
            return description
        return f"unknown error (code {first.attr})"

    if print_error != 0:
        description = describe_print_error(print_error)
        if description:
            return description
        return f"unknown error (code 0x{print_error:08X})"

    return None


def pause_reason(
    prev_hms: list[HMSCode],
    new_hms: list[HMSCode],
    prev_print_error: int,
    new_print_error: int,
) -> str | None:
    """Build a human-readable pause reason from the transition.

    Returns None for a user-initiated pause (no new error signal). For an
    error-caused pause returns either the mapped description or an
    "unknown error" fallback carrying the raw code so the owner still has
    something actionable.
    """
    prev_attrs = {c.attr for c in prev_hms}
    new_codes = [c for c in new_hms if c.attr not in prev_attrs]

    if new_codes:
        first = new_codes[0]
        description = describe_hms_code(first.attr)
        if description:
            return description
        return f"unknown error (code {first.attr})"

    if new_print_error != 0 and new_print_error != prev_print_error:
        description = describe_print_error(new_print_error)
        if description:
            return description
        return f"unknown error (code 0x{new_print_error:08X})"

    return None
