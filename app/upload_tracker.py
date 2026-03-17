"""In-memory tracker for FTP upload progress."""

from __future__ import annotations

import threading
import time
import uuid


class UploadState:
    """Mutable state for a single upload."""

    __slots__ = (
        "upload_id", "filename", "printer_id", "total_bytes",
        "bytes_sent", "status", "error", "_lock",
    )

    def __init__(
        self,
        upload_id: str,
        filename: str,
        printer_id: str,
        total_bytes: int,
    ) -> None:
        self.upload_id = upload_id
        self.filename = filename
        self.printer_id = printer_id
        self.total_bytes = total_bytes
        self.bytes_sent = 0
        self.status = "uploading"  # uploading | printing | completed | failed
        self.error: str | None = None
        self._lock = threading.Lock()

    @property
    def progress(self) -> int:
        """Return upload progress as 0-100."""
        if self.total_bytes <= 0:
            return 0
        return min(100, int(self.bytes_sent * 100 / self.total_bytes))

    def advance(self, chunk_size: int) -> None:
        with self._lock:
            self.bytes_sent += chunk_size

    def complete(self) -> None:
        with self._lock:
            self.bytes_sent = self.total_bytes
            self.status = "completed"

    def fail(self, error: str) -> None:
        with self._lock:
            self.status = "failed"
            self.error = error

    def set_status(self, status: str) -> None:
        with self._lock:
            self.status = status

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "upload_id": self.upload_id,
                "filename": self.filename,
                "printer_id": self.printer_id,
                "status": self.status,
                "progress": self.progress,
                "bytes_sent": self.bytes_sent,
                "total_bytes": self.total_bytes,
                "error": self.error,
            }


class UploadTracker:
    """Thread-safe registry of active uploads with auto-cleanup."""

    EXPIRY_SECONDS = 120

    def __init__(self) -> None:
        self._uploads: dict[str, tuple[UploadState, float]] = {}
        self._lock = threading.Lock()

    def create(
        self,
        filename: str,
        printer_id: str,
        total_bytes: int,
    ) -> UploadState:
        upload_id = uuid.uuid4().hex[:12]
        state = UploadState(upload_id, filename, printer_id, total_bytes)
        with self._lock:
            self._cleanup()
            self._uploads[upload_id] = (state, time.monotonic())
        return state

    def get(self, upload_id: str) -> UploadState | None:
        with self._lock:
            entry = self._uploads.get(upload_id)
            if entry is None:
                return None
            return entry[0]

    def remove(self, upload_id: str) -> None:
        with self._lock:
            self._uploads.pop(upload_id, None)

    def _cleanup(self) -> None:
        """Remove finished uploads older than EXPIRY_SECONDS."""
        now = time.monotonic()
        expired = [
            uid for uid, (state, ts) in self._uploads.items()
            if state.status in ("completed", "failed")
            and now - ts > self.EXPIRY_SECONDS
        ]
        for uid in expired:
            del self._uploads[uid]


# Module-level singleton
tracker = UploadTracker()
