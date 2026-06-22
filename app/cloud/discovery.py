"""Discover the printers bound to the signed-in Bambu account.

The plugin's ``get_user_print_info`` returns the account's device list using
its own stored session token, so discovery works without a Python-side token
and without a re-login (it runs at startup on a restored session and right
after a paste login). Discovered devices are merged into the printer config so
the real name and model show up instead of hand-entered placeholders.
"""
from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Callable

from app.cloud.plugin_host import PluginHost
from app.config import PrinterConfig

logger = logging.getLogger("bambu.cloud.discovery")


def _normalize_model(value: str) -> str:
    """Lowercase, drop a ``Bambu Lab`` prefix, collapse whitespace.

    The cloud bind list reports a bare product name (``A1 mini``) while the
    slicer catalogue spells the same printer as ``Bambu Lab A1 mini``.
    Normalizing both sides lets one match the other.
    """
    text = re.sub(r"^bambu\s*lab\s*", "", value.strip().lower())
    return re.sub(r"\s+", " ", text).strip()


def resolve_machine_setting_id(
    raw: str, machines: list[dict], *, nozzle: str = "0.4"
) -> str | None:
    """Map a cloud product/model string to a slicer machine ``setting_id``.

    The UI's filament filtering keys off the slicer ``setting_id`` (e.g.
    ``GM020``), not the human model name, so a cloud-discovered printer whose
    ``machine_model`` is the bare product name (``A1 mini``) can't be resolved
    to compatible filaments. This finds the matching catalogue entry — by
    ``printer_model`` first, then display ``name`` — preferring the requested
    nozzle (Bambu's stock 0.4 by default) when a model ships several. Returns
    ``None`` when nothing matches so the caller keeps the raw value.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    # Already a setting_id (re-running discovery, or a hand-entered id).
    if any(m.get("setting_id") == raw for m in machines):
        return raw
    target = _normalize_model(raw)
    if not target:
        return None
    matches = [
        m for m in machines
        if _normalize_model(str(m.get("printer_model") or "")) == target
    ]
    if not matches:
        matches = [
            m for m in machines
            if _normalize_model(str(m.get("name") or "")).startswith(target + " ")
        ]
    if not matches:
        return None
    want = str(nozzle).strip()
    for m in matches:
        if str(m.get("nozzle_diameter") or "").strip() == want:
            sid = str(m.get("setting_id") or "").strip()
            if sid:
                return sid
    return str(matches[0].get("setting_id") or "").strip() or None


async def fetch_user_devices(*, host: PluginHost) -> list[dict]:
    """Return the raw device dicts from ``get_user_print_info``.

    Each device carries ``dev_id``, ``dev_name``, ``dev_model_name``,
    ``dev_product_name`` and ``dev_online`` (DevManager.cpp:719). Returns an
    empty list when the account has no bound devices.
    """
    result = await host.call("get_user_print_info", {})
    devices = result.get("devices")
    return devices if isinstance(devices, list) else []


def merge_discovered_devices(
    configs: list[PrinterConfig],
    devices: list[dict],
    *,
    resolve_model: Callable[[str], str | None] | None = None,
) -> tuple[list[PrinterConfig], bool]:
    """Upsert ``devices`` into ``configs``, returning (new_configs, changed).

    For a known serial the name/model are refreshed from the account (the
    account is the source of truth); an unknown serial is added as a cloud
    printer (empty ip/access_code). Configs for serials not in ``devices`` are
    left untouched, so manually-managed printers survive.

    ``resolve_model`` (when given) maps the device's bare product name to a
    slicer ``setting_id`` so ``machine_model`` matches the manual-add
    convention and the UI can filter filaments. It's idempotent — a resolved
    id maps to itself — and a ``None`` result never clobbers an
    already-resolved id (so a slicer outage degrades gracefully).
    """
    by_serial = {c.serial: c for c in configs}
    changed = False
    for d in devices:
        serial = str(d.get("dev_id") or "").strip()
        if not serial:
            continue
        name = str(d.get("dev_name") or "").strip()
        raw_model = str(
            d.get("dev_product_name") or d.get("dev_model_name") or ""
        ).strip()
        resolved = (
            resolve_model(raw_model) if (resolve_model and raw_model) else None
        )
        # The cloud bind list carries the printer's LAN access code, so we can
        # connect over LAN MQTT (bblp + access code) without the user reading
        # it off the printer — the IP comes separately from SSDP discovery.
        access = str(d.get("dev_access_code") or "").strip()
        cur = by_serial.get(serial)
        if cur is None:
            by_serial[serial] = PrinterConfig(
                ip="", access_code=access, serial=serial,
                name=name, machine_model=resolved or raw_model,
            )
            changed = True
        else:
            new_name = name or cur.name
            new_model = resolved or cur.machine_model or raw_model
            new_access = access or cur.access_code
            if (new_name != cur.name or new_model != cur.machine_model
                    or new_access != cur.access_code):
                by_serial[serial] = replace(
                    cur, name=new_name, machine_model=new_model,
                    access_code=new_access,
                )
                changed = True
    return list(by_serial.values()), changed
