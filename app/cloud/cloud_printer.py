"""Per-printer state holder updated from cloud MQTT events."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import TYPE_CHECKING, AsyncIterator

from app.models import PrinterStatus
from app.mqtt_client import apply_print_payload

logger = logging.getLogger("bambu.cloud.printer")


class CloudPrinterClient:
    """Mirrors the read surface of ``BambuMQTTClient`` for cloud printers.

    Holds a ``PrinterStatus`` updated by the EventPump's OnMessage handler.
    Thread-safe via the same lock pattern as the LAN client.

    Unlike the LAN client, the cloud path has no MQTT connection or AMS tray
    tracking — it receives pre-parsed events from the C++ EventPump and applies
    them through the same ``apply_print_payload`` function the LAN client uses,
    so the ``PrinterStatus`` shape is identical between the two paths.
    """

    def __init__(self, *, dev_id: str, name: str = "", host=None) -> None:
        self._dev_id = dev_id
        self._status = PrinterStatus(
            id=dev_id,
            name=name or f"Cloud Printer {dev_id[-4:]}",
        )
        self._gcode_state: str = "IDLE"
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

    def set_status_change_callback(self, callback) -> None:
        """Register a ``(prev, new)`` snapshot callback — same contract as
        :meth:`BambuMQTTClient.set_status_change_callback`."""
        self._status_change_callback = callback

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

        print_info = msg.get("print", {})
        if not print_info:
            return

        with self._lock:
            prev_snapshot = self._status.model_copy(deep=True)
            # Receiving cloud reports is this transport's notion of liveness —
            # the UI hides all controls for printers with online == False.
            self._status.online = True
            self._gcode_state = apply_print_payload(
                self._status,
                print_info,
                gcode_state=self._gcode_state,
            )
            new_snapshot = self._status.model_copy(deep=True)

        callback = self._status_change_callback
        if callback is not None:
            try:
                callback(prev_snapshot, new_snapshot)
            except Exception:
                logger.exception("Status change callback raised")

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

        host = self._require_host(host)
        if self._progress is not None:
            raise RuntimeError(
                "a print job is already in flight for this printer"
            )
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
