"""MQTT client for communicating with a Bambu Lab printer over LAN."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

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


# Sequence-id source for control commands. OrcaSlicer cycles
# ``MachineObject::m_sequence_id`` strictly within [START_SEQ_ID, END_SEQ_ID) =
# [20000, 30000) and treats only ids in that window as its own ("studio")
# commands (``is_studio_cmd``, DeviceManager.cpp). The firmware shares the
# convention, so a control command must carry a 5-digit id in that range — a
# value outside it (e.g. a wall-clock seed) is not recognised as a studio
# command. Each command takes a fresh id, wrapping back to 20000.
_SEQ_START = 20000
_SEQ_END = 30000
_sequence_value = _SEQ_START
_sequence_lock = threading.Lock()


def next_sequence_id() -> str:
    """Return the next ``sequence_id`` string in OrcaSlicer's [20000, 30000)."""
    global _sequence_value
    with _sequence_lock:
        seq = _sequence_value
        _sequence_value += 1
        if _sequence_value >= _SEQ_END:
            _sequence_value = _SEQ_START
        return str(seq)


# ---------------------------------------------------------------------------
# Transport-neutral command-JSON builders
#
# These functions return the same JSON dict that the BambuMQTTClient publish_*
# methods construct, but without touching MQTT.  Both the LAN path
# (BambuMQTTClient) and the cloud path (CloudPrinterClient) call these builders
# so the envelope shape is always identical regardless of transport.
# ---------------------------------------------------------------------------


def build_pause_command() -> dict:
    """Return the JSON envelope for a pause command."""
    return {
        "print": {
            "sequence_id": "0",
            "command": "pause",
        }
    }


def build_resume_command() -> dict:
    """Return the JSON envelope for a resume command."""
    return {
        "print": {
            "sequence_id": "0",
            "command": "resume",
        }
    }


def build_cancel_command() -> dict:
    """Return the JSON envelope for a cancel/stop command."""
    return {
        "print": {
            "sequence_id": "0",
            "command": "stop",
        }
    }


def build_speed_command(level: int) -> dict:
    """Return the JSON envelope for a print-speed command.

    :param level: Speed level (1 = silent, 2 = standard, 3 = sport, 4 = ludicrous).
    """
    return {
        "print": {
            "sequence_id": "0",
            "command": "print_speed",
            "param": str(level),
        }
    }


def build_ams_start_drying_command(
    ams_id: int,
    temperature: int = 55,
    duration_minutes: int = 480,
) -> dict:
    """Return the JSON envelope for starting AMS filament drying."""
    return {
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
    }


def build_light_command(on: bool, node: str = "chamber_light") -> dict:
    """Return the JSON envelope for toggling an LED node via `system.ledctrl`."""
    return {
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
    }


def build_ams_stop_drying_command(ams_id: int) -> dict:
    """Return the JSON envelope for stopping AMS filament drying."""
    return {
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
    }


# Bambu's reserved ids for the external/virtual spool (no physical AMS bay).
VIRTUAL_TRAY_MAIN_ID = 255
VIRTUAL_TRAY_DEPUTY_ID = 254


def build_ams_filament_setting(
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
) -> dict:
    """Return the ``ams_filament_setting`` envelope.

    Mirrors OrcaSlicer's ``MachineObject::command_ams_filament_settings``
    (DeviceManager.cpp): the per-AMS slot is sent as BOTH ``slot_id`` and
    ``tray_id`` for a real AMS bay — newer firmware ignores the command when
    ``slot_id`` is missing. The external/virtual spool (``ams_id`` 255/254)
    pins ``tray_id`` to ``VIRTUAL_TRAY_DEPUTY_ID``.

    ``tray_id`` here is the per-AMS slot index (0..3), matching the public API.
    The keyword-only fields are spool-tracking extras forwarded verbatim when
    not None.
    """
    slot_id = tray_id
    if ams_id in (VIRTUAL_TRAY_MAIN_ID, VIRTUAL_TRAY_DEPUTY_ID):
        wire_tray_id = VIRTUAL_TRAY_DEPUTY_ID
    else:
        wire_tray_id = slot_id
    payload: dict = {
        "sequence_id": next_sequence_id(),
        "command": "ams_filament_setting",
        "ams_id": ams_id,
        "slot_id": slot_id,
        "tray_id": wire_tray_id,
        "tray_info_idx": tray_info_idx,
        "setting_id": setting_id,
        "tray_color": tray_color,
        "nozzle_temp_min": nozzle_temp_min,
        "nozzle_temp_max": nozzle_temp_max,
        "tray_type": tray_type,
    }
    for key, value in (
        ("tag_uid", tag_uid),
        ("bed_temp", bed_temp),
        ("tray_weight", tray_weight),
        ("remain", remain),
        ("k", k),
        ("n", n),
        ("tray_uuid", tray_uuid),
        ("cali_idx", cali_idx),
    ):
        if value is not None:
            payload[key] = value
    return {"print": payload}


def apply_print_payload(
    status: "PrinterStatus",
    print_info: dict,
    *,
    gcode_state: str = "IDLE",
    ams_auto_refill_hold_until: float = 0.0,
) -> str:
    """Apply fields from a Bambu ``print`` MQTT payload to ``status`` in-place.

    Shared by the LAN path (``BambuMQTTClient._update_status``) and the cloud
    path (``CloudPrinterClient.handle_event``) so both produce identical state
    updates from the same payload format.

    :param status: Mutable ``PrinterStatus`` to update.
    :param print_info: Parsed ``print`` sub-dict from the MQTT report.
    :param gcode_state: Current accumulated ``gcode_state`` string for this
        printer.  Will be updated if the payload contains a new value.
    :param ams_auto_refill_hold_until: ``time.monotonic()`` deadline below
        which AMS auto-refill fields are ignored (optimistic-update hold-off).
    :return: The (possibly updated) ``gcode_state`` string.

    .. note:: Caller is responsible for holding any necessary lock while
        calling this function.  The function does NOT acquire any locks.
    """
    new_gcode_state = print_info.get("gcode_state")
    if new_gcode_state is not None:
        gcode_state = new_gcode_state

    if "stg_cur" in print_info:
        try:
            status.stg_cur = int(print_info["stg_cur"])
        except (ValueError, TypeError):
            pass

    if "spd_lvl" in print_info:
        try:
            status.speed_level = int(print_info["spd_lvl"])
        except (ValueError, TypeError):
            pass

    if "support_filament_backup" in print_info:
        raw_supported = print_info.get("support_filament_backup")
        if isinstance(raw_supported, bool):
            status.ams_auto_refill_supported = raw_supported

    if time.monotonic() >= ams_auto_refill_hold_until:
        parsed_auto_refill = None
        if "cfg" in print_info:
            parsed_auto_refill = _bit_enabled(print_info.get("cfg"), 18)
        if parsed_auto_refill is None and "home_flag" in print_info:
            parsed_auto_refill = _bit_enabled(print_info.get("home_flag"), 10)
        if parsed_auto_refill is not None:
            status.ams_auto_refill_enabled = parsed_auto_refill

    temps = status.temperatures
    if "nozzle_temper" in print_info:
        temps.nozzle_temp = float(print_info["nozzle_temper"])
    if "nozzle_target_temper" in print_info:
        temps.nozzle_target = float(print_info["nozzle_target_temper"])
    if "bed_temper" in print_info:
        temps.bed_temp = float(print_info["bed_temper"])
    if "bed_target_temper" in print_info:
        temps.bed_target = float(print_info["bed_target_temper"])

    has_job_info = any(
        k in print_info
        for k in ("subtask_name", "mc_percent", "mc_remaining_time",
                  "layer_num", "total_layer_num", "gcode_start_time")
    )
    if has_job_info:
        if status.job is None:
            status.job = PrintJob()
        job = status.job
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
        if "gcode_start_time" in print_info:
            job.gcode_start_time = str(print_info["gcode_start_time"])

    if "hms" in print_info:
        raw = print_info["hms"]
        parsed_hms: list[HMSCode] = []
        if isinstance(raw, list):
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                attr = entry.get("attr")
                code = entry.get("code")
                if isinstance(attr, str) and isinstance(code, str):
                    parsed_hms.append(HMSCode(attr=attr, code=code))
        status.hms_codes = parsed_hms

    err_raw = print_info.get("print_error")
    if err_raw is None:
        err_raw = print_info.get("mc_print_error_code")
    if err_raw is not None:
        try:
            status.print_error = int(err_raw)
        except (ValueError, TypeError):
            pass

    if new_gcode_state is not None or "stg_cur" in print_info:
        layer_num = status.job.current_layer if status.job else 0
        state, category, s_name = determine_state(
            gcode_state,
            status.stg_cur,
            layer_num,
        )
        status.state = state
        status.stage_category = category
        status.stage_name = s_name

    if status.state in (PrinterState.paused, PrinterState.error):
        status.error_message = current_error_description(
            status.hms_codes, status.print_error,
        )
    else:
        status.error_message = None

    if status.state in (
        PrinterState.idle, PrinterState.finished, PrinterState.cancelled,
    ):
        if status.job and status.job.progress == 0:
            status.job = None

    return gcode_state


def _bit_enabled(value: object, bit: int) -> bool | None:
    """Return a bit from Bambu integer-like report fields.

    OrcaSlicer reads AMS auto-refill from `home_flag` bit 10 and `cfg` bit 18
    in `DeviceManager.cpp:999` and `DeviceManager.cpp:4951`.
    """
    if value is None:
        return None
    try:
        if isinstance(value, int):
            raw = value
        else:
            text = str(value).strip()
            if not text:
                return None
            base = 16 if text.lower().startswith("0x") else 10
            raw = int(text, base)
    except (TypeError, ValueError):
        return None
    return ((raw >> bit) & 0x1) != 0


def _parse_vt_tray_entry(data: dict) -> tuple[dict | None, bool]:
    """Parse the external spool (``vt_tray``) from a payload dict.

    Returns ``(entry, present)``. ``present`` is False when the key is absent
    (caller keeps prior state); True with a dict entry when set, or True with
    ``None`` when explicitly cleared (sent as null/empty).
    """
    _missing = object()
    vt_tray_raw = data.get("vt_tray", _missing)
    if isinstance(vt_tray_raw, dict) and vt_tray_raw:
        entry = {"slot": 254, "ams_id": -1, "tray_id": -1}
        for k, v in vt_tray_raw.items():
            if k != "id":
                entry[k] = v
        try:
            entry["remain"] = int(entry.get("remain", -1))
        except (ValueError, TypeError):
            entry["remain"] = -1
        return entry, True
    if vt_tray_raw is not _missing:
        return None, True
    return None, False


@dataclass
class AmsReport:
    """Parsed AMS state from a single print payload. ``*_present`` flags let
    callers preserve prior state for fields the payload didn't carry."""
    ams_present: bool = False
    trays: list[dict] = field(default_factory=list)
    units: list[dict] = field(default_factory=list)
    active_tray_present: bool = False
    active_tray: int | None = None
    vt_tray_present: bool = False
    vt_tray: dict | None = None


# Maps a get_version `module` name prefix to the AMS hardware type. Shared by
# the LAN and cloud paths via parse_ams_module_types().
_AMS_MODULE_PREFIX = {
    "ams/": AMSType.standard,
    "ams_f1/": AMSType.lite,
    "n3f/": AMSType.pro,
    "n3s/": AMSType.ht,
}


def parse_ams_module_types(info: dict) -> dict[int, AMSType]:
    """Map a get_version response's ``module`` list to AMSType per ams_id.

    Module names look like ``ams/0``, ``ams_f1/0`` (AMS Lite), ``n3f/0``
    (AMS 2 Pro), ``n3s/0`` (AMS HT). Returns ``{ams_id: AMSType}``.
    """
    out: dict[int, AMSType] = {}
    for mod in info.get("module", []) or []:
        name = mod.get("name", "") if isinstance(mod, dict) else ""
        for prefix, ams_type in _AMS_MODULE_PREFIX.items():
            if name.startswith(prefix):
                try:
                    out[int(name[len(prefix):])] = ams_type
                except (ValueError, TypeError):
                    pass
    return out


def parse_ams_report(
    print_info: dict,
    *,
    module_types: dict[int, AMSType] | None = None,
) -> AmsReport:
    """Parse AMS trays/units/active-tray/vt_tray from a print payload.

    Shared by the LAN and cloud paths so both produce identical AMS state.
    ``module_types`` maps ams_id -> AMSType (from the get_version module list —
    the authoritative source, since the per-unit ``hw_ver`` is often empty,
    especially over the cloud relay). The AMS type is hardware, NOT model:
    e.g. an A1 mini can run an AMS Lite OR an AMS 2 Pro, so it must be read
    from the report, never inferred from the printer.
    """
    module_types = module_types or {}
    report = AmsReport()
    ams_data = print_info.get("ams")

    if isinstance(ams_data, dict):
        tray_now = ams_data.get("tray_now")
        if tray_now is not None:
            try:
                tray_now_int = int(tray_now)
                report.active_tray_present = True
                # 255 = none, 254 = external spool
                report.active_tray = None if tray_now_int == 255 else tray_now_int
            except (ValueError, TypeError):
                pass

        # Bambu sends partial `ams` blocks during operations (e.g. just
        # `tray_now`, version subfields, or status bits) with NO unit array.
        # Only treat trays/units as "present" when the report actually carries
        # the unit list — otherwise a partial report parses to empty trays and
        # callers would wipe the cached AMS to nothing (the dashboard's "AMS
        # disappeared" after a filament change).
        unit_array = ams_data.get("ams")
        if not isinstance(unit_array, list) or not unit_array:
            unit_array = []
        else:
            report.ams_present = True
        for unit in unit_array:
            ams_id = int(unit.get("id", 0))
            unit_trays = unit.get("tray", [])
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
            hw_version = str(unit.get("hw_ver", ""))
            ams_type = module_types.get(ams_id)
            if ams_type is None and hw_version:
                ams_type = AMSType.from_hw_version(hw_version)
            # AMS Lite has no humidity sensor; firmware emits a placeholder.
            if ams_type is not None and not ams_type.has_humidity_sensor:
                humidity = -1
            dry_time = 0
            try:
                dry_time = int(unit.get("dry_time", 0))
            except (ValueError, TypeError):
                pass
            report.units.append({
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
                entry = {
                    "slot": ams_id * 4 + tray_id,
                    "ams_id": ams_id,
                    "tray_id": tray_id,
                }
                for k, v in tray.items():
                    if k != "id":
                        entry[k] = v
                try:
                    entry["remain"] = int(entry.get("remain", -1))
                except (ValueError, TypeError):
                    entry["remain"] = -1
                report.trays.append(entry)

        vt_entry, vt_present = _parse_vt_tray_entry(ams_data)
        if vt_present:
            report.vt_tray_present = True
            report.vt_tray = vt_entry

    # Some printers (e.g. A1 Mini) report vt_tray at the top level of the
    # print payload, not inside the ams block — it wins if present.
    vt_entry, vt_present = _parse_vt_tray_entry(print_info)
    if vt_present:
        report.vt_tray_present = True
        report.vt_tray = vt_entry

    return report


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
        self._ams_auto_refill_hold_until = 0.0

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
        self.publish(build_pause_command())

    def send_resume(self) -> None:
        """Send an MQTT command to resume a paused print."""
        self.publish(build_resume_command())

    def send_stop(self) -> None:
        """Send an MQTT command to cancel/stop the current print."""
        self.publish(build_cancel_command())

    def send_print_speed(self, level: int) -> None:
        """Send an MQTT command to change the print speed level."""
        self.publish(build_speed_command(level))

    def send_chamber_light(self, on: bool, node: str = "chamber_light") -> None:
        """Toggle an LED node (chamber light by default) via `system.ledctrl`."""
        self.publish(build_light_command(on, node=node))

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
        self.publish(build_ams_start_drying_command(ams_id, temperature, duration_minutes))

    def send_stop_drying(self, ams_id: int) -> None:
        """Send an MQTT command to stop AMS filament drying."""
        self.publish(build_ams_stop_drying_command(ams_id))

    def send_ams_auto_refill(self, enabled: bool) -> None:
        """Toggle AMS auto-refill.

        Mirrors OrcaSlicer's `MachineObject::command_ams_switch_filament`,
        which publishes `print_option.auto_switch_filament` instead of adding
        this setting to a `project_file` print payload.
        """
        self.publish({
            "print": {
                "sequence_id": "0",
                "command": "print_option",
                "auto_switch_filament": enabled,
            }
        })
        with self._lock:
            self._status.ams_auto_refill_enabled = enabled
            self._ams_auto_refill_hold_until = time.monotonic() + 3.0

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
        self.publish(build_ams_filament_setting(
            ams_id, tray_id, tray_info_idx, tray_color, tray_type,
            nozzle_temp_min, nozzle_temp_max, setting_id,
            tag_uid=tag_uid, bed_temp=bed_temp, tray_weight=tray_weight,
            remain=remain, k=k, n=n, tray_uuid=tray_uuid, cali_idx=cali_idx,
        ))

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
        detected = parse_ams_module_types(info)
        if not detected:
            return
        with self._lock:
            self._ams_module_types.update(detected)
        logger.debug("AMS module types detected: %s",
                     {k: v.value for k, v in detected.items()})

    def _update_status(self, print_info: dict) -> None:
        """Apply fields from an MQTT print report to the in-memory status."""
        with self._lock:
            prev_snapshot = self._status.model_copy(deep=True)

            # Log HMS and print_error changes before delegating to the shared
            # parser (the module-level function doesn't have access to the
            # serial for log context).
            if "hms" in print_info:
                raw = print_info["hms"]
                if isinstance(raw, list):
                    prev_attrs = {c.attr for c in self._status.hms_codes}
                    new_attrs = {
                        entry.get("attr")
                        for entry in raw
                        if isinstance(entry, dict) and isinstance(entry.get("attr"), str)
                    }
                    if prev_attrs != new_attrs:
                        logger.info(
                            "Printer %s HMS codes changed: %s -> %s",
                            self._config.serial,
                            sorted(prev_attrs) or "none",
                            sorted(new_attrs) or "none",
                        )

            # Same fallback rule as apply_print_payload (`is None`, not
            # truthiness) so the log never disagrees with the stored value
            # when print_error is 0 alongside a non-zero mc code.
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

            # Shared parser: updates status fields in-place and returns the
            # (possibly updated) gcode_state string.
            self._gcode_state = apply_print_payload(
                self._status,
                print_info,
                gcode_state=self._gcode_state,
                ams_auto_refill_hold_until=self._ams_auto_refill_hold_until,
            )

            # Lights report: [{"node": "chamber_light", "mode": "on"|"off"|"flashing"}, ...]
            # Handled here (not in apply_print_payload) because chamber light
            # state lives on a separate instance attribute, not PrinterStatus.
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

            # AMS state — shared parser, identical to the cloud path.
            ams_report = parse_ams_report(
                print_info, module_types=self._ams_module_types
            )
            if ams_report.active_tray_present:
                self._status.active_tray = ams_report.active_tray
            if ams_report.ams_present:
                self._ams_trays = ams_report.trays
                self._ams_units = ams_report.units
            if ams_report.vt_tray_present:
                self._vt_tray = ams_report.vt_tray
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
