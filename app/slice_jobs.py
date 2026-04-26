"""Async slice jobs: model, persistence, and worker pool."""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import PrintEstimate

logger = logging.getLogger(__name__)


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


class SliceJobStore:
    """Persistence for slice jobs.

    Stores metadata in a single JSON file and blobs in a sibling
    `slice_jobs/` directory. All mutations are guarded by an asyncio.Lock.
    """

    def __init__(self, json_path: Path) -> None:
        self._json_path = Path(json_path)
        self._blob_dir = self._json_path.parent / "slice_jobs"
        self._json_path.parent.mkdir(parents=True, exist_ok=True)
        self._blob_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._jobs: dict[str, SliceJob] | None = None

    def input_path(self, job_id: str) -> Path:
        return self._blob_dir / f"{job_id}.input.3mf"

    def output_path(self, job_id: str) -> Path:
        return self._blob_dir / f"{job_id}.output.3mf"

    async def list_all(self) -> list[SliceJob]:
        async with self._lock:
            return list((await self._load()).values())

    async def get(self, job_id: str) -> SliceJob | None:
        async with self._lock:
            return (await self._load()).get(job_id)

    async def upsert(self, job: SliceJob) -> None:
        async with self._lock:
            jobs = dict(await self._load())
            job.touch()
            jobs[job.id] = job
            await self._flush(jobs)

    async def delete(self, job_id: str) -> None:
        async with self._lock:
            jobs = dict(await self._load())
            job = jobs.pop(job_id, None)
            if job is None:
                return
            await self._flush(jobs)
            for path_str in (job.input_path, job.output_path):
                if path_str:
                    Path(path_str).unlink(missing_ok=True)

    async def _load(self) -> dict[str, SliceJob]:
        if self._jobs is not None:
            return self._jobs
        if not self._json_path.exists():
            self._jobs = {}
            return self._jobs
        try:
            data = json.loads(self._json_path.read_text())
        except json.JSONDecodeError:
            logger.exception("Corrupt slice_jobs.json; starting empty")
            self._jobs = {}
            return self._jobs
        self._jobs = {entry["id"]: SliceJob.from_dict(entry) for entry in data}
        return self._jobs

    async def _flush(self, jobs: dict[str, SliceJob]) -> None:
        tmp = self._json_path.with_suffix(".json.tmp")
        payload = json.dumps(
            [j.to_dict() for j in jobs.values()],
            indent=2,
        )
        tmp.write_text(payload)
        tmp.replace(self._json_path)
        self._jobs = jobs
