"""MQTT client for communicating with a Bambu Lab printer over LAN."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import threading
import time
from collections.abc import Callable

import paho.mqtt.client as mqtt

from app.config import PrinterConfig
from app.hms_codes import current_error_description
from app.models import (
    AMSType,
    HMSCode,
    PrinterState,
    PrinterStatus,
    PrintJob,
    TemperatureInfo,
)
from app.preparation_stages import determine_state

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
        self._gcode_state: str = "IDLE"
        self._ams_trays: list[dict] = []
        self._ams_units: list[dict] = []
        self._vt_tray: dict | None = None
        self._ams_module_types: dict[int, AMSType] = {}  # ams_id -> AMSType from get_version
        # None until the printer reports its first `lights_report`.
        self._chamber_light_on: bool | None = None
        self._lock = threading.Lock()
        self._disconnect_timer: threading.Timer | None = None
        self._status_change_callback: Callable[[PrinterStatus, PrinterStatus], None] | None = None
        # Set by _update_status on the first MQTT report that parses. Lets
        # async consumers wait briefly on cold-start / lazy-connect for the
        # printer's first pushall to land in the cache before returning.
        self._data_ready_event = threading.Event()

    def set_status_change_callback(
        self, callback: Callable[[PrinterStatus, PrinterStatus], None] | None,
    ) -> None:
        """Register a callback invoked on every status update with (prev, new) snapshots."""
        self._status_change_callback = callback

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

    async def get_ams_info_async(
        self, wait_timeout: float = 2.5,
    ) -> tuple[list[dict], list[dict], dict | None]:
        """Async variant that waits briefly for the first MQTT report on
        cold-start / lazy-connect before reading the cache. Once the
        `_data_ready_event` is set (lifetime of the client instance),
        subsequent calls return immediately.
        """
        def _prime() -> None:
            self.ensure_connected()
            self.request_pushall()

        await asyncio.to_thread(_prime)
        if wait_timeout > 0 and not self._data_ready_event.is_set():
            await asyncio.to_thread(self._data_ready_event.wait, wait_timeout)

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

    def request_version(self) -> None:
        """Send a get_version command to discover module hardware types."""
        self.publish({
            "info": {
                "sequence_id": "0",
                "command": "get_version",
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

    def send_print_speed(self, level: int) -> None:
        """Send an MQTT command to change the print speed level."""
        self.publish({
            "print": {
                "sequence_id": "0",
                "command": "print_speed",
                "param": str(level),
            }
        })

    def send_chamber_light(self, on: bool, node: str = "chamber_light") -> None:
        """Toggle an LED node (chamber light by default) via `system.ledctrl`."""
        self.publish({
            "system": {
                "sequence_id": "0",
                "command": "ledctrl",
                "led_node": node,
                "led_mode": "on" if on else "off",
                "led_on_time": 500,
                "led_off_time": 500,
                "loop_times": 0,
                "interval_time": 0,
            }
        })

    @property
    def chamber_light_on(self) -> bool | None:
        """Last-reported chamber light state; None until the printer reports."""
        with self._lock:
            return self._chamber_light_on

    def send_start_drying(
        self,
        ams_id: int,
        temperature: int = 55,
        duration_minutes: int = 480,
    ) -> None:
        """Send an MQTT command to start AMS filament drying."""
        self.publish({
            "print": {
                "sequence_id": "0",
                "command": "ams_filament_drying",
                "ams_id": ams_id,
                "temp": temperature,
                "cooling_temp": 45,
                "duration": duration_minutes // 60,
                "humidity": 0,
                "mode": 1,
                "rotate_tray": False,
            }
        })

    def send_stop_drying(self, ams_id: int) -> None:
        """Send an MQTT command to stop AMS filament drying."""
        self.publish({
            "print": {
                "sequence_id": "0",
                "command": "ams_filament_drying",
                "ams_id": ams_id,
                "temp": 0,
                "cooling_temp": 45,
                "duration": 0,
                "humidity": 0,
                "mode": 0,
                "rotate_tray": False,
            }
        })

    def send_ams_filament_setting(
        self,
        ams_id: int,
        tray_id: int,
        tray_info_idx: str,
        tray_color: str,
        tray_type: str,
        nozzle_temp_min: int,
        nozzle_temp_max: int,
        setting_id: str,
        *,
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

        Mirrors what the printer's own RFID-scan flow does when a Bambu spool
        is loaded: writes `tray_info_idx` (the BBL filament_id, e.g. "GFA00"),
        the slicer `setting_id`, and the temp/color/type defaults so the
        printer can drive matching pre-print conditioning. The printer echoes
        the new state back over MQTT, which `_apply_ams_status` picks up so
        the gateway's AMS view reflects the change without further work.

        `ams_id` is the AMS unit index; `tray_id` is the per-AMS slot 0..3
        (NOT the global slot index). For the external spool, callers pass
        ams_id=255 / tray_id=254 — Bambu uses those reserved values.
        `tray_color` is the 8-char hex "RRGGBBAA" (no leading "#").

        The keyword-only fields are spool-tracking extras forwarded verbatim
        when not None: `tag_uid` and `tray_uuid` identify the physical spool
        for the printer's RFID/AMS ledger; `k`, `n`, and `cali_idx` carry
        flow-calibration state; `bed_temp`, `tray_weight`, and `remain`
        propagate per-spool defaults that would otherwise reset.
        """
        payload = {
            "sequence_id": "0",
            "command": "ams_filament_setting",
            "ams_id": ams_id,
            "tray_id": tray_id,
            "tray_info_idx": tray_info_idx,
            "tray_color": tray_color,
            "tray_type": tray_type,
            "nozzle_temp_min": nozzle_temp_min,
            "nozzle_temp_max": nozzle_temp_max,
            "setting_id": setting_id,
        }
        if tag_uid is not None:
            payload["tag_uid"] = tag_uid
        if bed_temp is not None:
            payload["bed_temp"] = bed_temp
        if tray_weight is not None:
            payload["tray_weight"] = tray_weight
        if remain is not None:
            payload["remain"] = remain
        if k is not None:
            payload["k"] = k
        if n is not None:
            payload["n"] = n
        if tray_uuid is not None:
            payload["tray_uuid"] = tray_uuid
        if cali_idx is not None:
            payload["cali_idx"] = cali_idx
        self.publish({"print": payload})

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
                # Leave `state` alone — the upcoming pushall reply will derive
                # it from `gcode_state`. Flipping to `idle` here would mask the
                # `offline → real` transition that NotificationHub relies on to
                # suppress phantom alerts on reconnect (e.g. a stale "Print
                # complete" each time a browser tab wakes up the gateway).
                self._schedule_disconnect_locked()

            self.request_version()
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

        # Handle get_version response — detect AMS module types
        info = payload.get("info", {})
        if info and info.get("command") == "get_version":
            self._parse_version_modules(info)

        print_info = payload.get("print", {})
        if not print_info:
            return

        self._update_status(print_info)

    def _parse_version_modules(self, info: dict) -> None:
        """Extract AMS module types from a get_version response.

        Module names like ``ams/0``, ``ams_f1/0``, ``n3f/0``, ``n3s/0``
        identify the AMS hardware type for each unit index.
        """
        modules = info.get("module", [])
        if not modules:
            return

        # Map module name prefixes to AMSType
        prefix_map = {
            "ams/": AMSType.standard,
            "ams_f1/": AMSType.lite,
            "n3f/": AMSType.pro,
            "n3s/": AMSType.ht,
        }

        with self._lock:
            for mod in modules:
                name = mod.get("name", "")
                for prefix, ams_type in prefix_map.items():
                    if name.startswith(prefix):
                        try:
                            ams_id = int(name[len(prefix):])
                        except (ValueError, TypeError):
                            continue
                        self._ams_module_types[ams_id] = ams_type
                        logger.debug("AMS %d detected as %s (module=%s, hw_ver=%s)",
                                     ams_id, ams_type.value, name, mod.get("hw_ver", ""))

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
            prev_snapshot = self._status.model_copy(deep=True)
            # Track raw fields for state derivation
            gcode_state = print_info.get("gcode_state")
            if gcode_state is not None:
                self._gcode_state = gcode_state

            if "stg_cur" in print_info:
                try:
                    self._status.stg_cur = int(print_info["stg_cur"])
                except (ValueError, TypeError):
                    pass

            # Speed level
            if "spd_lvl" in print_info:
                try:
                    self._status.speed_level = int(print_info["spd_lvl"])
                except (ValueError, TypeError):
                    pass

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

            # Lights report: [{"node": "chamber_light", "mode": "on"|"off"|"flashing"}, ...]
            if "lights_report" in print_info:
                raw = print_info["lights_report"]
                if isinstance(raw, list):
                    for entry in raw:
                        if not isinstance(entry, dict):
                            continue
                        if entry.get("node") == "chamber_light":
                            mode = entry.get("mode")
                            if isinstance(mode, str):
                                # Treat anything other than "off" as on — "flashing"
                                # still emits light, and the UI only distinguishes on/off.
                                self._chamber_light_on = mode.lower() != "off"

            # HMS codes
            if "hms" in print_info:
                raw = print_info["hms"]
                parsed: list[HMSCode] = []
                if isinstance(raw, list):
                    for entry in raw:
                        if not isinstance(entry, dict):
                            continue
                        attr = entry.get("attr")
                        code = entry.get("code")
                        if isinstance(attr, str) and isinstance(code, str):
                            parsed.append(HMSCode(attr=attr, code=code))
                prev_attrs = {c.attr for c in self._status.hms_codes}
                new_attrs = {c.attr for c in parsed}
                if prev_attrs != new_attrs:
                    logger.info(
                        "Printer %s HMS codes changed: %s -> %s",
                        self._config.serial,
                        sorted(prev_attrs) or "none",
                        sorted(new_attrs) or "none",
                    )
                self._status.hms_codes = parsed

            # print_error: non-zero means the printer auto-paused/stopped on an
            # error. User-initiated pauses keep this at 0.
            err_raw = print_info.get("print_error")
            if err_raw is None:
                err_raw = print_info.get("mc_print_error_code")
            if err_raw is not None:
                try:
                    err_val = int(err_raw)
                except (ValueError, TypeError):
                    err_val = 0
                if err_val != self._status.print_error:
                    logger.info(
                        "Printer %s print_error changed: %d -> %d",
                        self._config.serial,
                        self._status.print_error,
                        err_val,
                    )
                    self._status.print_error = err_val

            # Derive state using gcode_state + stg_cur + layer_num
            if gcode_state is not None or "stg_cur" in print_info:
                layer_num = self._status.job.current_layer if self._status.job else 0
                state, category, s_name = determine_state(
                    self._gcode_state,
                    self._status.stg_cur,
                    layer_num,
                )
                self._status.state = state
                self._status.stage_category = category
                self._status.stage_name = s_name

            # Populate error_message while paused/stopped at an error. Cleared
            # automatically on resume (state leaves paused/error).
            if self._status.state in (PrinterState.paused, PrinterState.error):
                self._status.error_message = current_error_description(
                    self._status.hms_codes, self._status.print_error,
                )
            else:
                self._status.error_message = None

            # Clear job when idle/finished/cancelled with 0 progress
            if self._status.state in (
                PrinterState.idle, PrinterState.finished, PrinterState.cancelled,
            ):
                if self._status.job and self._status.job.progress == 0:
                    self._status.job = None

            # AMS tray data
            ams_data = print_info.get("ams")
            if ams_data is not None:
                trays = []
                units = []

                # Active tray
                tray_now = ams_data.get("tray_now")
                if tray_now is not None:
                    try:
                        tray_now_int = int(tray_now)
                        # 255 = none, 254 = external spool
                        if tray_now_int == 255:
                            self._status.active_tray = None
                        else:
                            self._status.active_tray = tray_now_int
                    except (ValueError, TypeError):
                        pass

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
                    # Hardware version and AMS type
                    hw_version = str(unit.get("hw_ver", ""))
                    # Prefer module type from get_version, fall back to hw_ver
                    ams_type = self._ams_module_types.get(ams_id)
                    if ams_type is None and hw_version:
                        ams_type = AMSType.from_hw_version(hw_version)
                    # AMS Lite has no humidity sensor; firmware still emits a
                    # placeholder value in the field. Scrub it so the API and
                    # UI can detect "no reading available".
                    if ams_type is not None and not ams_type.has_humidity_sensor:
                        humidity = -1
                    # Drying state
                    dry_time = 0
                    try:
                        dry_time = int(unit.get("dry_time", 0))
                    except (ValueError, TypeError):
                        pass
                    units.append({
                        "id": ams_id,
                        "humidity": humidity,
                        "temperature": temperature,
                        "tray_count": len(unit_trays),
                        "hw_version": hw_version,
                        "ams_type": ams_type.value if ams_type else None,
                        "supports_drying": ams_type.supports_drying if ams_type else False,
                        "max_drying_temp": ams_type.max_drying_temp if ams_type else 55,
                        "dry_time_remaining": dry_time,
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

            new_snapshot = self._status.model_copy(deep=True)

        # Outside the lock: release anyone awaiting the first parsed report.
        self._data_ready_event.set()

        callback = self._status_change_callback
        if callback is not None:
            try:
                callback(prev_snapshot, new_snapshot)
            except Exception:
                logger.exception("Status change callback raised")
