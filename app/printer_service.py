"""Printer service — manages MQTT clients and exposes printer status."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from app.camera_proxy import CameraProxy
from app.config import PrinterConfig
from app.models import CameraInfo, ChamberLightInfo, PrinterStatus
from app.mqtt_client import BambuMQTTClient
from app import ftp_client


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
    ) -> None:
        self._configs: dict[str, PrinterConfig] = {}
        self._clients: dict[str, BambuMQTTClient] = {}
        self._proxies: dict[str, CameraProxy] = {}
        self._status_change_callback = status_change_callback
        for cfg in printer_configs:
            self._configs[cfg.serial] = cfg
            client = BambuMQTTClient(cfg)
            if status_change_callback is not None:
                client.set_status_change_callback(status_change_callback)
            self._clients[cfg.serial] = client

    def start(self) -> None:
        """Initialize printer service without opening MQTT connections."""
        if not self._clients:
            logger.warning("No printers configured")
            return

        logger.info("Initialized %d printer client(s) in lazy-connect mode", len(self._clients))

    def stop(self) -> None:
        """Disconnect from all printers."""
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
        """
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

    def get_all_statuses(self) -> list[PrinterStatus]:
        """Return status for every configured printer."""
        return [
            self._attach_camera(client.get_status(), client)
            for client in self._clients.values()
        ]

    def get_status(self, printer_id: str) -> PrinterStatus | None:
        """Return status for a single printer, or None if not found."""
        client = self._clients.get(printer_id)
        if client is None:
            return None
        return self._attach_camera(client.get_status(), client)

    def _attach_camera(
        self, status: PrinterStatus, client: BambuMQTTClient,
    ) -> PrinterStatus:
        """Populate ``status.camera`` from the printer's config + last-known state.

        Omits the camera entirely when the model isn't classifiable or the
        config lacks IP/access code — iOS falls back to "not available".
        """
        config = self._configs.get(status.id)
        if config is None or not config.ip or not config.access_code:
            return status
        transport = _classify_camera_transport(config.machine_model)
        if transport is None:
            return status
        status.camera = CameraInfo(
            ip=config.ip,
            access_code=config.access_code,
            transport=transport,
            chamber_light=ChamberLightInfo(
                supported=True,
                on=client.chamber_light_on,
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
        return None

    def get_config(self, printer_id: str) -> PrinterConfig | None:
        """Return the config for a single printer, or None if not found."""
        return self._configs.get(printer_id)

    def get_ams_trays(self, printer_id: str) -> list[dict] | None:
        """Return AMS tray data for a printer, or None if not found."""
        client = self._clients.get(printer_id)
        if client is None:
            return None
        return client.get_ams_trays()

    def get_ams_info(self, printer_id: str) -> tuple[list[dict], list[dict], dict | None] | None:
        """Return (trays, units, vt_tray) for a printer, or None if not found."""
        client = self._clients.get(printer_id)
        if client is None:
            return None
        return client.get_ams_info()

    async def get_ams_info_async(
        self, printer_id: str, wait_timeout: float = 2.5,
    ) -> tuple[list[dict], list[dict], dict | None] | None:
        """Async AMS fetch that waits up to `wait_timeout` for the first
        MQTT report on cold-start, avoiding the empty-cache race without
        needing client-side retries.
        """
        client = self._clients.get(printer_id)
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

    def set_ams_filament(
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
    ) -> None:
        """Assign a filament profile to one AMS tray via MQTT.

        Pre-validates the printer is connected, then publishes
        `ams_filament_setting`. The printer echoes the new tray state back
        over MQTT and `_apply_ams_status` propagates it into the cached
        PrinterStatus, so the dashboard reflects the change on its next poll.
        """
        client = self._clients.get(printer_id)
        if client is None:
            raise ValueError(f"Printer {printer_id} not found")
        client.ensure_connected()
        status = client.get_status()
        if not status.online:
            raise ConnectionError(f"Printer {printer_id} is offline")
        client.send_ams_filament_setting(
            ams_id=ams_id,
            tray_id=tray_id,
            tray_info_idx=tray_info_idx,
            tray_color=tray_color,
            tray_type=tray_type,
            nozzle_temp_min=nozzle_temp_min,
            nozzle_temp_max=nozzle_temp_max,
            setting_id=setting_id,
        )
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
