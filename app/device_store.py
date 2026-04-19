"""JSON-backed persistence for APNs devices and active Live Activities."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DeviceRecord:
    id: str
    name: str
    device_token: str
    live_activity_start_token: str | None
    subscribed_printers: list[str] = field(default_factory=list)
    registered_at: str = ""
    last_seen_at: str = ""


@dataclass
class ActiveActivity:
    device_id: str
    printer_id: str
    activity_update_token: str
    started_at: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class DeviceStore:
    """Thread-safe registry backed by a JSON file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._devices: dict[str, DeviceRecord] = {}
        self._activities: dict[tuple[str, str], ActiveActivity] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load device store %s: %s", self._path, exc)
            return
        for d in raw.get("devices", []):
            rec = DeviceRecord(
                id=d["id"], name=d.get("name", ""),
                device_token=d.get("device_token", ""),
                live_activity_start_token=d.get("live_activity_start_token") or None,
                subscribed_printers=d.get("subscribed_printers", []),
                registered_at=d.get("registered_at", ""),
                last_seen_at=d.get("last_seen_at", ""),
            )
            self._devices[rec.id] = rec
        for a in raw.get("active_activities", []):
            act = ActiveActivity(
                device_id=a["device_id"], printer_id=a["printer_id"],
                activity_update_token=a["activity_update_token"],
                started_at=a.get("started_at", ""),
            )
            self._activities[(act.device_id, act.printer_id)] = act

    def _save_locked(self) -> None:
        raw = {
            "devices": [asdict(d) for d in self._devices.values()],
            "active_activities": [asdict(a) for a in self._activities.values()],
        }
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(raw, indent=2))
        tmp.replace(self._path)

    def list_devices(self) -> list[DeviceRecord]:
        with self._lock:
            return list(self._devices.values())

    def get_device(self, device_id: str) -> DeviceRecord | None:
        with self._lock:
            return self._devices.get(device_id)

    def upsert_device(self, record: DeviceRecord) -> None:
        with self._lock:
            existing = self._devices.get(record.id)
            if existing and not record.registered_at:
                record.registered_at = existing.registered_at
            if not record.registered_at:
                record.registered_at = _now_iso()
            record.last_seen_at = _now_iso()
            self._devices[record.id] = record
            self._save_locked()

    def remove_device(self, device_id: str) -> None:
        with self._lock:
            self._devices.pop(device_id, None)
            self._activities = {
                k: v for k, v in self._activities.items() if v.device_id != device_id
            }
            self._save_locked()

    def add_activity(self, activity: ActiveActivity) -> None:
        with self._lock:
            if not activity.started_at:
                activity.started_at = _now_iso()
            self._activities[(activity.device_id, activity.printer_id)] = activity
            self._save_locked()

    def remove_activity(self, device_id: str, printer_id: str) -> None:
        with self._lock:
            self._activities.pop((device_id, printer_id), None)
            self._save_locked()

    def list_activities_for_printer(self, printer_id: str) -> list[ActiveActivity]:
        with self._lock:
            return [a for a in self._activities.values() if a.printer_id == printer_id]

    def invalidate_token(self, token: str) -> None:
        """Remove any device_token / start_token / activity_token equal to ``token``."""
        if not token:
            return
        with self._lock:
            changed = False
            for dev in self._devices.values():
                if dev.device_token == token:
                    dev.device_token = ""
                    changed = True
                if dev.live_activity_start_token == token:
                    dev.live_activity_start_token = None
                    changed = True
            for key in list(self._activities.keys()):
                if self._activities[key].activity_update_token == token:
                    del self._activities[key]
                    changed = True
            if changed:
                self._save_locked()

    def subscribers_for_printer(self, printer_id: str) -> list[DeviceRecord]:
        with self._lock:
            return [
                d for d in self._devices.values()
                if "*" in d.subscribed_printers or printer_id in d.subscribed_printers
            ]
