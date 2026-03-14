"""Printer service — manages MQTT clients and exposes printer status."""

from __future__ import annotations

import logging

from app.config import PrinterConfig
from app.models import PrinterStatus
from app.mqtt_client import BambuMQTTClient
from app import ftp_client

logger = logging.getLogger(__name__)


class PrinterService:
    """Manages MQTT connections for all configured printers."""

    def __init__(self, printer_configs: list[PrinterConfig]) -> None:
        self._configs: dict[str, PrinterConfig] = {}
        self._clients: dict[str, BambuMQTTClient] = {}
        for cfg in printer_configs:
            self._configs[cfg.serial] = cfg
            self._clients[cfg.serial] = BambuMQTTClient(cfg)

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

        for serial in to_check:
            old = self._configs[serial]
            new = new_by_serial[serial]
            if old.ip != new.ip or old.access_code != new.access_code:
                logger.info("Resetting printer %s client (config changed)", serial)
                self._clients[serial].stop()
                self._configs[serial] = new
                self._clients[serial] = BambuMQTTClient(new)
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
            self._clients[serial] = BambuMQTTClient(cfg)

    def get_all_statuses(self) -> list[PrinterStatus]:
        """Return status for every configured printer."""
        return [client.get_status() for client in self._clients.values()]

    def get_status(self, printer_id: str) -> PrinterStatus | None:
        """Return status for a single printer, or None if not found."""
        client = self._clients.get(printer_id)
        if client is None:
            return None
        return client.get_status()

    def get_client(self, printer_id: str) -> BambuMQTTClient | None:
        """Return the MQTT client for a printer, or None if not found."""
        return self._clients.get(printer_id)

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

    def submit_print(
        self,
        printer_id: str,
        file_data: bytes,
        filename: str,
        *,
        plate_id: int = 1,
        ams_mapping: list[int] | None = None,
        use_ams: bool = False,
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
        ftp_client.upload_file(cfg.ip, cfg.access_code, file_data, filename)
        client.send_print_command(
            filename,
            plate_id=plate_id,
            ams_mapping=ams_mapping,
            use_ams=use_ams,
        )
        logger.info("Print job submitted: %s on printer %s (plate %d)", filename, printer_id, plate_id)
