"""Establish the plugin's cloud MQTT session: connect + subscribe.

The Bambu plugin does not connect to the cloud broker on its own — after a
logged-in session exists someone must call ``connect_server`` and subscribe
the printer serials, mirroring OrcaSlicer's post-login sequence
(``GUI_App::on_user_login_handle`` → ``connect_server`` +
``DeviceManager::add_user_subscribe``; ``start_subscribe("app")`` while the
app is active). Without these calls no OnMessage event ever arrives.
"""
from __future__ import annotations

import logging
from typing import Sequence

from app.cloud.plugin_host import PluginHost

logger = logging.getLogger("bambu.cloud.session")

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
    return True
