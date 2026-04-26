"""Async slice jobs: model, persistence, and worker pool."""

from __future__ import annotations

import asyncio
import base64
import enum
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from app.filament_selection import build_ams_mapping, validate_selected_trays
from app.models import PrintEstimate
from app.print_estimate import extract_print_estimate
from app.upload_tracker import UploadCancelledError

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


class _SlicerLike(Protocol):
    def slice_stream(
        self,
        file_data: bytes,
        filename: str,
        machine_profile: str,
        process_profile: str,
        filament_profiles: list | dict,
        plate_type: str = "",
        plate: int = 1,
    ): ...


# Optional callback signature: notifier(job, kind) where kind is "ready" or "failed".
TerminalCallback = Callable[[SliceJob, str], Awaitable[None]]

_PRINTER_BUSY_STATES = {"RUNNING", "PAUSE", "PREPARE"}


def _is_printer_idle(printer_service, printer_id: str) -> bool:
    """Return True iff the named printer is online and not currently printing."""
    if not printer_id:
        return False
    status = printer_service.get_status(printer_id)
    if status is None or not getattr(status, "online", False):
        return False
    return getattr(status, "gcode_state", "IDLE") not in _PRINTER_BUSY_STATES


class SliceJobManager:
    """Owns the asyncio queue and worker tasks for slice jobs."""

    PROGRESS_WRITE_INTERVAL_SECONDS = 1.0

    def __init__(
        self,
        *,
        store: SliceJobStore,
        slicer: _SlicerLike,
        printer_service,
        notifier: TerminalCallback | None,
        max_concurrent: int = 1,
    ) -> None:
        self._store = store
        self._slicer = slicer
        self._printer_service = printer_service
        self._notifier = notifier
        self._max_concurrent = max(1, max_concurrent)
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for i in range(self._max_concurrent):
            self._workers.append(
                asyncio.create_task(self._worker_loop(), name=f"slice-worker-{i}")
            )

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for _ in self._workers:
            await self._queue.put("__STOP__")
        for w in self._workers:
            await w
        self._workers.clear()

    async def recover_on_startup(self) -> None:
        """Reconcile persisted jobs with the live worker state.

        - jobs in slicing/uploading -> failed("interrupted by gateway restart")
        - jobs in queued -> re-enqueued (cancel event recreated)
        - terminal jobs left alone
        """
        for job in await self._store.list_all():
            if job.status in (
                SliceJobStatus.SLICING,
                SliceJobStatus.UPLOADING,
            ):
                job.error = "interrupted by gateway restart"
                job.phase = None
                await self._set_status(job, SliceJobStatus.FAILED)
            elif job.status == SliceJobStatus.QUEUED:
                self._cancel_events[job.id] = asyncio.Event()
                await self._queue.put(job.id)

    async def submit(
        self,
        *,
        file_data: bytes,
        filename: str,
        machine_profile: str,
        process_profile: str,
        filament_profiles: list | dict,
        plate_id: int,
        plate_type: str,
        project_filament_count: int | None,
        printer_id: str | None,
        auto_print: bool,
    ) -> SliceJob:
        # Allocate job id, then write input blob at the matching path so the
        # job record always references a real file.
        job_id = uuid.uuid4().hex[:12]
        input_path = self._store.input_path(job_id)
        input_path.write_bytes(file_data)

        job = SliceJob.new(
            filename=filename,
            machine_profile=machine_profile,
            process_profile=process_profile,
            filament_profiles=filament_profiles,
            plate_id=plate_id,
            plate_type=plate_type,
            project_filament_count=project_filament_count,
            printer_id=printer_id,
            auto_print=auto_print,
            input_path=input_path,
        )
        # SliceJob.new generates its own id, but we want it to match the blob.
        job.id = job_id

        await self._store.upsert(job)
        self._cancel_events[job.id] = asyncio.Event()
        await self._queue.put(job.id)
        return job

    async def get(self, job_id: str) -> SliceJob | None:
        return await self._store.get(job_id)

    async def list(self) -> list[SliceJob]:
        return await self._store.list_all()

    async def cancel(self, job_id: str) -> bool:
        """Request cancellation of a queued or in-flight job.

        Returns True if the job existed and was non-terminal at the moment
        cancel was requested; False otherwise.
        """
        job = await self._store.get(job_id)
        if job is None or job.status.is_terminal:
            return False
        ev = self._cancel_events.get(job_id)
        if ev is not None:
            ev.set()
        return True

    async def _worker_loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            if job_id == "__STOP__":
                return
            try:
                await self._run_job(job_id)
            except Exception:
                logger.exception("slice worker crashed on job %s", job_id)
            finally:
                self._cancel_events.pop(job_id, None)

    async def _run_job(self, job_id: str) -> None:
        job = await self._store.get(job_id)
        if job is None:
            return
        cancel = self._cancel_events.get(job_id)
        if cancel is not None and cancel.is_set():
            await self._set_status(job, SliceJobStatus.CANCELLED)
            return

        await self._set_status(job, SliceJobStatus.SLICING, phase="slicing")
        file_data = Path(job.input_path).read_bytes()

        result_bytes: bytes | None = None
        estimate: dict | None = None
        settings_transfer: dict | None = None
        last_write = 0.0
        events_seen: list[str] = []

        try:
            agen = self._slicer.slice_stream(
                file_data, job.filename, job.machine_profile, job.process_profile,
                job.filament_profiles, plate_type=job.plate_type,
                plate=job.plate_id or 1,
            )
            try:
                while True:
                    next_task = asyncio.ensure_future(agen.__anext__())
                    waitables: list[asyncio.Future] = [next_task]
                    cancel_task: asyncio.Task | None = None
                    if cancel is not None:
                        cancel_task = asyncio.ensure_future(cancel.wait())
                        waitables.append(cancel_task)
                    done, _pending = await asyncio.wait(
                        waitables, return_when=asyncio.FIRST_COMPLETED,
                    )
                    if cancel_task is not None and cancel_task in done:
                        next_task.cancel()
                        try:
                            await agen.aclose()
                        except Exception:
                            pass
                        await self._set_status(job, SliceJobStatus.CANCELLED)
                        return
                    if cancel_task is not None:
                        cancel_task.cancel()
                    try:
                        event = next_task.result()
                    except StopAsyncIteration:
                        break
                    etype = event.get("event")
                    edata = event.get("data") or {}
                    events_seen.append(etype or "?")
                    logger.debug(
                        "slice job %s slicer event: %s data=%s",
                        job.id, etype, edata,
                    )
                    if etype == "progress":
                        # Slicer may emit a progress tick with `percent: null`
                        # before it has computed any. Treat null/missing/non-
                        # numeric values as "no update" and keep prior progress.
                        pct_raw = edata.get("percent")
                        if pct_raw is not None:
                            try:
                                job.progress = max(0, min(100, int(float(pct_raw))))
                            except (TypeError, ValueError):
                                pass
                        now = time.monotonic()
                        if now - last_write >= self.PROGRESS_WRITE_INTERVAL_SECONDS:
                            await self._store.upsert(job)
                            last_write = now
                    elif etype == "result":
                        b64 = edata.get("file_base64")
                        if not b64:
                            raise ValueError("result event missing file_base64")
                        result_bytes = base64.b64decode(b64)
                        estimate = edata.get("estimate")
                        settings_transfer = edata.get("settings_transfer")
                    elif etype == "error":
                        msg = (
                            (isinstance(edata, dict) and (
                                edata.get("error") or edata.get("message")
                            ))
                            or "Slicer reported an error"
                        )
                        raise RuntimeError(msg)
                    elif etype == "done":
                        break
            finally:
                try:
                    await agen.aclose()
                except Exception:
                    pass
        except Exception as e:
            logger.warning(
                "slice job %s slicer call raised after events=%s: %s",
                job.id, events_seen, e,
            )
            await self._fail(job, f"Slicing failed: {e}")
            return

        if result_bytes is None:
            await self._fail(
                job,
                f"Slicer produced no output (events seen: {events_seen or 'none'})",
            )
            return

        # If the slicer didn't return an estimate, try to extract one from the
        # sliced 3MF itself (mirrors the old /api/print-stream behavior).
        if estimate is None:
            extracted = extract_print_estimate(result_bytes)
            if extracted is not None:
                estimate = extracted.model_dump(exclude_none=True)

        out_path = self._store.output_path(job.id)
        out_path.write_bytes(result_bytes)
        job.output_path = str(out_path)
        job.output_size = len(result_bytes)
        job.estimate = estimate
        job.settings_transfer = settings_transfer
        job.progress = 100
        job.phase = None

        if not job.auto_print or not job.printer_id:
            await self._set_status(job, SliceJobStatus.READY)
            await self._notify(job, "ready")
            return

        if not _is_printer_idle(self._printer_service, job.printer_id):
            # Best-effort: degrade to READY; user starts the print manually.
            await self._set_status(job, SliceJobStatus.READY)
            await self._notify(job, "ready")
            return

        await self._auto_print(job, result_bytes)

    async def _set_status(
        self, job: SliceJob, status: SliceJobStatus, *, phase: str | None = None,
    ) -> None:
        job.status = status
        job.phase = phase
        await self._store.upsert(job)

    async def _fail(self, job: SliceJob, message: str) -> None:
        logger.warning("slice job %s failed: %s", job.id, message)
        job.error = message
        job.phase = None
        await self._set_status(job, SliceJobStatus.FAILED)
        await self._notify(job, "failed")

    async def _notify(self, job: SliceJob, kind: str) -> None:
        if self._notifier is None:
            return
        try:
            await self._notifier(job, kind)
        except Exception:
            logger.exception("slice job notifier raised for %s", job.id)

    async def _auto_print(self, job: SliceJob, file_data: bytes) -> None:
        """Validate trays, derive AMS mapping, upload + start the print."""
        tray_error = await validate_selected_trays(
            job.filament_profiles, job.printer_id, self._printer_service,
        )
        if tray_error is not None:
            # Sliced bytes are good; degrade to READY and surface the warning.
            job.error = tray_error
            await self._set_status(job, SliceJobStatus.READY)
            await self._notify(job, "ready")
            return

        ams_mapping, use_ams = build_ams_mapping(
            job.filament_profiles,
            project_filament_count=job.project_filament_count,
        )

        await self._set_status(job, SliceJobStatus.UPLOADING, phase="uploading")
        bytes_total = len(file_data)
        bytes_sent = 0
        last_write = 0.0
        cancel = self._cancel_events.get(job.id)
        loop = asyncio.get_running_loop()

        def progress_cb(chunk: int) -> None:
            nonlocal bytes_sent
            if cancel is not None and cancel.is_set():
                # Raised inside the FTP storbinary callback — aborts the upload.
                raise UploadCancelledError("Cancelled by user")
            bytes_sent += chunk

        def do_submit() -> None:
            self._printer_service.submit_print(
                job.printer_id, file_data, job.filename,
                plate_id=1,
                ams_mapping=ams_mapping,
                use_ams=use_ams,
                progress_callback=progress_cb,
            )

        future = loop.run_in_executor(None, do_submit)
        try:
            while not future.done():
                await asyncio.sleep(0.2)
                if bytes_total:
                    job.progress = min(99, int(bytes_sent * 100 / bytes_total))
                now = time.monotonic()
                if now - last_write >= self.PROGRESS_WRITE_INTERVAL_SECONDS:
                    await self._store.upsert(job)
                    last_write = now
            await future
        except UploadCancelledError:
            await self._set_status(job, SliceJobStatus.CANCELLED)
            return
        except Exception as e:
            await self._fail(job, f"Upload failed: {e}")
            return

        job.progress = 100
        job.phase = None
        await self._set_status(job, SliceJobStatus.PRINTING)
