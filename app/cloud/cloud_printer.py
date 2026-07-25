"""Per-printer state holder updated from cloud MQTT events."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import TYPE_CHECKING, AsyncIterator

from app.models import AMSType, PrinterStatus
from app.mqtt_client import (
    SPEED_LEVEL_HOLD_SECONDS,
    apply_print_payload,
    extract_command_ack,
    parse_ams_module_types,
    parse_ams_report,
)

logger = logging.getLogger("bambu.cloud.printer")

# Gateway-internal sentinel (not a Bambu firmware code): a print submission was
# attempted while one is already in flight for this printer. The HTTP layer maps
# it to 409 Conflict so a double-click doesn't surface as a 500.
PRINT_IN_FLIGHT_CODE = -1009


class CloudPrinterClient:
    """Mirrors the read surface of ``BambuMQTTClient`` for cloud printers.

    Holds a ``PrinterStatus`` updated by the EventPump's OnMessage handler.
    Thread-safe via the same lock pattern as the LAN client.

    Unlike the LAN client, the cloud path has no MQTT connection or AMS tray
    tracking — it receives pre-parsed events from the C++ EventPump and applies
    them through the same ``apply_print_payload`` function the LAN client uses,
    so the ``PrinterStatus`` shape is identical between the two paths.
    """

    def __init__(
        self, *, dev_id: str, name: str = "", machine_model: str = "",
        default_plate_type: str = "", host=None
    ) -> None:
        self._dev_id = dev_id
        self._status = PrinterStatus(
            id=dev_id,
            name=name or f"Cloud Printer {dev_id[-4:]}",
            machine_model=machine_model,
            default_plate_type=default_plate_type,
        )
        self._gcode_state: str = "IDLE"
        # AMS state, parsed from the same `print.ams` block the LAN client
        # uses (via the shared parse_ams_report). The cloud relay has no
        # get_version stream, so AMS module type falls back to per-unit hw_ver.
        self._ams_trays: list[dict] = []
        self._ams_units: list[dict] = []
        self._vt_tray: dict | None = None
        # ams_id -> AMSType, parsed from the get_version module list (the
        # authoritative hardware source). Empty until the first get_version
        # report arrives; feeds parse_ams_report so AMS Lite is recognised as
        # sensor-less (otherwise it shows a phantom humidity reading).
        self._ams_module_types: dict[int, AMSType] = {}
        # Last command ack per command name (see extract_command_ack).
        self._command_acks: dict[str, dict] = {}
        # Deadline below which reported spd_lvl is ignored in favour of an
        # optimistically-applied level (see apply_optimistic_speed).
        self._speed_level_hold_until = 0.0
        self._lock = threading.Lock()
        # Single in-flight print job's progress channel. None = no active job.
        self._progress: asyncio.Queue | None = None
        # The PluginHost this printer talks through. Attached at construction
        # (lifespan) or via attach_host so call sites don't have to thread
        # (host, client) pairs around.
        self._host = host
        # Fires (prev, new) snapshots on every applied report — feeds the
        # NotificationHub exactly like BambuMQTTClient._update_status does.
        self._status_change_callback = None
        self._print_report_callback = None
        self._print_source_registrar = None

    @property
    def serial(self) -> str:
        return self._dev_id

    def attach_host(self, host) -> None:
        self._host = host

    def _require_host(self, host):
        resolved = host if host is not None else self._host
        if resolved is None:
            raise RuntimeError(
                f"no PluginHost attached for cloud printer {self._dev_id}"
            )
        return resolved

    def get_status(self) -> PrinterStatus:
        with self._lock:
            return self._status.model_copy()

    def set_name(self, name: str) -> None:
        with self._lock:
            self._status.name = name

    def set_machine_model(self, machine_model: str) -> None:
        with self._lock:
            self._status.machine_model = machine_model

    def get_ams_trays(self) -> list[dict]:
        with self._lock:
            return list(self._ams_trays)

    def get_ams_info(self) -> tuple[list[dict], list[dict], dict | None]:
        """Return (trays, units, vt_tray) — same shape as the LAN client."""
        with self._lock:
            return (
                list(self._ams_trays),
                list(self._ams_units),
                dict(self._vt_tray) if self._vt_tray else None,
            )

    async def get_ams_info_async(
        self, wait_timeout: float = 2.5,
    ) -> tuple[list[dict], list[dict], dict | None]:
        """Cloud reports arrive asynchronously via the EventPump, so there's
        no cold-start barrier to await — return the current snapshot."""
        return self.get_ams_info()

    def get_command_ack(self, command: str) -> dict | None:
        """Return the last recorded ack for a command name, or None."""
        with self._lock:
            ack = self._command_acks.get(command)
            return dict(ack) if ack else None

    def apply_optimistic_speed(self, level: int) -> None:
        """Cache ``level`` as the current speed and hold off reported values.

        Same contract as :meth:`BambuMQTTClient.apply_optimistic_speed` — the
        cloud path needs it more, since a full snapshot only arrives on the
        periodic pushall (``BAMBU_CLOUD_PUSHALL_SECS``, 30s by default).
        """
        with self._lock:
            self._status.speed_level = level
            self._speed_level_hold_until = (
                time.monotonic() + SPEED_LEVEL_HOLD_SECONDS
            )

    def clear_speed_hold(self) -> None:
        """Drop the optimistic-speed hold-off — reported ``spd_lvl`` wins again."""
        with self._lock:
            self._speed_level_hold_until = 0.0

    def set_status_change_callback(self, callback) -> None:
        """Register a ``(prev, new)`` snapshot callback — same contract as
        :meth:`BambuMQTTClient.set_status_change_callback`."""
        self._status_change_callback = callback

    def set_print_report_callback(self, callback) -> None:
        """Receive the same partial ``print`` payload surface as LAN MQTT."""
        self._print_report_callback = callback

    def set_print_source_registrar(self, callback) -> None:
        self._print_source_registrar = callback

    def register_print_source(self, **kwargs) -> str | None:
        callback = self._print_source_registrar
        return callback(**kwargs) if callback is not None else None

    async def handle_event(self, event: dict) -> None:
        """Dispatch an EventPump event for this device.

        Only ``OnMessage`` events whose ``dev_id`` matches this client are
        applied. All others are silently ignored.
        """
        if event.get("dev_id") != self._dev_id:
            return
        if event.get("kind") != "OnMessage":
            return

        raw = event.get("payload", "")
        try:
            msg = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            logger.warning("dev=%s: cloud payload is not valid JSON", self._dev_id)
            return

        # get_version carries the AMS module list (ams_f1/0 = Lite, n3f/0 =
        # 2 Pro, …) — the authoritative AMS hardware type. Parse it before the
        # print early-return so the type is known when AMS reports arrive.
        info = msg.get("info")
        if isinstance(info, dict) and info.get("command") == "get_version":
            detected = parse_ams_module_types(info)
            if detected:
                with self._lock:
                    self._ams_module_types.update(detected)
                logger.info(
                    "dev=%s AMS module types: %s", self._dev_id,
                    {k: v.value for k, v in detected.items()},
                )
            return

        print_info = msg.get("print", {})
        if not print_info:
            return

        with self._lock:
            prev_snapshot = self._status.model_copy(deep=True)
            ack = extract_command_ack(print_info)
            if ack is not None:
                self._command_acks[ack["command"]] = ack
            # Receiving cloud reports is this transport's notion of liveness —
            # the UI hides all controls for printers with online == False.
            self._status.online = True
            self._gcode_state = apply_print_payload(
                self._status,
                print_info,
                gcode_state=self._gcode_state,
                speed_level_hold_until=self._speed_level_hold_until,
            )
            # AMS — shared parser, identical to the LAN path. Pass the
            # get_version-derived module types so AMS Lite is recognised.
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
            new_snapshot = self._status.model_copy(deep=True)

        callback = self._status_change_callback
        if callback is not None:
            try:
                callback(prev_snapshot, new_snapshot)
            except Exception:
                logger.exception("Status change callback raised")
        report_callback = self._print_report_callback
        if report_callback is not None:
            try:
                report_callback(dict(print_info))
            except Exception:
                logger.exception("Print report callback raised")

    async def handle_update_status(self, event: dict) -> None:
        """OnUpdateStatus event handler — dispatched from the EventPump."""
        if self._progress is None:
            return
        await self._progress.put(event)

    async def send_command(
        self,
        *,
        envelope: dict,
        host=None,
        qos: int = 0,
    ) -> int:
        """Publish a command envelope to this printer via the cloud relay.

        The envelope is whatever ``build_<command>`` from ``app.mqtt_client``
        produces — the same JSON shape the LAN path uses.

        :param envelope: Command dict (e.g. ``{"print": {"command": "pause", ...}}``).
        :param host: Optional :class:`~app.cloud.plugin_host.PluginHost`
            override; defaults to the attached host.
        :param qos: MQTT QoS level (0 = at-most-once, 1 = at-least-once).
        :returns: Plugin return code — 0 on success, negative on error.
        """
        host = self._require_host(host)
        result = await host.call("send_message", {
            "dev_id": self._dev_id,
            "payload": json.dumps(envelope),
            "qos": qos,
        })
        return result.get("rc", -1)

    async def submit_print(
        self,
        *,
        print_params: dict,
        host=None,
        progress_timeout: float = 900.0,
    ) -> AsyncIterator[dict]:
        """Submit a print job and yield SSE-shape frames as progress arrives.

        Frames:
            ``{"event": "progress", "stage": N, "code": M, "msg": "..."}``
            ``{"event": "done"}`` on Finished (stage 6)
            ``{"event": "error", "code": N, "msg": "..."}`` on ERROR (stage 7)

        ``progress_timeout`` bounds the wait *between* OnUpdateStatus events:
        if the host dies or drops the terminal frame, an error frame is
        yielded instead of blocking the HTTP request forever (the in-flight
        slot is released either way).
        """
        from app.cloud.error_codes import error_message

        if self._progress is not None:
            # A submission is already running; reject without disturbing it so
            # the HTTP layer returns 409 (a double-click, typically) instead of
            # crashing with a 500.
            yield {
                "event": "error",
                "code": PRINT_IN_FLIGHT_CODE,
                "msg": "A print is already starting on this printer",
            }
            return
        host = self._require_host(host)
        self._progress = asyncio.Queue()
        try:
            result = await host.call("start_print", print_params)
            rc = result.get("rc", -1)
            if rc != 0:
                yield {"event": "error", "code": rc, "msg": error_message(rc)}
                return
            # Wait for OnUpdateStatus events pushed via handle_update_status.
            while True:
                try:
                    ev = await asyncio.wait_for(
                        self._progress.get(), timeout=progress_timeout,
                    )
                except asyncio.TimeoutError:
                    yield {
                        "event": "error",
                        "code": -99,
                        "msg": (
                            "Timed out waiting for cloud print progress "
                            f"(no event for {progress_timeout:.0f}s)"
                        ),
                    }
                    return
                stage = ev.get("stage")
                code = ev.get("code", 0)
                if stage == 6:  # PrintingStageFinished
                    yield {"event": "done"}
                    return
                if stage == 7:  # PrintingStageERROR
                    yield {
                        "event": "error",
                        "code": code,
                        "msg": error_message(code),
                    }
                    return
                yield {
                    "event": "progress",
                    "stage": stage,
                    "code": code,
                    "msg": ev.get("msg", ""),
                }
        finally:
            self._progress = None
