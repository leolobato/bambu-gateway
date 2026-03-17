"""MQTT client for communicating with a Bambu Lab printer over LAN."""

from __future__ import annotations

import json
import logging
import ssl
import threading
import time

import paho.mqtt.client as mqtt

from app.config import PrinterConfig
from app.models import (
    GCODE_STATE_MAP,
    PrinterState,
    PrinterStatus,
    PrintJob,
    TemperatureInfo,
)

logger = logging.getLogger(__name__)

MQTT_PORT = 8883
MQTT_USERNAME = "bblp"
MQTT_IDLE_TIMEOUT_SECONDS = 20


class BambuMQTTClient:
    """Manages an MQTT connection to a single Bambu Lab printer."""

    def __init__(self, config: PrinterConfig) -> None:
        self._config = config
        self._client: mqtt.Client | None = None
        self._status = PrinterStatus(
            id=config.serial,
            name=config.name or f"Printer {config.serial[-4:]}",
            machine_model=config.machine_model,
        )
        self._ams_trays: list[dict] = []
        self._ams_units: list[dict] = []
        self._vt_tray: dict | None = None
        self._lock = threading.Lock()
        self._disconnect_timer: threading.Timer | None = None

    @property
    def serial(self) -> str:
        return self._config.serial

    def get_status(self) -> PrinterStatus:
        self.ensure_connected(timeout=0.5)
        with self._lock:
            return self._status.model_copy()

    def get_ams_trays(self) -> list[dict]:
        self.ensure_connected()
        self.request_pushall()
        with self._lock:
            return list(self._ams_trays)

    def get_ams_info(self) -> tuple[list[dict], list[dict], dict | None]:
        """Return (trays, units, vt_tray) for this printer."""
        self.ensure_connected()
        self.request_pushall()
        with self._lock:
            return list(self._ams_trays), list(self._ams_units), (dict(self._vt_tray) if self._vt_tray else None)

    def start(self) -> None:
        """Connect to the printer MQTT broker and start the network loop."""
        with self._lock:
            if self._client is not None:
                self._schedule_disconnect_locked()
                return

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(MQTT_USERNAME, self._config.access_code)

        tls_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        tls_ctx.check_hostname = False
        tls_ctx.verify_mode = ssl.CERT_NONE
        client.tls_set_context(tls_ctx)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        self._client = client

        logger.info("Connecting to printer %s at %s:%d",
                     self._config.serial, self._config.ip, MQTT_PORT)
        try:
            client.connect(self._config.ip, MQTT_PORT, keepalive=60)
        except Exception:
            logger.exception("Failed to connect to printer %s",
                             self._config.serial)
            with self._lock:
                self._status.online = False
                self._status.state = PrinterState.offline
            return

        client.loop_start()
        with self._lock:
            self._schedule_disconnect_locked()

    def stop(self) -> None:
        """Disconnect and stop the network loop."""
        client: mqtt.Client | None
        with self._lock:
            self._cancel_disconnect_timer_locked()
            client = self._client
            self._client = None
        if client is not None:
            client.loop_stop()
            client.disconnect()

        with self._lock:
            self._status.online = False
            self._status.state = PrinterState.offline

        logger.info("Disconnected from printer %s", self._config.serial)

    def ensure_connected(self, timeout: float = 3.0) -> bool:
        self.start()
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            with self._lock:
                if self._status.online:
                    self._schedule_disconnect_locked()
                    return True
                has_client = self._client is not None
            if not has_client:
                return False
            time.sleep(0.05)

        with self._lock:
            self._schedule_disconnect_locked()
        return False

    def publish(self, payload: dict) -> None:
        """Publish a JSON command to the printer's request topic."""
        if not self.ensure_connected():
            logger.warning("Cannot publish — printer %s is not connected",
                           self._config.serial)
            return

        client = self._client
        if client is None:
            logger.warning("Cannot publish — printer %s has no MQTT client", self._config.serial)
            return

        topic = f"device/{self._config.serial}/request"
        message = json.dumps(payload)
        client.publish(topic, message)
        logger.debug("Published to %s: %s", topic, message)

    def request_pushall(self) -> None:
        """Send a pushall command to request a full status report."""
        self.publish({
            "pushing": {
                "sequence_id": "0",
                "command": "pushall",
            }
        })

    def send_pause(self) -> None:
        """Send an MQTT command to pause the current print."""
        self.publish({
            "print": {
                "sequence_id": "0",
                "command": "pause",
            }
        })

    def send_resume(self) -> None:
        """Send an MQTT command to resume a paused print."""
        self.publish({
            "print": {
                "sequence_id": "0",
                "command": "resume",
            }
        })

    def send_stop(self) -> None:
        """Send an MQTT command to cancel/stop the current print."""
        self.publish({
            "print": {
                "sequence_id": "0",
                "command": "stop",
            }
        })

    def send_print_command(
        self,
        filename: str,
        plate_id: int = 1,
        ams_mapping: list[int] | None = None,
        use_ams: bool = False,
    ) -> None:
        """Send an MQTT command to start printing an uploaded file."""
        payload = {
            "print": {
                "sequence_id": "0",
                "command": "project_file",
                "param": f"Metadata/plate_{plate_id}.gcode",
                "url": f"file:///sdcard/cache/{filename}",
                "bed_type": "auto",
                "timelapse": False,
                "bed_leveling": True,
                "flow_cali": False,
                "vibration_cali": False,
                "layer_inspect": False,
                "use_ams": use_ams,
                "ams_mapping": ams_mapping or [0],
                "subtask_name": filename,
                "profile_id": "0",
                "project_id": "0",
                "subtask_id": "0",
                "task_id": "0",
            }
        }
        logger.info(
            "project_file MQTT payload: %s",
            json.dumps(payload["print"]),
        )
        self.publish(payload)

    def _cancel_disconnect_timer_locked(self) -> None:
        if self._disconnect_timer is not None:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None

    def _schedule_disconnect_locked(self) -> None:
        self._cancel_disconnect_timer_locked()
        if self._client is None:
            return
        timer = threading.Timer(MQTT_IDLE_TIMEOUT_SECONDS, self.stop)
        timer.daemon = True
        timer.start()
        self._disconnect_timer = timer

    # -- MQTT callbacks --

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            report_topic = f"device/{self._config.serial}/report"
            client.subscribe(report_topic)
            logger.info("Connected to printer %s, subscribed to %s",
                        self._config.serial, report_topic)

            with self._lock:
                self._status.online = True
                if self._status.state == PrinterState.offline:
                    self._status.state = PrinterState.idle
                self._schedule_disconnect_locked()

            self.request_pushall()
        else:
            logger.error("Connection refused by printer %s (rc=%d)",
                         self._config.serial, rc)
            with self._lock:
                self._status.online = False
                self._status.state = PrinterState.offline

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        logger.warning("Disconnected from printer %s (rc=%d)",
                       self._config.serial, rc)
        with self._lock:
            self._status.online = False
            self._status.state = PrinterState.offline

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.debug("Ignoring non-JSON message on %s", msg.topic)
            return

        print_info = payload.get("print", {})
        if not print_info:
            return

        self._update_status(print_info)

    def _parse_vt_tray(self, data: dict) -> None:
        """Parse the external spool holder (vt_tray) from an MQTT payload dict.

        Must be called while self._lock is held.
        """
        _missing = object()
        vt_tray_raw = data.get("vt_tray", _missing)
        if isinstance(vt_tray_raw, dict) and vt_tray_raw:
            entry = {
                "slot": 254,
                "ams_id": -1,
                "tray_id": -1,
            }
            for k, v in vt_tray_raw.items():
                if k != "id":
                    entry[k] = v
            try:
                entry["remain"] = int(entry.get("remain", -1))
            except (ValueError, TypeError):
                entry["remain"] = -1
            self._vt_tray = entry
        elif vt_tray_raw is not _missing:
            # Explicitly sent as null or empty — clear it
            self._vt_tray = None

    def _update_status(self, print_info: dict) -> None:
        """Apply fields from an MQTT print report to the in-memory status."""
        with self._lock:
            # Printer state
            gcode_state = print_info.get("gcode_state")
            if gcode_state is not None:
                self._status.state = GCODE_STATE_MAP.get(
                    gcode_state, PrinterState.error
                )

            # Temperatures
            temps = self._status.temperatures
            if "nozzle_temper" in print_info:
                temps.nozzle_temp = float(print_info["nozzle_temper"])
            if "nozzle_target_temper" in print_info:
                temps.nozzle_target = float(print_info["nozzle_target_temper"])
            if "bed_temper" in print_info:
                temps.bed_temp = float(print_info["bed_temper"])
            if "bed_target_temper" in print_info:
                temps.bed_target = float(print_info["bed_target_temper"])

            # Print job
            has_job_info = any(
                k in print_info
                for k in ("subtask_name", "mc_percent", "mc_remaining_time",
                          "layer_num", "total_layer_num")
            )
            if has_job_info:
                if self._status.job is None:
                    self._status.job = PrintJob()

                job = self._status.job
                if "subtask_name" in print_info:
                    job.file_name = print_info["subtask_name"]
                if "mc_percent" in print_info:
                    job.progress = int(print_info["mc_percent"])
                if "mc_remaining_time" in print_info:
                    job.remaining_minutes = int(print_info["mc_remaining_time"])
                if "layer_num" in print_info:
                    job.current_layer = int(print_info["layer_num"])
                if "total_layer_num" in print_info:
                    job.total_layers = int(print_info["total_layer_num"])

            # Clear job when idle/finished with 0 progress
            if self._status.state in (PrinterState.idle, PrinterState.finished):
                if self._status.job and self._status.job.progress == 0:
                    self._status.job = None

            # AMS tray data
            ams_data = print_info.get("ams")
            if ams_data is not None:
                trays = []
                units = []
                for unit in ams_data.get("ams", []):
                    ams_id = int(unit.get("id", 0))
                    unit_trays = unit.get("tray", [])
                    # Unit-level info
                    humidity = -1
                    try:
                        humidity = int(unit.get("humidity", -1))
                    except (ValueError, TypeError):
                        pass
                    temperature = 0.0
                    try:
                        temperature = float(unit.get("temp", 0.0))
                    except (ValueError, TypeError):
                        pass
                    units.append({
                        "id": ams_id,
                        "humidity": humidity,
                        "temperature": temperature,
                        "tray_count": len(unit_trays),
                    })
                    for tray in unit_trays:
                        tray_id = int(tray.get("id", 0))
                        slot = ams_id * 4 + tray_id
                        entry = {
                            "slot": slot,
                            "ams_id": ams_id,
                            "tray_id": tray_id,
                        }
                        # Forward all tray fields from the printer
                        for k, v in tray.items():
                            if k != "id":
                                entry[k] = v
                        # Normalize remain to int
                        try:
                            entry["remain"] = int(entry.get("remain", -1))
                        except (ValueError, TypeError):
                            entry["remain"] = -1
                        trays.append(entry)
                self._ams_trays = trays
                self._ams_units = units

                self._parse_vt_tray(ams_data)

            # Some printers (e.g. A1 Mini) report vt_tray at the top
            # level of the print payload, not inside the ams block.
            if "vt_tray" in print_info:
                self._parse_vt_tray(print_info)
            self._schedule_disconnect_locked()
