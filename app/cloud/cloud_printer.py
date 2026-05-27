"""Per-printer state holder updated from cloud MQTT events."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import AsyncIterator

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

    def __init__(self, *, dev_id: str, name: str = "") -> None:
        self._dev_id = dev_id
        self._status = PrinterStatus(
            id=dev_id,
            name=name or f"Cloud Printer {dev_id[-4:]}",
        )
        self._gcode_state: str = "IDLE"
        self._lock = threading.Lock()
        # Single in-flight print job's progress channel. None = no active job.
        self._progress: asyncio.Queue | None = None

    @property
    def serial(self) -> str:
        return self._dev_id

    def get_status(self) -> PrinterStatus:
        with self._lock:
            return self._status.model_copy()

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
            self._gcode_state = apply_print_payload(
                self._status,
                print_info,
                gcode_state=self._gcode_state,
            )

    async def handle_update_status(self, event: dict) -> None:
        """OnUpdateStatus event handler — dispatched from the EventPump."""
        if self._progress is None:
            return
        await self._progress.put(event)

    async def submit_print(
        self,
        *,
        host,
        print_params: dict,
    ) -> AsyncIterator[dict]:
        """Submit a print job and yield SSE-shape frames as progress arrives.

        Frames:
            ``{"event": "progress", "stage": N, "code": M, "msg": "..."}``
            ``{"event": "done"}`` on Finished (stage 6)
            ``{"event": "error", "code": N, "msg": "..."}`` on ERROR (stage 7)
        """
        from app.cloud.error_codes import error_message

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
                ev = await self._progress.get()
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
