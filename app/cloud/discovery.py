"""Discover the printers bound to the signed-in Bambu account.

The plugin's ``get_user_print_info`` returns the account's device list using
its own stored session token, so discovery works without a Python-side token
and without a re-login (it runs at startup on a restored session and right
after a paste login). Discovered devices are merged into the printer config so
the real name and model show up instead of hand-entered placeholders.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from app.cloud.plugin_host import PluginHost
from app.config import PrinterConfig

logger = logging.getLogger("bambu.cloud.discovery")


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
    configs: list[PrinterConfig], devices: list[dict]
) -> tuple[list[PrinterConfig], bool]:
    """Upsert ``devices`` into ``configs``, returning (new_configs, changed).

    For a known serial the name/model are refreshed from the account (the
    account is the source of truth); an unknown serial is added as a cloud
    printer (empty ip/access_code). Configs for serials not in ``devices`` are
    left untouched, so manually-managed printers survive.
    """
    by_serial = {c.serial: c for c in configs}
    changed = False
    for d in devices:
        serial = str(d.get("dev_id") or "").strip()
        if not serial:
            continue
        name = str(d.get("dev_name") or "").strip()
        model = str(
            d.get("dev_product_name") or d.get("dev_model_name") or ""
        ).strip()
        # The cloud bind list carries the printer's LAN access code, so we can
        # connect over LAN MQTT (bblp + access code) without the user reading
        # it off the printer — the IP comes separately from SSDP discovery.
        access = str(d.get("dev_access_code") or "").strip()
        cur = by_serial.get(serial)
        if cur is None:
            by_serial[serial] = PrinterConfig(
                ip="", access_code=access, serial=serial,
                name=name, machine_model=model,
            )
            changed = True
        else:
            new_name = name or cur.name
            new_model = model or cur.machine_model
            new_access = access or cur.access_code
            if (new_name != cur.name or new_model != cur.machine_model
                    or new_access != cur.access_code):
                by_serial[serial] = replace(
                    cur, name=new_name, machine_model=new_model,
                    access_code=new_access,
                )
                changed = True
    return list(by_serial.values()), changed
