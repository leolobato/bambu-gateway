"""Transport-neutral live print snapshots and active 3MF sources.

Firmware reports are partial updates.  ``PrintEventBroker`` accumulates those
updates into a versioned snapshot so consumers never need to understand MQTT
pushall/incremental semantics.  The same broker is fed by both LAN MQTT and the
cloud plugin client.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Callable

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
TERMINAL_STATES = {"FINISH", "FAILED", "FAIL", "CANCELLED", "CANCELED"}
ACTIVE_STATES = {
    "RUNNING", "PAUSE", "PAUSED", "PREPARE", "PREPARING", "SLICING",
}


def _merge(target: dict, update: dict) -> None:
    """Recursively merge a partial firmware report into accumulated state."""
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def _integer(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value) -> str:
    return str(value).strip() if value is not None else ""


def _reported_url(report: dict) -> str:
    for key in ("url", "project_file", "file_url"):
        value = _text(report.get(key))
        if value:
            return value
    return ""


def _active_tray(report: dict, previous: int | None) -> int | None:
    ams = report.get("ams")
    if isinstance(ams, dict) and "tray_now" in ams:
        value = _integer(ams.get("tray_now"), -1)
        return None if value < 0 else value
    if "active_tray" in report:
        value = _integer(report.get("active_tray"), -1)
        return None if value < 0 else value
    return previous


@dataclass
class PrintSource:
    job_key: str
    kind: str
    filename: str = ""
    data: bytes | None = None
    path: str = ""
    url: str = ""
    reason: str = ""
    plate_id: int = 1
    ams_mapping: list[int] | None = None

    def status(self) -> dict:
        return {
            "available": bool(self.data is not None or self.path or self.url),
            "kind": self.kind,
            "filename": self.filename,
            "url": self.url or None,
            "reason": self.reason or None,
        }


class PrintEventBroker:
    """Per-printer accumulated snapshot, fan-out, and active source registry."""

    def __init__(
        self,
        printer_id: str,
        *,
        max_queue_size: int = 32,
        acquire_connection: Callable[[], None] | None = None,
        release_connection: Callable[[], None] | None = None,
        printer_file_available: bool = True,
    ) -> None:
        self.printer_id = printer_id
        self._max_queue_size = max_queue_size
        self._acquire_connection = acquire_connection
        self._release_connection = release_connection
        self._printer_file_available = printer_file_available
        self._lock = threading.RLock()
        self._subscribers: dict[asyncio.Queue[dict], asyncio.AbstractEventLoop] = {}
        self._raw: dict = {}
        self._sources: dict[str, PrintSource] = {}
        self._pending_source: PrintSource | None = None
        self._active_job_key: str | None = None
        self._sequence = 0
        self._snapshot = self._empty_snapshot()

    def _empty_snapshot(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "sequence": 0,
            "printer_id": self.printer_id,
            "job_key": None,
            "task_id": None,
            "subtask_id": None,
            "job_name": None,
            "state": "IDLE",
            "layer": {"current": 0, "total": 0},
            "gcode_entry": None,
            "ams_mapping": [],
            "active_tray": None,
            "source": {
                "available": False,
                "kind": "unavailable",
                "filename": None,
                "url": None,
                "reason": "No active print source has been reported",
            },
            "updated_at": None,
        }

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def snapshot(self) -> dict:
        with self._lock:
            return _copy_dict(self._snapshot)

    def set_connection_lease(
        self,
        acquire: Callable[[], None] | None,
        release: Callable[[], None] | None,
    ) -> None:
        with self._lock:
            old_release = self._release_connection
            subscriber_count = len(self._subscribers)
            self._acquire_connection = acquire
            self._release_connection = release
        # Client replacement can happen while an SSE consumer is connected.
        # Transfer each outstanding lease so the replacement LAN client is
        # kept alive immediately, not only after the helper reconnects.
        changed = old_release != release
        if old_release is not None and changed:
            for _ in range(subscriber_count):
                old_release()
        if acquire is not None and changed:
            for _ in range(subscriber_count):
                acquire()

    def set_printer_file_available(self, available: bool) -> None:
        with self._lock:
            if self._printer_file_available == available:
                return
            self._printer_file_available = available
            self._snapshot = self._build_snapshot()
            snapshot = _copy_dict(self._snapshot)
        self._fan_out(snapshot)

    def register_source(
        self,
        *,
        data: bytes | None = None,
        path: str | Path | None = None,
        filename: str,
        plate_id: int,
        ams_mapping: list[int] | None,
    ) -> str:
        """Register a gateway-submitted 3MF before handing it to the printer."""
        if data is None and path is None:
            raise ValueError("a source data blob or path is required")
        digest_input = data
        if digest_input is None:
            digest_input = Path(path).read_bytes()
        digest = hashlib.sha256(digest_input).hexdigest()[:16]
        key = f"gateway:{digest}:{plate_id}:{filename}"
        source = PrintSource(
            job_key=key,
            kind="registered",
            filename=filename,
            data=bytes(digest_input),
            plate_id=plate_id,
            ams_mapping=list(ams_mapping or []),
        )
        with self._lock:
            # One active job per printer. Releasing the prior blob here avoids
            # retaining a full print history in memory.
            self._sources = {key: source}
            self._pending_source = source
            self._active_job_key = key
            self._raw["subtask_name"] = filename
            self._raw["param"] = f"Metadata/plate_{plate_id}.gcode"
            self._raw["ams_mapping"] = list(ams_mapping or [])
            self._snapshot = self._build_snapshot(preferred_job_key=key)
            snapshot = _copy_dict(self._snapshot)
        self._fan_out(snapshot)
        return key

    def update(self, report: dict) -> dict:
        """Merge one LAN/cloud firmware ``print`` report and publish snapshot."""
        if not isinstance(report, dict):
            return self.snapshot()
        with self._lock:
            previous_state = _text(self._raw.get("gcode_state")).upper()
            incoming_state = _text(report.get("gcode_state")).upper()
            incoming_name = _text(
                report.get("subtask_name") or report.get("gcode_file")
            )
            previous_name = _text(
                self._raw.get("subtask_name") or self._raw.get("gcode_file")
            )
            identity_changed = any(
                _text(report.get(field)) not in {"", "0"}
                and _text(self._raw.get(field)) not in {"", "0"}
                and _text(report.get(field)) != _text(self._raw.get(field))
                for field in ("task_id", "subtask_id", "gcode_start_time")
            )
            # A new active job after a terminal state must not inherit identity,
            # mapping, layer, or source URL from the previous print.
            if (
                incoming_state in ACTIVE_STATES
                and previous_state in TERMINAL_STATES
            ) or (
                incoming_name and previous_name and incoming_name != previous_name
                and (
                    incoming_state in ACTIVE_STATES
                    or report.get("command") == "project_file"
                )
            ) or (
                identity_changed
                and (
                    incoming_state in ACTIVE_STATES
                    or report.get("command") == "project_file"
                )
            ):
                self._raw = {}
                pending_name = (
                    self._pending_source.filename
                    if self._pending_source is not None
                    else ""
                )
                if not pending_name or (
                    incoming_name and incoming_name != pending_name
                ):
                    self._active_job_key = None
                    self._sources = {}
            _merge(self._raw, report)
            self._snapshot = self._build_snapshot()
            snapshot = _copy_dict(self._snapshot)
        self._fan_out(snapshot)
        return snapshot

    def _derive_job_key(self) -> str | None:
        if self._active_job_key is not None:
            return self._active_job_key
        task = _text(self._raw.get("task_id"))
        subtask = _text(self._raw.get("subtask_id"))
        started = _text(self._raw.get("gcode_start_time"))
        name = _text(
            self._raw.get("subtask_name") or self._raw.get("gcode_file")
        )
        meaningful = [x for x in (task, subtask, started) if x and x != "0"]
        if meaningful:
            self._active_job_key = (
                "printer:" + ":".join(meaningful + ([name] if name else []))
            )
            return self._active_job_key
        if self._pending_source is not None:
            return self._pending_source.job_key
        if name:
            self._active_job_key = (
                self._snapshot.get("job_key") or f"printer:name:{name}"
            )
            return self._active_job_key
        return None

    def _build_snapshot(self, preferred_job_key: str | None = None) -> dict:
        self._sequence += 1
        state = _text(self._raw.get("gcode_state")).upper() or "IDLE"
        key = preferred_job_key or self._derive_job_key()
        name = _text(
            self._raw.get("subtask_name") or self._raw.get("gcode_file")
        )
        mapping = self._raw.get("ams_mapping")
        if not isinstance(mapping, list):
            mapping = self._snapshot.get("ams_mapping") or []

        source = self._sources.get(key or "")
        if (
            source is not None
            and self._pending_source is not None
            and source.job_key == self._pending_source.job_key
            and state in ACTIVE_STATES
        ):
            self._pending_source = None
        if source is None and self._pending_source is not None and state not in TERMINAL_STATES:
            source = self._pending_source
            if key and key != source.job_key:
                source = PrintSource(
                    job_key=key,
                    kind=source.kind,
                    filename=source.filename,
                    data=source.data,
                    plate_id=source.plate_id,
                    ams_mapping=list(source.ams_mapping or []),
                )
                self._sources[key] = source
                self._pending_source = None
        url = _reported_url(self._raw)
        if source is None and key and url:
            scheme = url.split(":", 1)[0].lower()
            kind = "http" if scheme in {"http", "https"} else "printer_ftps"
            source = PrintSource(
                job_key=key,
                kind=kind,
                filename=Path(url).name,
                url=url,
            )
            self._sources[key] = source
        if source is not None and not mapping and source.ams_mapping is not None:
            mapping = source.ams_mapping

        if source is None:
            source_status = {
                "available": False,
                "kind": "unavailable",
                "filename": name or None,
                "url": url or None,
                "reason": "Printer did not report a retrievable 3MF source",
            }
        else:
            source_status = source.status()
            if (
                source.kind == "printer_ftps"
                and not self._printer_file_available
            ):
                source_status = {
                    **source_status,
                    "available": False,
                    "reason": "Printer FTPS credentials are unavailable",
                }

        return {
            "schema_version": SCHEMA_VERSION,
            "sequence": self._sequence,
            "printer_id": self.printer_id,
            "job_key": key,
            "task_id": _text(self._raw.get("task_id")) or None,
            "subtask_id": _text(self._raw.get("subtask_id")) or None,
            "job_name": name or (source.filename if source else None),
            "state": state,
            "layer": {
                "current": _integer(self._raw.get("layer_num"), 0),
                "total": _integer(self._raw.get("total_layer_num"), 0),
            },
            "gcode_entry": _text(
                self._raw.get("param") or self._raw.get("gcode_entry")
            ) or (
                f"Metadata/plate_{source.plate_id}.gcode"
                if source is not None else None
            ),
            "ams_mapping": [_integer(v, -1) for v in mapping],
            "active_tray": _active_tray(
                self._raw, self._snapshot.get("active_tray")
            ),
            "source": source_status,
            "updated_at": time.time(),
        }

    def source_for(self, job_key: str) -> PrintSource | None:
        with self._lock:
            current = self._snapshot.get("job_key")
            if not current or job_key != current:
                return None
            source = self._sources.get(job_key)
            return PrintSource(**source.__dict__) if source else None

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict]]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=self._max_queue_size)
        loop = asyncio.get_running_loop()
        acquire = self._acquire_connection
        if acquire is not None:
            await asyncio.to_thread(acquire)
        with self._lock:
            self._subscribers[queue] = loop
            queue.put_nowait(_copy_dict(self._snapshot))
        try:
            yield queue
        finally:
            with self._lock:
                self._subscribers.pop(queue, None)
            release = self._release_connection
            if release is not None:
                await asyncio.to_thread(release)

    def _fan_out(self, snapshot: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers.items())
        for queue, loop in subscribers:
            loop.call_soon_threadsafe(self._enqueue_latest, queue, snapshot)

    @staticmethod
    def _enqueue_latest(queue: asyncio.Queue[dict], snapshot: dict) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(_copy_dict(snapshot))
        except asyncio.QueueFull:
            logger.warning("print event queue remained full after stale drop")


def _copy_dict(value: dict) -> dict:
    """Cheap JSON-shaped deep copy without serialization overhead."""
    return {
        key: _copy_dict(item)
        if isinstance(item, dict)
        else [
            _copy_dict(element) if isinstance(element, dict) else element
            for element in item
        ]
        if isinstance(item, list)
        else item
        for key, item in value.items()
    }
