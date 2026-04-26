"""Async slice jobs: model, persistence, and worker pool."""

from __future__ import annotations

import enum
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import PrintEstimate


class SliceJobStatus(str, enum.Enum):
    QUEUED = "queued"
    SLICING = "slicing"
    UPLOADING = "uploading"
    PRINTING = "printing"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {
            SliceJobStatus.READY,
            SliceJobStatus.PRINTING,
            SliceJobStatus.FAILED,
            SliceJobStatus.CANCELLED,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SliceJob:
    id: str
    created_at: str
    updated_at: str

    # inputs
    filename: str
    machine_profile: str
    process_profile: str
    filament_profiles: list | dict
    plate_id: int
    plate_type: str
    project_filament_count: int | None

    # target
    printer_id: str | None
    auto_print: bool

    # blobs (paths as strings for JSON-friendliness; converted to Path in code)
    input_path: str
    output_path: str | None = None

    # progress
    status: SliceJobStatus = SliceJobStatus.QUEUED
    progress: int = 0
    phase: str | None = None

    # result
    estimate: dict | None = None  # PrintEstimate.model_dump
    settings_transfer: dict | None = None
    output_size: int | None = None

    # failure
    error: str | None = None

    @classmethod
    def new(
        cls,
        *,
        filename: str,
        machine_profile: str,
        process_profile: str,
        filament_profiles: list | dict,
        plate_id: int,
        plate_type: str,
        project_filament_count: int | None,
        printer_id: str | None,
        auto_print: bool,
        input_path: Path,
    ) -> "SliceJob":
        ts = _now()
        return cls(
            id=uuid.uuid4().hex[:12],
            created_at=ts,
            updated_at=ts,
            filename=filename,
            machine_profile=machine_profile,
            process_profile=process_profile,
            filament_profiles=filament_profiles,
            plate_id=plate_id,
            plate_type=plate_type,
            project_filament_count=project_filament_count,
            printer_id=printer_id,
            auto_print=auto_print,
            input_path=str(input_path),
        )

    def touch(self) -> None:
        self.updated_at = _now()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SliceJob":
        data = dict(data)
        data["status"] = SliceJobStatus(data["status"])
        return cls(**data)

    @property
    def estimate_model(self) -> PrintEstimate | None:
        if not self.estimate:
            return None
        return PrintEstimate(**self.estimate)
