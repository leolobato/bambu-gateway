"""Establish the plugin's cloud MQTT session: connect + subscribe.

The Bambu plugin does not connect to the cloud broker on its own — after a
logged-in session exists someone must call ``connect_server`` and subscribe
the printer serials, mirroring OrcaSlicer's post-login sequence
(``GUI_App::on_user_login_handle`` → ``connect_server`` +
``DeviceManager::add_user_subscribe``; ``start_subscribe("app")`` while the
app is active). Without these calls no OnMessage event ever arrives.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Sequence

from app.cloud.plugin_host import PluginHost, PluginHostError

logger = logging.getLogger("bambu.cloud.session")

# Keep strong refs to fire-and-forget tasks so they aren't GC'd mid-flight.
_bg_tasks: set[asyncio.Task] = set()

# Full-snapshot + module-version requests, mirroring the LAN client's
# request_pushall()/request_version(). The plugin subscription only carries
# the printer's incremental deltas; without an explicit pushall the gateway
# never receives gcode_state, temps or the AMS block, so a freshly-subscribed
# cloud printer looks "offline" even while it's powered on and printing.
_PUSHALL = {"pushing": {"sequence_id": "0", "command": "pushall"}}
_GET_VERSION = {"info": {"sequence_id": "0", "command": "get_version"}}

_SUBSCRIBE_MODULE = "app"


async def establish_session(
    *, host: PluginHost, dev_ids: Sequence[str],
) -> bool:
    """Connect the cloud MQTT relay and subscribe to ``dev_ids``.

    Call whenever a logged-in session becomes available: at startup when the
    plugin restored a persisted login, and right after a successful paste
    login. Returns True when every step returned rc == 0.
    """
    rc = (await host.call("connect_server", {})).get("rc", -1)
    if rc != 0:
        logger.error("connect_server failed rc=%s", rc)
        return False
    rc = (
        await host.call("start_subscribe", {"module": _SUBSCRIBE_MODULE})
    ).get("rc", -1)
    if rc != 0:
        logger.error("start_subscribe failed rc=%s", rc)
        return False
    return await subscribe_printers(host=host, dev_ids=dev_ids)


async def subscribe_printers(
    *, host: PluginHost, dev_ids: Sequence[str],
) -> bool:
    """Point the active subscription at ``dev_ids`` (printer serials).

    Also called on printer CRUD so newly added printers start reporting
    without a gateway restart.
    """
    if not dev_ids:
        return True
    rc = (
        await host.call("add_subscribe", {"dev_ids": list(dev_ids)})
    ).get("rc", -1)
    if rc != 0:
        logger.error("add_subscribe failed rc=%s", rc)
        return False
    logger.info("Subscribed to %d cloud printer(s)", len(dev_ids))
    # Selecting the machine + pushall opens the publish channel and pulls the
    # full snapshot; the channel readies asynchronously, so run it in the
    # background rather than blocking the connect/subscribe path.
    task = asyncio.create_task(
        request_full_status(host=host, dev_ids=list(dev_ids))
    )
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return True


async def request_full_status(
    *, host: PluginHost, dev_ids: Sequence[str],
    attempts: int = 12, delay: float = 0.5,
) -> None:
    """Open each printer's publish channel and pull a full status snapshot.

    Mirrors OrcaSlicer's post-connect sequence: set_user_selected_machine
    (the cloud equivalent of connect_printer) opens the send channel, then
    get_version + pushall request the full report. The channel readies
    asynchronously, so send_message returns -2 (CONNECT_FAILED) until it's up;
    retry until accepted. Best-effort — failures leave the printer sparse
    until the next (re)subscribe.
    """
    for dev_id in dev_ids:
        try:
            rc = (await host.call(
                "set_user_selected_machine", {"dev_id": dev_id}
            )).get("rc", -1)
            if rc != 0:
                logger.warning(
                    "set_user_selected_machine(%s) rc=%s", dev_id, rc
                )
        except PluginHostError as exc:
            logger.warning("set_user_selected_machine(%s) failed: %s", dev_id, exc)
            continue

        for attempt in range(attempts):
            try:
                ok = True
                for envelope in (_GET_VERSION, _PUSHALL):
                    rc = (await host.call("send_message", {
                        "dev_id": dev_id,
                        "payload": json.dumps(envelope),
                        "qos": 0,
                    })).get("rc", -1)
                    ok = ok and rc == 0
                if ok:
                    logger.info(
                        "Cloud printer %s full-status requested", dev_id
                    )
                    break
            except PluginHostError as exc:
                logger.warning("pushall(%s) failed: %s", dev_id, exc)
                break
            await asyncio.sleep(delay)
