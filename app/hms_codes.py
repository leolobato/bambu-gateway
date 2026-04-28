"""Human-readable descriptions for Bambu Health Monitoring System codes.

The mapping data ships in ``app/data/bambu_hms_en.json`` and is sourced from
the community ha-bambulab project, which extracts the strings Bambu Studio
itself uses (https://github.com/greghesp/ha-bambulab — see
``scripts/hms_error_text/hms_en.json``). The file groups codes into:

* ``device_hms`` — keyed by the 16-char hex ``attr`` field reported in the
  ``hms`` MQTT array.
* ``device_error`` — keyed by 8-char hex matching the ``print_error`` int.

Both lookups normalise the input (strip underscores, uppercase) so callers
can pass either ``"0300_2003_0002_0001"`` or ``"0300200300020001"``. Codes
not present in the bundled mapping fall through to a generic "unknown error"
label carrying the raw code so the owner still has something actionable.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.models import HMSCode


_DATA_PATH = Path(__file__).parent / "data" / "bambu_hms_en.json"


def _load_descriptions() -> tuple[dict[str, str], dict[int, str]]:
    with _DATA_PATH.open(encoding="utf-8") as f:
        data = json.load(f)

    def first_description(entry: dict) -> str:
        # entry is {description: [printer_models, ...]} — take the first key.
        return next(iter(entry), "")

    hms_codes = {
        key.upper(): first_description(value)
        for key, value in data.get("device_hms", {}).items()
        if isinstance(value, dict) and value
    }
    print_errors = {
        int(key, 16): first_description(value)
        for key, value in data.get("device_error", {}).items()
        if isinstance(value, dict) and value
    }
    return hms_codes, print_errors


HMS_CODE_DESCRIPTIONS, PRINT_ERROR_DESCRIPTIONS = _load_descriptions()


def describe_hms_code(attr: str) -> str | None:
    """Return a human description for an HMS code, or None if unknown."""
    return HMS_CODE_DESCRIPTIONS.get(attr.replace("_", "").upper())


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
