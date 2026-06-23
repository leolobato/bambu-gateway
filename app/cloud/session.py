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
    # connect_server only *initiates* the cloud MQTT handshake. Subscribing or
    # selecting before it completes leaves the subscription dead (no reports,
    # no OnPrinterConnected, send_message → -2). Wait for the broker to report
    # connected, mirroring OrcaSlicer's is_server_connected() gate.
    if not await _await_server_connected(host=host):
        logger.error("cloud server did not connect; aborting subscribe")
        return False
    rc = (
        await host.call("start_subscribe", {"module": _SUBSCRIBE_MODULE})
    ).get("rc", -1)
    if rc != 0:
        logger.error("start_subscribe failed rc=%s", rc)
        return False
    return await subscribe_printers(host=host, dev_ids=dev_ids)


async def _await_server_connected(
    *, host: PluginHost, timeout: float = 20.0, interval: float = 0.5,
) -> bool:
    """Poll ``is_server_connected`` until the cloud MQTT broker is up.

    Returns True as soon as it reports connected. If the plugin lacks the
    symbol (older build), ``connected`` stays False forever — fall back to a
    short fixed delay so we don't block startup, then proceed optimistically.
    """
    waited = 0.0
    while waited < timeout:
        try:
            resp = await host.call("is_server_connected", {})
        except PluginHostError as exc:
            logger.warning("is_server_connected failed: %s", exc)
            return False
        if not resp.get("available", False):
            # Older plugin without the probe — proceed optimistically.
            logger.warning("is_server_connected unavailable; proceeding")
            return True
        if resp.get("connected"):
            logger.info("Cloud server connected after %.1fs", waited)
            return True
        await asyncio.sleep(interval)
        waited += interval
    logger.error("Cloud server not connected after %.0fs", timeout)
    return False


async def subscribe_printers(
    *, host: PluginHost, dev_ids: Sequence[str],
) -> bool:
    """Point the active subscription at ``dev_ids`` (printer serials).

    Also called on printer CRUD so newly added printers start reporting
    without a gateway restart.
    """
    if not dev_ids:
        return True
    # Select the machine(s) BEFORE subscribing — OrcaSlicer's order is
    # set_selected_machine → add_subscribe (DevManager.cpp), and its subscribe
    # list carries the selected device first. The relay only opens a device's
    # publish channel (and routes its reports) once it's the selected machine,
    # so subscribing first leaves the channel closed (send_message → -2, no
    # OnPrinterConnected, no reports).
    await select_machines(host=host, dev_ids=dev_ids)
    rc = (
        await host.call("add_subscribe", {"dev_ids": list(dev_ids)})
    ).get("rc", -1)
    if rc != 0:
        logger.error("add_subscribe failed rc=%s", rc)
        return False
    logger.info("Subscribed to %d cloud printer(s)", len(dev_ids))
    return True


async def select_machines(
    *, host: PluginHost, dev_ids: Sequence[str],
) -> None:
    """Open each printer's cloud publish channel (the cloud equivalent of
    connect_printer). Call this exactly ONCE per (re)subscribe: the channel
    readies asynchronously and the plugin fires its printer-connected callback
    when it's live — that callback is where pushall is sent. Re-selecting in a
    loop churns the connection (repeated connect/disconnect), so don't.
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


async def request_pushall(*, host: PluginHost, dev_id: str) -> bool:
    """Request a full status snapshot (get_version + pushall) for one printer.

    Sent from the printer-connected callback, when the publish channel is live.
    Returns True when both sends were accepted (rc == 0).
    """
    ok = True
    for envelope in (_GET_VERSION, _PUSHALL):
        try:
            rc = (await host.call("send_message", {
                "dev_id": dev_id,
                "payload": json.dumps(envelope),
                "qos": 0,
            })).get("rc", -1)
            ok = ok and rc == 0
            if rc != 0:
                logger.warning("pushall(%s) rc=%s", dev_id, rc)
        except PluginHostError as exc:
            logger.warning("pushall(%s) failed: %s", dev_id, exc)
            return False
    return ok
