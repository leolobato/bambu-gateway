"""Printer service — manages MQTT clients and exposes printer status."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

from app.camera_proxy import CameraProxy
from app.config import PrinterConfig
from app.models import CameraInfo, ChamberLightInfo, PrinterStatus
from app.mqtt_client import BambuMQTTClient, build_ams_filament_setting
from app import ftp_client

# Avoid a circular import at module level — imported locally when needed.
# from app.cloud.cloud_printer import CloudPrinterClient  (do not import here)


# Bambu's internal machine codes (as used by the slicer's `machine` query param)
# mapped to their camera transport. RTSPS models use port 322 with H.264 over
# RTSP; TCP-JPEG models use port 6000 with a binary JPEG frame stream.
_MACHINE_CODE_TRANSPORT: dict[str, str] = {
    "GM001": "rtsps",     # X1 Carbon
    "GM002": "rtsps",     # X1
    "GM003": "rtsps",     # X1E
    "GM017": "tcp_jpeg",  # P1P
    "GM018": "tcp_jpeg",  # P1S
    "GM020": "tcp_jpeg",  # A1 Mini
    "GM021": "tcp_jpeg",  # A1
}


def _classify_camera_transport(machine_model: str) -> str | None:
    """Map a Bambu machine_model string to the camera transport iOS needs.

    Returns ``"rtsps"`` for X1/X1C/P2S family (RTSPS on port 322),
    ``"tcp_jpeg"`` for A1/P1 family (TCP JPEG on port 6000), or ``None``
    when the model is unknown — callers should omit the ``camera`` field
    in that case.

    Accepts both Bambu's internal machine codes (``GM020`` for A1 Mini,
    ``GM001`` for X1 Carbon, …) and human-readable names (``A1``, ``X1C``,
    ``P1S``, ``P2S``). New codes should be added to
    ``_MACHINE_CODE_TRANSPORT`` above.
    """
    model = (machine_model or "").strip().upper()
    if not model:
        return None
    if model in _MACHINE_CODE_TRANSPORT:
        return _MACHINE_CODE_TRANSPORT[model]
    if model.startswith("X1") or model.startswith("P2"):
        return "rtsps"
    if model.startswith("A1") or model.startswith("P1"):
        return "tcp_jpeg"
    return None

logger = logging.getLogger(__name__)


class PrinterService:
    """Manages MQTT connections for all configured printers."""

    def __init__(
        self,
        printer_configs: list[PrinterConfig],
        status_change_callback=None,
        cloud_mode: bool = False,
    ) -> None:
        self._configs: dict[str, PrinterConfig] = {}
        self._clients: dict[str, BambuMQTTClient] = {}
        self._proxies: dict[str, CameraProxy] = {}
        self._status_change_callback = status_change_callback
        self._cloud_mode = cloud_mode
        self._cloud_host = None
        # Serials promoted to a read-write LAN client (SSDP found the ip and the
        # cloud bind list gave the access code). Empty until SSDP promotes one,
        # so cloud printers default to the relay transport.
        self._lan_promoted: set[str] = set()
        # Serials with a live plugin LAN connection (connect_printer succeeded
        # and OnLocalConnected reported ready). Writes to these go through the
        # plugin's authenticated local publish (send_message_to_printer).
        self._local_ready: set[str] = set()
        self._local_waiters: dict[str, asyncio.Future] = {}
        # Per-device event set when the plugin fires OnPrinterConnected (the
        # cloud publish channel just opened). The publish window is brief, so a
        # write waits on this and sends the instant it fires.
        self._cloud_ready_events: dict[str, asyncio.Event] = {}
        # Per-device event set once the plugin confirms the device certificate
        # is installed (a "device_cert_installed" OnMessage string). The cloud
        # relay rejects every "print"-namespace publish with -2 until then, so a
        # cloud write waits on this before send_message. Mirrors OrcaSlicer's
        # install_device_cert → is_security_control_ready() handshake.
        self._security_ready: dict[str, asyncio.Event] = {}
        # Delay between cloud-relay publish retries (overridable so tests don't
        # actually sleep through the retry loop).
        self._cloud_retry_delay: float = 0.4
        # Cloud printer clients keyed by serial. Set via set_cloud_printers()
        # after the EventPump is wired up; None until then.
        self._cloud_clients: dict[str, "CloudPrinterClient"] | None = None  # type: ignore[name-defined]
        for cfg in printer_configs:
            self._configs[cfg.serial] = cfg
            if cloud_mode and not self._wants_lan(cfg):
                # Cloud mode: printers are reached via CloudPrinterClient
                # (the relay). A printer SSDP has promoted to LAN gets a
                # read-write LAN client below instead.
                continue
            client = BambuMQTTClient(cfg)
            if status_change_callback is not None:
                client.set_status_change_callback(status_change_callback)
            self._clients[cfg.serial] = client

    def set_cloud_printers(self, cloud_clients: dict, host=None) -> None:
        """Register cloud printer clients so they appear in list/status results.

        Called from the lifespan after CloudPrinterClient instances are created.
        ``cloud_clients`` is a ``dict[serial, CloudPrinterClient]``. The dict
        object is shared with ``app.state.cloud_printers`` and the EventPump
        handler closures, so :meth:`sync_printers` mutates it in place.

        ``host`` is the active PluginHost, attached to clients created later
        by :meth:`_sync_cloud_printers`.
        """
        self._cloud_clients = cloud_clients
        self._cloud_host = host
        if self._status_change_callback is not None:
            for client in cloud_clients.values():
                client.set_status_change_callback(self._status_change_callback)

    def get_cloud_client(self, printer_id: str):
        """Return the CloudPrinterClient for a printer, or None."""
        return (self._cloud_clients or {}).get(printer_id)

    def _wants_lan(self, cfg: PrinterConfig) -> bool:
        """Whether this printer should use a read-write LAN client.

        Outside cloud mode every printer is LAN. In cloud mode a printer is LAN
        only once SSDP has promoted it AND we have its ip + access code.
        """
        if not self._cloud_mode:
            return True
        # A read-only paho LAN connection competes with the plugin's cloud
        # connection at the printer, destabilising the cloud publish channel.
        # BAMBU_NO_PAHO keeps cloud printers purely on the relay (reads via
        # cloud OnMessage, writes via send_message) — what OrcaSlicer does.
        import os
        if os.environ.get("BAMBU_NO_PAHO") == "1":
            return False
        return (
            cfg.serial in self._lan_promoted
            and bool(cfg.ip and cfg.access_code)
        )

    def promote_lan(self, serial: str) -> None:
        """Mark a serial as LAN-reachable (called when SSDP finds its ip).

        Caller should follow with :meth:`sync_printers` so the transport
        actually switches from the relay to a LAN client.
        """
        self._lan_promoted.add(serial)

    def start(self) -> None:
        """Initialize printer service without opening MQTT connections."""
        if not self._clients:
            logger.warning("No printers configured")
            return

        logger.info("Initialized %d printer client(s) in lazy-connect mode", len(self._clients))

    def stop(self) -> None:
        """Disconnect MQTT clients. Does not stop camera proxies — async callers
        should use :meth:`stop_async` instead. Kept for backward compatibility with
        any synchronous teardown paths.
        """
        logger.info("Stopping all printer connections")
        for client in self._clients.values():
            client.stop()

    def get_configs(self) -> list[PrinterConfig]:
        """Return all current printer configs."""
        return list(self._configs.values())

    def sync_printers(self, new_configs: list[PrinterConfig]) -> None:
        """Hot-reload printers by diffing against running clients.

        - New serials get added and started.
        - Removed serials get stopped and deleted.
        - Changed configs (ip or access_code) get stopped and restarted.

        In cloud mode the same diff is applied to the cloud-client registry
        instead (in place, so app.state and the EventPump handlers see it).
        """
        if self._cloud_mode:
            self._sync_cloud_printers(new_configs)
            return

        new_by_serial = {c.serial: c for c in new_configs}
        old_serials = set(self._configs.keys())
        new_serials = set(new_by_serial.keys())

        to_remove = old_serials - new_serials
        to_add = new_serials - old_serials
        to_check = old_serials & new_serials

        for serial in to_remove:
            logger.info("Removing printer %s", serial)
            self._clients[serial].stop()
            del self._clients[serial]
            del self._configs[serial]
            proxy = self._proxies.pop(serial, None)
            if proxy is not None:
                asyncio.create_task(proxy.stop())

        for serial in to_check:
            old = self._configs[serial]
            new = new_by_serial[serial]
            if old.ip != new.ip or old.access_code != new.access_code:
                logger.info("Resetting printer %s client (config changed)", serial)
                self._clients[serial].stop()
                self._configs[serial] = new
                new_client = BambuMQTTClient(new)
                if self._status_change_callback is not None:
                    new_client.set_status_change_callback(self._status_change_callback)
                self._clients[serial] = new_client
                proxy = self._proxies.pop(serial, None)
                if proxy is not None:
                    asyncio.create_task(proxy.stop())
            else:
                # Non-connection fields changed (name, machine_model, etc.)
                self._configs[serial] = new
                if old.name != new.name:
                    client = self._clients[serial]
                    display = new.name or f"Printer {serial[-4:]}"
                    with client._lock:
                        client._status.name = display

        for serial in to_add:
            cfg = new_by_serial[serial]
            logger.info("Adding printer %s", serial)
            self._configs[serial] = cfg
            client = BambuMQTTClient(cfg)
            if self._status_change_callback is not None:
                client.set_status_change_callback(self._status_change_callback)
            self._clients[serial] = client

    def _sync_cloud_printers(self, new_configs: list[PrinterConfig]) -> None:
        """Cloud-mode hot-reload that routes each printer per-transport.

        A printer with a LAN ip + access_code (SSDP-found ip + cloud-fetched
        access code) gets a read-write LAN client; one without is reached via
        the cloud relay. A serial lives in exactly one of the two registries.
        """
        from app.cloud.cloud_printer import CloudPrinterClient

        new_by_serial = {c.serial: c for c in new_configs}
        old_configs = self._configs
        self._configs = dict(new_by_serial)

        gone = (
            set(old_configs) | set(self._clients) | set(self._cloud_clients or {})
        ) - set(new_by_serial)
        for serial in gone:
            self._drop_lan_client(serial)
            if self._cloud_clients is not None and serial in self._cloud_clients:
                logger.info("Removing cloud printer %s", serial)
                del self._cloud_clients[serial]

        for serial, cfg in new_by_serial.items():
            if self._wants_lan(cfg):
                if self._cloud_clients is not None and serial in self._cloud_clients:
                    del self._cloud_clients[serial]
                self._ensure_lan_client(serial, cfg, old_configs.get(serial))
                continue

            self._drop_lan_client(serial)
            if self._cloud_clients is None:
                continue
            display = cfg.name or f"Printer {serial[-4:]}"
            existing = self._cloud_clients.get(serial)
            if existing is None:
                logger.info("Adding cloud printer %s", serial)
                client = CloudPrinterClient(
                    dev_id=serial, name=display,
                    machine_model=cfg.machine_model, host=self._cloud_host,
                )
                if self._status_change_callback is not None:
                    client.set_status_change_callback(
                        self._status_change_callback
                    )
                self._cloud_clients[serial] = client
            else:
                existing.set_name(display)
                existing.set_machine_model(cfg.machine_model)

    def _drop_lan_client(self, serial: str) -> None:
        client = self._clients.pop(serial, None)
        if client is not None:
            logger.info("Removing LAN printer %s", serial)
            client.stop()
            proxy = self._proxies.pop(serial, None)
            if proxy is not None:
                asyncio.create_task(proxy.stop())

    def _ensure_lan_client(
        self, serial: str, cfg: PrinterConfig, old_cfg: PrinterConfig | None,
    ) -> None:
        existing = self._clients.get(serial)
        if existing is None:
            logger.info("Adding LAN printer %s (%s)", serial, cfg.ip)
            client = BambuMQTTClient(cfg)
            if self._status_change_callback is not None:
                client.set_status_change_callback(self._status_change_callback)
            self._clients[serial] = client
            return
        if (old_cfg is None or old_cfg.ip != cfg.ip
                or old_cfg.access_code != cfg.access_code):
            logger.info("Resetting LAN printer %s client (config changed)", serial)
            existing.stop()
            new_client = BambuMQTTClient(cfg)
            if self._status_change_callback is not None:
                new_client.set_status_change_callback(self._status_change_callback)
            self._clients[serial] = new_client
            proxy = self._proxies.pop(serial, None)
            if proxy is not None:
                asyncio.create_task(proxy.stop())
        elif old_cfg.name != cfg.name:
            display = cfg.name or f"Printer {serial[-4:]}"
            with existing._lock:
                existing._status.name = display

    def get_all_statuses(self) -> list[PrinterStatus]:
        """Return status for every configured printer (LAN + cloud)."""
        statuses = [
            self._attach_camera(client.get_status(), client)
            for client in self._clients.values()
        ]
        if self._cloud_clients:
            for cloud_client in self._cloud_clients.values():
                statuses.append(self._attach_camera(cloud_client.get_status()))
        return statuses

    def get_status(self, printer_id: str) -> PrinterStatus | None:
        """Return status for a single printer, or None if not found."""
        client = self._clients.get(printer_id)
        if client is not None:
            return self._attach_camera(client.get_status(), client)
        if self._cloud_clients:
            cloud_client = self._cloud_clients.get(printer_id)
            if cloud_client is not None:
                return self._attach_camera(cloud_client.get_status())
        return None

    def _attach_camera(
        self, status: PrinterStatus, client: BambuMQTTClient | None = None,
    ) -> PrinterStatus:
        """Populate ``status.camera`` from the printer's config + last-known state.

        Camera access is a direct connection to the printer's IP with the access
        code — independent of whether control/status flow over LAN MQTT or the
        cloud relay. So as long as the config has an IP + access code and a
        classifiable model, it works for cloud printers too (``client`` is the
        LAN client when present, used only to read the live chamber-light state).
        Omits the camera when the model isn't classifiable or the config lacks
        IP/access code — iOS falls back to "not available".
        """
        config = self._configs.get(status.id)
        if config is None or not config.ip or not config.access_code:
            return status
        transport = _classify_camera_transport(config.machine_model)
        if transport is None:
            return status
        # Chamber-light control works on both transports; the live on/off state
        # comes from the LAN client's lights_report. The cloud relay doesn't
        # deliver that report, so the state defaults to False there (the toggle
        # still works — only the readback is approximate).
        light_on = client.chamber_light_on if client is not None else False
        status.camera = CameraInfo(
            ip=config.ip,
            access_code=config.access_code,
            transport=transport,
            chamber_light=ChamberLightInfo(
                supported=True,
                on=light_on,
            ),
        )
        return status

    def get_client(self, printer_id: str) -> BambuMQTTClient | None:
        """Return the MQTT client for a printer, or None if not found."""
        return self._clients.get(printer_id)

    def get_camera_proxy(self, printer_id: str) -> CameraProxy | None:
        """Return (and lazily create) the camera proxy for a printer.

        Returns None when the printer is unknown, has no IP/access code, or
        its transport isn't `tcp_jpeg`. RTSPS-family printers always return
        None — those are handled by a separate transcode pipeline (future).
        """
        if printer_id in self._proxies:
            return self._proxies[printer_id]
        config = self._configs.get(printer_id)
        if config is None or not config.ip or not config.access_code:
            return None
        transport = _classify_camera_transport(config.machine_model)
        if transport != "tcp_jpeg":
            return None
        proxy = CameraProxy(ip=config.ip, access_code=config.access_code)
        self._proxies[printer_id] = proxy
        return proxy

    async def stop_async(self) -> None:
        """Stop all MQTT clients and camera proxies. Safe to call from async code."""
        self.stop()
        proxies = list(self._proxies.values())
        self._proxies.clear()
        for proxy in proxies:
            await proxy.stop()

    def default_printer_id(self) -> str | None:
        """Return the serial of the first configured printer."""
        if self._clients:
            return next(iter(self._clients))
        if self._cloud_clients:
            return next(iter(self._cloud_clients))
        return None

    def get_config(self, printer_id: str) -> PrinterConfig | None:
        """Return the config for a single printer, or None if not found."""
        return self._configs.get(printer_id)

    def _any_client(self, printer_id: str):
        """LAN client for this serial, falling back to the cloud client."""
        client = self._clients.get(printer_id)
        if client is not None:
            return client
        if self._cloud_clients:
            return self._cloud_clients.get(printer_id)
        return None

    def get_ams_trays(self, printer_id: str) -> list[dict] | None:
        """Return AMS tray data for a printer, or None if not found."""
        client = self._any_client(printer_id)
        if client is None:
            return None
        return client.get_ams_trays()

    def get_ams_info(self, printer_id: str) -> tuple[list[dict], list[dict], dict | None] | None:
        """Return (trays, units, vt_tray) for a printer, or None if not found."""
        client = self._any_client(printer_id)
        if client is None:
            return None
        return client.get_ams_info()

    async def get_ams_info_async(
        self, printer_id: str, wait_timeout: float = 2.5,
    ) -> tuple[list[dict], list[dict], dict | None] | None:
        """Async AMS fetch that waits up to `wait_timeout` for the first
        MQTT report on cold-start, avoiding the empty-cache race without
        needing client-side retries. Cloud clients return immediately.
        """
        client = self._any_client(printer_id)
        if client is None:
            return None
        return await client.get_ams_info_async(wait_timeout=wait_timeout)

    def pause_print(self, printer_id: str) -> None:
        """Pause the current print on the given printer."""
        client = self._clients.get(printer_id)
        if client is None:
            raise ValueError(f"Printer {printer_id} not found")
        client.ensure_connected()
        status = client.get_status()
        if not status.online:
            raise ConnectionError(f"Printer {printer_id} is offline")
        client.send_pause()
        logger.info("Pause sent to printer %s", printer_id)

    def resume_print(self, printer_id: str) -> None:
        """Resume a paused print on the given printer."""
        client = self._clients.get(printer_id)
        if client is None:
            raise ValueError(f"Printer {printer_id} not found")
        client.ensure_connected()
        status = client.get_status()
        if not status.online:
            raise ConnectionError(f"Printer {printer_id} is offline")
        client.send_resume()
        logger.info("Resume sent to printer %s", printer_id)

    def cancel_print(self, printer_id: str) -> None:
        """Cancel the current print on the given printer."""
        client = self._clients.get(printer_id)
        if client is None:
            raise ValueError(f"Printer {printer_id} not found")
        client.ensure_connected()
        status = client.get_status()
        if not status.online:
            raise ConnectionError(f"Printer {printer_id} is offline")
        client.send_stop()
        logger.info("Cancel sent to printer %s", printer_id)

    def set_print_speed(self, printer_id: str, level: int) -> None:
        """Set print speed on the given printer."""
        client = self._clients.get(printer_id)
        if client is None:
            raise ValueError(f"Printer {printer_id} not found")
        client.ensure_connected()
        status = client.get_status()
        if not status.online:
            raise ConnectionError(f"Printer {printer_id} is offline")
        client.send_print_speed(level)
        logger.info("Print speed set to %d on printer %s", level, printer_id)

    def set_chamber_light(
        self, printer_id: str, on: bool, node: str = "chamber_light",
    ) -> None:
        """Toggle the printer's chamber light (or another LED node)."""
        client = self._clients.get(printer_id)
        if client is None:
            raise ValueError(f"Printer {printer_id} not found")
        client.ensure_connected()
        status = client.get_status()
        if not status.online:
            raise ConnectionError(f"Printer {printer_id} is offline")
        client.send_chamber_light(on, node=node)
        logger.info(
            "Light %s set to %s on printer %s", node, "on" if on else "off", printer_id,
        )

    def start_drying(
        self,
        printer_id: str,
        ams_id: int,
        temperature: int = 55,
        duration_minutes: int = 480,
    ) -> None:
        """Start filament drying on an AMS unit."""
        client = self._clients.get(printer_id)
        if client is None:
            raise ValueError(f"Printer {printer_id} not found")
        client.ensure_connected()
        status = client.get_status()
        if not status.online:
            raise ConnectionError(f"Printer {printer_id} is offline")
        client.send_start_drying(ams_id, temperature, duration_minutes)
        logger.info(
            "Drying started on printer %s AMS %d: %d°C for %d min",
            printer_id, ams_id, temperature, duration_minutes,
        )

    def stop_drying(self, printer_id: str, ams_id: int) -> None:
        """Stop filament drying on an AMS unit."""
        client = self._clients.get(printer_id)
        if client is None:
            raise ValueError(f"Printer {printer_id} not found")
        client.ensure_connected()
        status = client.get_status()
        if not status.online:
            raise ConnectionError(f"Printer {printer_id} is offline")
        client.send_stop_drying(ams_id)
        logger.info("Drying stopped on printer %s AMS %d", printer_id, ams_id)

    def set_ams_auto_refill(self, printer_id: str, enabled: bool) -> None:
        """Toggle printer-level AMS auto-refill."""
        client = self._clients.get(printer_id)
        if client is None:
            raise ValueError(f"Printer {printer_id} not found")
        client.ensure_connected()
        status = client.get_status()
        if not status.online:
            raise ConnectionError(f"Printer {printer_id} is offline")
        client.send_ams_auto_refill(enabled)
        logger.info(
            "AMS auto-refill set to %s on printer %s",
            "enabled" if enabled else "disabled",
            printer_id,
        )

    def mark_local_connected(self, dev_id: str, status: int) -> None:
        """Record an ``OnLocalConnected`` event from the plugin.

        ``status`` follows Bambu's ConnectStatus enum: 0 == ready, 1 == failed,
        2 == lost. Readiness flips ``_local_ready`` and wakes any pending
        :meth:`_ensure_local_link` waiter.
        """
        if status == 0:
            self._local_ready.add(dev_id)
        else:
            self._local_ready.discard(dev_id)
        waiter = self._local_waiters.get(dev_id)
        if waiter is not None and not waiter.done():
            waiter.set_result(status)

    def mark_cloud_connected(self, dev_id: str) -> None:
        """Record an ``OnPrinterConnected`` event — the device's cloud publish
        channel just opened. Wakes any write waiting to publish."""
        ev = self._cloud_ready_events.get(dev_id)
        if ev is None:
            ev = asyncio.Event()
            self._cloud_ready_events[dev_id] = ev
        ev.set()

    def _security_event(self, dev_id: str) -> asyncio.Event:
        ev = self._security_ready.get(dev_id)
        if ev is None:
            ev = asyncio.Event()
            self._security_ready[dev_id] = ev
        return ev

    def mark_security_control_ready(self, dev_id: str, ready: bool) -> None:
        """Record the device-certificate state from a plugin control message.

        ``"device_cert_installed"`` → ready; ``"device_cert_uninstalled"`` /
        ``"cert_expired"`` / ``"cert_revoked"`` → not ready. Gates cloud
        ``print``-namespace writes (see :meth:`_await_security_control_ready`).
        """
        ev = self._security_event(dev_id)
        was_ready = ev.is_set()
        if ready:
            ev.set()
            if not was_ready:
                logger.info("Secure control channel ready for %s", dev_id)
        else:
            ev.clear()
            if was_ready:
                logger.info("Secure control channel cleared for %s", dev_id)

    async def request_device_cert(self, printer_id: str) -> None:
        """Ask the plugin to install this device's certificate.

        Mirrors OrcaSlicer issuing ``install_device_cert`` on
        ``OnPrinterConnected``. Cloud printers pass ``lan_only=False``. The
        plugin confirms asynchronously via a ``device_cert_installed`` OnMessage
        string, handled by :meth:`mark_security_control_ready`.
        """
        host = self._cloud_host
        if host is None:
            return
        try:
            await host.call(
                "install_device_cert",
                {"dev_id": printer_id, "lan_only": False},
            )
        except Exception:
            logger.exception("install_device_cert(%s) failed", printer_id)

    async def _cloud_relay_publish(
        self, printer_id: str, envelope: dict, qos: int,
        *, attempts: int = 4,
    ) -> int:
        """Publish a command over the cloud relay, retrying transient -2s.

        A branded session accepts the write on the first try (rc 0). The bounded
        retry only covers the brief window where the device's publish channel is
        still settling right after connect. Returns the last rc.
        """
        host = self._cloud_host
        if host is None:
            raise ConnectionError(
                f"Printer {printer_id}: cloud network plugin unavailable"
            )
        payload = json.dumps(envelope)
        rc = -1
        for attempt in range(attempts):
            rc = (await host.call("send_message", {
                "dev_id": printer_id,
                "payload": payload,
                "qos": qos,
            })).get("rc", -1)
            if rc == 0:
                return 0
            logger.warning(
                "cloud publish to %s rc=%s (attempt %d/%d)",
                printer_id, rc, attempt + 1, attempts,
            )
            if attempt + 1 < attempts:
                await asyncio.sleep(self._cloud_retry_delay)
        return rc

    async def send_command_envelope(
        self, printer_id: str, envelope: dict, *, qos: int = 0,
    ) -> None:
        """Route a write-command envelope to the printer's command transport.

        In cloud mode writes go through the authenticated network plugin's
        cloud relay (``send_message``) — exactly as OrcaSlicer publishes a
        cloud-bound printer (``dev_connection_type`` empty → ``cloud_publish_json``).
        The relay refuses every ``print``-namespace command with -2 until the
        device certificate is installed, so we wait for that secure-control
        handshake first (:meth:`_await_security_control_ready`). In pure LAN
        mode (dev-mode printers, no plugin) the local MQTT publish is used.
        """
        if self._cloud_mode:
            cfg = self._configs.get(printer_id)
            if cfg is None:
                raise ValueError(f"Printer {printer_id} not found")
            host = self._cloud_host
            if host is None:
                raise ConnectionError(
                    f"Printer {printer_id}: cloud network plugin unavailable"
                )
            # The relay accepts print writes once the agent is branded (done at
            # bring-up via set_extra_http_header) AND the per-device secure
            # channel is warm — kept so by the ~1Hz install_device_cert
            # heartbeat (see _cloud_keepalive). We deliberately do NOT re-issue
            # the cert here: a fresh install briefly drops the channel (-2), so
            # touching it per-write would fight the heartbeat. The bounded retry
            # below covers the rare cold-channel race right after connect.
            rc = await self._cloud_relay_publish(printer_id, envelope, qos)
            if rc != 0:
                raise ConnectionError(
                    f"Printer {printer_id}: cloud relay rejected command "
                    f"(rc={rc})"
                )
            return

        client = self._clients.get(printer_id)
        if client is None:
            raise ValueError(f"Printer {printer_id} not found")
        client.ensure_connected()
        if not client.get_status().online:
            raise ConnectionError(f"Printer {printer_id} is offline")
        client.publish(envelope)

    async def _ensure_local_link(
        self, printer_id: str, cfg: PrinterConfig, *, timeout: float = 12.0,
    ) -> None:
        """Open (once) the plugin's authenticated LAN connection to a printer.

        Mirrors OrcaSlicer's ``MachineObject::connect()``: ``connect_printer``
        with ``bblp`` + the access code, then wait for the async
        ``OnLocalConnected`` callback to report ready. Requires the plugin's
        ``start_discovery`` to have populated its local-device registry first
        (otherwise connect_printer fails before opening a socket).
        """
        if printer_id in self._local_ready:
            return
        host = self._cloud_host
        if host is None:
            raise ConnectionError(
                f"Printer {printer_id}: network plugin unavailable"
            )
        if not (cfg.ip and cfg.access_code):
            raise ConnectionError(
                f"Printer {printer_id}: no LAN ip/access code for local connect"
            )
        import os
        use_ssl = os.environ.get("BAMBU_LAN_USE_SSL", "1") == "1"
        waiter: asyncio.Future = asyncio.get_running_loop().create_future()
        self._local_waiters[printer_id] = waiter
        try:
            rc = (await host.call("connect_printer", {
                "dev_id": printer_id, "dev_ip": cfg.ip,
                "username": "bblp", "password": cfg.access_code,
                "use_ssl": use_ssl,
            })).get("rc", -1)
            if rc != 0:
                raise ConnectionError(
                    f"Printer {printer_id}: connect_printer rejected (rc={rc})"
                )
            if printer_id in self._local_ready:
                return
            try:
                status = await asyncio.wait_for(
                    asyncio.shield(waiter), timeout=timeout,
                )
            except asyncio.TimeoutError:
                raise ConnectionError(
                    f"Printer {printer_id}: timed out establishing the LAN "
                    "connection through the network plugin"
                )
            if status != 0:
                raise ConnectionError(
                    f"Printer {printer_id}: LAN connection failed "
                    f"(status={status})"
                )
            self._local_ready.add(printer_id)
        finally:
            self._local_waiters.pop(printer_id, None)

    async def set_ams_filament(
        self,
        printer_id: str,
        ams_id: int,
        tray_id: int,
        *,
        tray_info_idx: str,
        tray_color: str,
        tray_type: str,
        nozzle_temp_min: int,
        nozzle_temp_max: int,
        setting_id: str,
        tag_uid: str | None = None,
        bed_temp: int | None = None,
        tray_weight: int | None = None,
        remain: int | None = None,
        k: float | None = None,
        n: float | None = None,
        tray_uuid: str | None = None,
        cali_idx: int | None = None,
    ) -> None:
        """Assign a filament profile to one AMS tray.

        Publishes ``ams_filament_setting`` through the printer's command
        transport (the network plugin in cloud mode — see
        :meth:`send_command_envelope`). The printer echoes the new tray state
        back over its status report and `_apply_ams_status` propagates it into
        the cached PrinterStatus, so the dashboard reflects the change on its
        next poll.
        """
        if printer_id not in self._configs:
            raise ValueError(f"Printer {printer_id} not found")
        envelope = build_ams_filament_setting(
            ams_id, tray_id, tray_info_idx, tray_color, tray_type,
            nozzle_temp_min, nozzle_temp_max, setting_id,
            tag_uid=tag_uid, bed_temp=bed_temp, tray_weight=tray_weight,
            remain=remain, k=k, n=n, tray_uuid=tray_uuid, cali_idx=cali_idx,
        )
        await self.send_command_envelope(printer_id, envelope)
        logger.info(
            "AMS filament set on printer %s AMS %d tray %d: %s (%s)",
            printer_id, ams_id, tray_id, tray_info_idx, setting_id,
        )

    def submit_print(
        self,
        printer_id: str,
        file_data: bytes,
        filename: str,
        *,
        plate_id: int = 1,
        ams_mapping: list[int] | None = None,
        use_ams: bool = False,
        progress_callback: Callable[[int], None] | None = None,
    ) -> None:
        """Upload a 3MF file via FTPS and start printing via MQTT."""
        client = self._clients.get(printer_id)
        if client is None:
            raise ValueError(f"Printer {printer_id} not found")

        client.ensure_connected()
        status = client.get_status()
        if not status.online:
            raise ConnectionError(f"Printer {printer_id} is offline")

        cfg = client._config
        ftp_client.upload_file(
            cfg.ip, cfg.access_code, file_data, filename,
            progress_callback=progress_callback,
        )
        client.send_print_command(
            filename,
            plate_id=plate_id,
            ams_mapping=ams_mapping,
            use_ams=use_ams,
        )
        logger.info("Print job submitted: %s on printer %s (plate %d)", filename, printer_id, plate_id)
