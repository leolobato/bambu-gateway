# Async Slice Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fire-and-forget slice-job system: clients submit a 3MF + slice params, get a `job_id`, and check progress/pick up the result later. Multiple clients can slice in parallel up to a configurable limit.

**Architecture:** New `app/slice_jobs.py` module owns a `SliceJob` dataclass, a `SliceJobStore` (atomic JSON + blob persistence), and a `SliceJobManager` (asyncio queue + worker pool). Existing `/api/print-stream` and `/api/print-preview` become thin wrappers; `/api/print` accepts `job_id` (alias `preview_id`). Optional APNs `slice_ready`/`slice_failed` pushes via the existing `NotificationHub`. See spec at `docs/superpowers/specs/2026-04-26-async-slice-jobs-design.md`.

**Tech Stack:** Python 3.11+, FastAPI, asyncio, httpx, pydantic-settings, pytest + pytest-asyncio (already in `requirements.txt`).

---

## File Structure

**Create:**
- `app/slice_jobs.py` — `SliceJob`, `SliceJobStore`, `SliceJobManager` (~400 LOC).
- `tests/__init__.py` — empty marker.
- `tests/conftest.py` — pytest fixtures (`tmp_jobs_dir`, fake `SlicerClient`, fake `printer_service`).
- `tests/test_slice_job_model.py`
- `tests/test_slice_job_store.py`
- `tests/test_slice_job_manager.py`
- `tests/test_slice_job_recovery.py`
- `tests/test_slice_jobs_api.py` — exercises the FastAPI endpoints via `httpx.ASGITransport`.
- `pyproject.toml` (or `pytest.ini`) — pytest configuration.

**Modify:**
- `app/config.py` — add `slice_max_concurrent` setting.
- `app/main.py` — wire `SliceJobManager` into `lifespan`; add `/api/slice-jobs/*` endpoints; rewrite `/api/print-stream` and `/api/print-preview` as wrappers; add `job_id` parameter to `/api/print`.
- `app/notification_hub.py` — add `notify_slice_terminal(job, kind)` that pushes a `slice_ready` / `slice_failed` alert.
- `app/models.py` — add `SliceJobResponse` Pydantic model for API output.

**Boundaries:**
- `slice_jobs.py` does NOT touch FastAPI, route handlers, or HTTP framing. It only depends on `SlicerClient`, `PrinterService`, `parse_3mf`, `models`, `config_store` (for the storage path).
- `main.py` glue layer constructs request payloads and forwards results; never reaches into `SliceJobManager` internals.

---

## Task 1: Set up tests scaffolding

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `pyproject.toml`

- [ ] **Step 1: Create empty test package marker**

```bash
mkdir -p tests
touch tests/__init__.py
```

- [ ] **Step 2: Create pyproject.toml with pytest config**

Write `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
filterwarnings = [
    "ignore::DeprecationWarning",
]
```

- [ ] **Step 3: Create conftest with shared fixtures**

Write `tests/conftest.py`:

```python
"""Shared fixtures for slice-job tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def tmp_jobs_dir(tmp_path: Path) -> Path:
    """Return a temp directory with a slice_jobs/ subdir for blob storage."""
    (tmp_path / "slice_jobs").mkdir()
    return tmp_path


@pytest.fixture
def fake_slicer():
    """Return a MagicMock SlicerClient. Tests configure slice_stream() per case."""
    client = MagicMock()
    return client


@pytest.fixture
def fake_printer_service():
    """Return a MagicMock PrinterService with sensible defaults."""
    svc = MagicMock()
    svc.default_printer_id.return_value = "PRINTER1"
    return svc
```

- [ ] **Step 4: Verify pytest discovers the test directory**

Run: `pytest --collect-only`
Expected: `collected 0 items` (no failures).

- [ ] **Step 5: Commit**

```bash
git add tests pyproject.toml
git commit -m "Add pytest scaffolding for slice-job tests"
```

---

## Task 2: Add SLICE_MAX_CONCURRENT setting

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_settings.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_settings.py`:

```python
from app.config import Settings


def test_slice_max_concurrent_defaults_to_one():
    s = Settings(_env_file=None)
    assert s.slice_max_concurrent == 1


def test_slice_max_concurrent_reads_env(monkeypatch):
    monkeypatch.setenv("SLICE_MAX_CONCURRENT", "3")
    s = Settings(_env_file=None)
    assert s.slice_max_concurrent == 3
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `pytest tests/test_settings.py -v`
Expected: `AttributeError: 'Settings' object has no attribute 'slice_max_concurrent'`.

- [ ] **Step 3: Add setting**

In `app/config.py`, inside the `Settings` class, add (right after the `max_file_size_mb` line):

```python
    # Slice job concurrency
    slice_max_concurrent: int = 1
```

- [ ] **Step 4: Run test, expect PASS**

Run: `pytest tests/test_settings.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_settings.py
git commit -m "Add SLICE_MAX_CONCURRENT setting"
```

---

## Task 3: Define SliceJob dataclass + serialization

**Files:**
- Create: `app/slice_jobs.py`
- Test: `tests/test_slice_job_model.py`

This task introduces the data model only — no store, no manager. Keeps the file small and easy to verify.

- [ ] **Step 1: Write failing test**

Create `tests/test_slice_job_model.py`:

```python
from pathlib import Path

from app.slice_jobs import SliceJob, SliceJobStatus


def test_new_job_has_queued_status_and_zero_progress(tmp_jobs_dir: Path):
    job = SliceJob.new(
        filename="cube.3mf",
        machine_profile="GM014",
        process_profile="0.20mm",
        filament_profiles={"0": "GFL99"},
        plate_id=1,
        plate_type="",
        project_filament_count=1,
        printer_id="PRINTER1",
        auto_print=False,
        input_path=tmp_jobs_dir / "slice_jobs" / "abc.input.3mf",
    )
    assert job.status == SliceJobStatus.QUEUED
    assert job.progress == 0
    assert job.error is None
    assert len(job.id) == 12
    assert job.created_at == job.updated_at


def test_job_round_trips_through_dict(tmp_jobs_dir: Path):
    job = SliceJob.new(
        filename="cube.3mf",
        machine_profile="GM014",
        process_profile="0.20mm",
        filament_profiles={"0": "GFL99"},
        plate_id=1,
        plate_type="",
        project_filament_count=1,
        printer_id="PRINTER1",
        auto_print=True,
        input_path=tmp_jobs_dir / "slice_jobs" / "abc.input.3mf",
    )
    job.progress = 42
    job.phase = "slicing"

    rebuilt = SliceJob.from_dict(job.to_dict())
    assert rebuilt == job


def test_terminal_helper():
    assert SliceJobStatus.READY.is_terminal
    assert SliceJobStatus.PRINTING.is_terminal
    assert SliceJobStatus.FAILED.is_terminal
    assert SliceJobStatus.CANCELLED.is_terminal
    assert not SliceJobStatus.QUEUED.is_terminal
    assert not SliceJobStatus.SLICING.is_terminal
    assert not SliceJobStatus.UPLOADING.is_terminal
```

- [ ] **Step 2: Run, expect ImportError**

Run: `pytest tests/test_slice_job_model.py -v`
Expected: `ModuleNotFoundError: No module named 'app.slice_jobs'`.

- [ ] **Step 3: Implement model**

Create `app/slice_jobs.py`:

```python
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
```

- [ ] **Step 4: Run, expect PASS**

Run: `pytest tests/test_slice_job_model.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/slice_jobs.py tests/test_slice_job_model.py
git commit -m "Add SliceJob dataclass and SliceJobStatus enum"
```

---

## Task 4: Build SliceJobStore (atomic JSON + blob storage)

**Files:**
- Modify: `app/slice_jobs.py`
- Test: `tests/test_slice_job_store.py`

The store handles persistence: atomic JSON writes, blob read/write, listing, deletion. Single asyncio.Lock guards the JSON file.

- [ ] **Step 1: Write failing tests**

Create `tests/test_slice_job_store.py`:

```python
import asyncio
from pathlib import Path

import pytest

from app.slice_jobs import SliceJob, SliceJobStatus, SliceJobStore


def _make_job(tmp_jobs_dir: Path, **overrides) -> SliceJob:
    return SliceJob.new(
        filename=overrides.get("filename", "cube.3mf"),
        machine_profile="GM014",
        process_profile="0.20mm",
        filament_profiles={"0": "GFL99"},
        plate_id=1,
        plate_type="",
        project_filament_count=1,
        printer_id="PRINTER1",
        auto_print=False,
        input_path=tmp_jobs_dir / "slice_jobs" / "in.3mf",
    )


async def test_store_creates_directory_on_init(tmp_path: Path):
    base = tmp_path / "newdir"
    store = SliceJobStore(base / "slice_jobs.json")
    assert (base / "slice_jobs").exists()
    assert (await store.list_all()) == []


async def test_save_and_load_round_trip(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    job = _make_job(tmp_jobs_dir)
    await store.upsert(job)

    other = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    loaded = await other.list_all()
    assert len(loaded) == 1
    assert loaded[0].id == job.id


async def test_upsert_replaces_existing(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    job = _make_job(tmp_jobs_dir)
    await store.upsert(job)
    job.status = SliceJobStatus.SLICING
    job.progress = 50
    await store.upsert(job)

    loaded = await store.list_all()
    assert len(loaded) == 1
    assert loaded[0].status == SliceJobStatus.SLICING
    assert loaded[0].progress == 50


async def test_delete_removes_job_and_blobs(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    job = _make_job(tmp_jobs_dir)
    Path(job.input_path).write_bytes(b"input")
    output = tmp_jobs_dir / "slice_jobs" / f"{job.id}.output.3mf"
    output.write_bytes(b"output")
    job.output_path = str(output)
    await store.upsert(job)

    await store.delete(job.id)

    assert (await store.get(job.id)) is None
    assert not Path(job.input_path).exists()
    assert not output.exists()


async def test_blob_path_helpers(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    assert store.input_path("xyz") == tmp_jobs_dir / "slice_jobs" / "xyz.input.3mf"
    assert store.output_path("xyz") == tmp_jobs_dir / "slice_jobs" / "xyz.output.3mf"


async def test_atomic_write_does_not_corrupt_on_partial_failure(
    tmp_jobs_dir: Path, monkeypatch
):
    """If rename fails, the existing JSON file must remain valid."""
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    await store.upsert(_make_job(tmp_jobs_dir, filename="first.3mf"))

    original_replace = Path.replace
    calls = {"n": 0}

    def flaky_replace(self, target):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated rename failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    with pytest.raises(OSError):
        await store.upsert(_make_job(tmp_jobs_dir, filename="second.3mf"))

    monkeypatch.setattr(Path, "replace", original_replace)
    loaded = await store.list_all()
    assert len(loaded) == 1
    assert loaded[0].filename == "first.3mf"
```

- [ ] **Step 2: Run, expect ImportError**

Run: `pytest tests/test_slice_job_store.py -v`
Expected: `ImportError: cannot import name 'SliceJobStore'`.

- [ ] **Step 3: Implement SliceJobStore**

Append to `app/slice_jobs.py`:

```python
import asyncio
import json
import logging

logger = logging.getLogger(__name__)


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
        self._jobs: dict[str, SliceJob] | None = None  # cache

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
            jobs = await self._load()
            job.touch()
            jobs[job.id] = job
            await self._flush(jobs)

    async def delete(self, job_id: str) -> None:
        async with self._lock:
            jobs = await self._load()
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
```

- [ ] **Step 4: Run, expect PASS**

Run: `pytest tests/test_slice_job_store.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add app/slice_jobs.py tests/test_slice_job_store.py
git commit -m "Add SliceJobStore with atomic JSON writes and blob storage"
```

---

## Task 5: SliceJobManager — submit + happy-path slice (no auto-print)

**Files:**
- Modify: `app/slice_jobs.py`
- Test: `tests/test_slice_job_manager.py`

This task introduces `SliceJobManager` with a single worker, slicing only (no auto-print yet). Keeps the surface tight; later tasks add concurrency, cancellation, auto-print, and failure paths incrementally.

- [ ] **Step 1: Write failing test**

Create `tests/test_slice_job_manager.py`:

```python
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.slice_jobs import (
    SliceJob,
    SliceJobManager,
    SliceJobStatus,
    SliceJobStore,
)


def make_slicer(events: list[dict]):
    """Build a SlicerClient mock whose slice_stream yields the given events."""
    client = MagicMock()

    async def stream(*args, **kwargs):
        for e in events:
            yield e

    client.slice_stream = stream
    return client


async def _wait_for_status(
    store: SliceJobStore, job_id: str, target: SliceJobStatus, timeout: float = 2.0,
):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        job = await store.get(job_id)
        if job and job.status == target:
            return job
        await asyncio.sleep(0.02)
    pytest.fail(f"job {job_id} never reached {target} (last={job.status if job else None})")


async def test_submit_slice_succeeds_and_writes_output_blob(tmp_jobs_dir: Path):
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        {"event": "progress", "data": {"percent": 25}},
        {"event": "progress", "data": {"percent": 80}},
        {
            "event": "result",
            "data": {
                "file_base64": base64.b64encode(b"sliced!").decode(),
                "file_size": 7,
                "estimate": {"total_time_seconds": 1234},
            },
        },
        {"event": "done", "data": {}},
    ])
    manager = SliceJobManager(
        store=store,
        slicer=slicer,
        printer_service=MagicMock(),
        notifier=None,
        max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"original-3mf-bytes",
            filename="cube.3mf",
            machine_profile="GM014",
            process_profile="0.20mm",
            filament_profiles={"0": "GFL99"},
            plate_id=1,
            plate_type="",
            project_filament_count=1,
            printer_id=None,
            auto_print=False,
        )
        assert job.status == SliceJobStatus.QUEUED

        ready = await _wait_for_status(store, job.id, SliceJobStatus.READY)
        assert ready.progress == 100
        assert ready.output_path is not None
        assert Path(ready.output_path).read_bytes() == b"sliced!"
        assert ready.estimate == {"total_time_seconds": 1234}
        assert ready.error is None
    finally:
        await manager.stop()


async def test_progress_events_update_job_progress(tmp_jobs_dir: Path):
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    seen_progress: list[int] = []

    # Slow slicer so we can observe intermediate state.
    async def stream(*a, **kw):
        yield {"event": "progress", "data": {"percent": 33}}
        await asyncio.sleep(0.05)
        yield {"event": "progress", "data": {"percent": 66}}
        await asyncio.sleep(0.05)
        yield {
            "event": "result",
            "data": {
                "file_base64": base64.b64encode(b"x").decode(),
                "file_size": 1,
            },
        }
        yield {"event": "done", "data": {}}

    slicer = MagicMock()
    slicer.slice_stream = stream
    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="cube.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id=None, auto_print=False,
        )
        # Sample progress periodically until terminal
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            cur = await store.get(job.id)
            if cur and cur.progress not in seen_progress:
                seen_progress.append(cur.progress)
            if cur and cur.status.is_terminal:
                break
            await asyncio.sleep(0.01)
        assert any(p in seen_progress for p in (33, 66))
        assert seen_progress[-1] == 100
    finally:
        await manager.stop()
```

- [ ] **Step 2: Run, expect ImportError**

Run: `pytest tests/test_slice_job_manager.py -v`
Expected: `ImportError: cannot import name 'SliceJobManager'`.

- [ ] **Step 3: Implement minimal manager**

Append to `app/slice_jobs.py`:

```python
import base64
import time
from typing import Awaitable, Callable, Protocol


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
        # Allocate ID and write input blob first so the job record always
        # references a real file.
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
            input_path=self._store.input_path(uuid.uuid4().hex[:12]),
        )
        # Re-derive input path with actual job id
        input_path = self._store.input_path(job.id)
        input_path.write_bytes(file_data)
        job.input_path = str(input_path)

        await self._store.upsert(job)
        self._cancel_events[job.id] = asyncio.Event()
        await self._queue.put(job.id)
        return job

    async def get(self, job_id: str) -> SliceJob | None:
        return await self._store.get(job_id)

    async def list(self) -> list[SliceJob]:
        return await self._store.list_all()

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

        try:
            async for event in self._slicer.slice_stream(
                file_data, job.filename, job.machine_profile, job.process_profile,
                job.filament_profiles, plate_type=job.plate_type,
                plate=job.plate_id or 1,
            ):
                etype = event.get("event")
                edata = event.get("data") or {}
                if etype == "progress":
                    pct = int(edata.get("percent", 0))
                    job.progress = pct
                    now = time.monotonic()
                    if now - last_write >= self.PROGRESS_WRITE_INTERVAL_SECONDS:
                        await self._store.upsert(job)
                        last_write = now
                elif etype == "result":
                    result_bytes = base64.b64decode(edata["file_base64"])
                    estimate = edata.get("estimate")
                    settings_transfer = edata.get("settings_transfer")
                elif etype == "done":
                    break
        except Exception as e:
            await self._fail(job, f"Slicing failed: {e}")
            return

        if result_bytes is None:
            await self._fail(job, "Slicer produced no output")
            return

        # Write output blob
        out_path = self._store.output_path(job.id)
        out_path.write_bytes(result_bytes)
        job.output_path = str(out_path)
        job.output_size = len(result_bytes)
        job.estimate = estimate
        job.settings_transfer = settings_transfer
        job.progress = 100
        job.phase = None

        # No auto-print yet (handled in later task) — settle in READY.
        await self._set_status(job, SliceJobStatus.READY)
        await self._notify(job, "ready")

    async def _set_status(
        self, job: SliceJob, status: SliceJobStatus, *, phase: str | None = None,
    ) -> None:
        job.status = status
        job.phase = phase
        await self._store.upsert(job)

    async def _fail(self, job: SliceJob, message: str) -> None:
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
```

- [ ] **Step 4: Run, expect PASS**

Run: `pytest tests/test_slice_job_manager.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/slice_jobs.py tests/test_slice_job_manager.py
git commit -m "Add SliceJobManager with single-worker slicing happy path"
```

---

## Task 6: Concurrency limit (multiple workers in parallel)

**Files:**
- Test: `tests/test_slice_job_manager.py` (extend)

The manager already starts N workers; this task locks down the behavior with a test that submits N+1 jobs and verifies the (N+1)th waits.

- [ ] **Step 1: Write failing test**

Append to `tests/test_slice_job_manager.py`:

```python
async def test_max_concurrent_limits_parallel_slices(tmp_jobs_dir: Path):
    """With max_concurrent=2, the 3rd job must wait until one of the first two finishes."""
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    in_flight = 0
    peak = 0
    gate = asyncio.Event()

    async def stream(*a, **kw):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await gate.wait()
            yield {
                "event": "result",
                "data": {"file_base64": base64.b64encode(b"x").decode(), "file_size": 1},
            }
            yield {"event": "done", "data": {}}
        finally:
            in_flight -= 1

    slicer = MagicMock()
    slicer.slice_stream = stream

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=2,
    )
    await manager.start()
    try:
        ids = []
        for _ in range(3):
            job = await manager.submit(
                file_data=b"x", filename="c.3mf",
                machine_profile="GM014", process_profile="0.20mm",
                filament_profiles={}, plate_id=1, plate_type="",
                project_filament_count=0, printer_id=None, auto_print=False,
            )
            ids.append(job.id)

        # Give workers a chance to pick up jobs
        await asyncio.sleep(0.1)
        assert peak == 2  # never exceeded the limit
        gate.set()

        for jid in ids:
            await _wait_for_status(store, jid, SliceJobStatus.READY)
    finally:
        gate.set()
        await manager.stop()
```

- [ ] **Step 2: Run, expect PASS**

Run: `pytest tests/test_slice_job_manager.py::test_max_concurrent_limits_parallel_slices -v`
Expected: PASS (the existing implementation already enforces the limit via N workers).

If it fails, the bug is in `start()` — verify `max_concurrent` workers are being created.

- [ ] **Step 3: Commit**

```bash
git add tests/test_slice_job_manager.py
git commit -m "Verify slice manager respects max_concurrent limit"
```

---

## Task 7: Cancellation — queued jobs

**Files:**
- Modify: `app/slice_jobs.py` (add `cancel()` method)
- Test: `tests/test_slice_job_manager.py` (extend)

- [ ] **Step 1: Write failing test**

Append to `tests/test_slice_job_manager.py`:

```python
async def test_cancel_queued_job_skips_slicer(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer_called = False

    async def stream(*a, **kw):
        nonlocal slicer_called
        slicer_called = True
        yield {"event": "done", "data": {}}

    slicer = MagicMock()
    slicer.slice_stream = stream

    # Block the worker by holding a gate so we can cancel before it picks up
    gate = asyncio.Event()

    async def gated_stream(*a, **kw):
        await gate.wait()
        async for e in stream(*a, **kw):
            yield e

    slicer.slice_stream = gated_stream

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        # First job occupies the worker
        first = await manager.submit(
            file_data=b"x", filename="a.3mf", machine_profile="GM014",
            process_profile="0.20mm", filament_profiles={}, plate_id=1,
            plate_type="", project_filament_count=0, printer_id=None,
            auto_print=False,
        )
        # Second job is queued behind it
        second = await manager.submit(
            file_data=b"x", filename="b.3mf", machine_profile="GM014",
            process_profile="0.20mm", filament_profiles={}, plate_id=1,
            plate_type="", project_filament_count=0, printer_id=None,
            auto_print=False,
        )
        await asyncio.sleep(0.05)
        await manager.cancel(second.id)
        gate.set()
        cancelled = await _wait_for_status(store, second.id, SliceJobStatus.CANCELLED)
        assert cancelled.error is None
    finally:
        gate.set()
        await manager.stop()
```

- [ ] **Step 2: Run, expect FAIL**

Run: `pytest tests/test_slice_job_manager.py::test_cancel_queued_job_skips_slicer -v`
Expected: `AttributeError: 'SliceJobManager' object has no attribute 'cancel'`.

- [ ] **Step 3: Implement cancel for queued jobs**

In `app/slice_jobs.py`, add to `SliceJobManager`:

```python
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
```

And update `_run_job` to check the cancel event right after dequeue (before `_set_status(SLICING)`):

Existing block:
```python
        cancel = self._cancel_events.get(job_id)
        if cancel is not None and cancel.is_set():
            await self._set_status(job, SliceJobStatus.CANCELLED)
            return
```

This is already in the implementation from Task 5 — confirm it's there.

- [ ] **Step 4: Run, expect PASS**

Run: `pytest tests/test_slice_job_manager.py::test_cancel_queued_job_skips_slicer -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/slice_jobs.py tests/test_slice_job_manager.py
git commit -m "Support cancelling queued slice jobs"
```

---

## Task 8: Cancellation — in-flight slicing

**Files:**
- Modify: `app/slice_jobs.py` (race the slicer against cancel_event)
- Test: `tests/test_slice_job_manager.py` (extend)

- [ ] **Step 1: Write failing test**

Append to `tests/test_slice_job_manager.py`:

```python
async def test_cancel_during_slicing_aborts(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    started = asyncio.Event()
    finished_naturally = False

    async def slow_stream(*a, **kw):
        nonlocal finished_naturally
        started.set()
        try:
            await asyncio.sleep(5)  # would block past the test timeout
            yield {"event": "result", "data": {"file_base64": "", "file_size": 0}}
            yield {"event": "done", "data": {}}
            finished_naturally = True
        except asyncio.CancelledError:
            raise

    slicer = MagicMock()
    slicer.slice_stream = slow_stream

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="a.3mf", machine_profile="GM014",
            process_profile="0.20mm", filament_profiles={}, plate_id=1,
            plate_type="", project_filament_count=0, printer_id=None,
            auto_print=False,
        )
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await manager.cancel(job.id)
        cancelled = await _wait_for_status(
            store, job.id, SliceJobStatus.CANCELLED, timeout=2.0,
        )
        assert cancelled.output_path is None
        assert not finished_naturally
    finally:
        await manager.stop()
```

- [ ] **Step 2: Run, expect FAIL**

Run: `pytest tests/test_slice_job_manager.py::test_cancel_during_slicing_aborts -v`
Expected: timeout (the slicer keeps running past cancel).

- [ ] **Step 3: Implement in-flight cancellation**

Replace the `async for event in self._slicer.slice_stream(...)` block in `_run_job` with a cancel-aware variant. Replace this section:

```python
        try:
            async for event in self._slicer.slice_stream(
                file_data, job.filename, job.machine_profile, job.process_profile,
                job.filament_profiles, plate_type=job.plate_type,
                plate=job.plate_id or 1,
            ):
                # ... (existing event handling)
```

With:

```python
        try:
            agen = self._slicer.slice_stream(
                file_data, job.filename, job.machine_profile, job.process_profile,
                job.filament_profiles, plate_type=job.plate_type,
                plate=job.plate_id or 1,
            )
            try:
                while True:
                    next_task = asyncio.ensure_future(agen.__anext__())
                    cancel_task = asyncio.ensure_future(cancel.wait()) if cancel else None
                    waitables = [next_task] + ([cancel_task] if cancel_task else [])
                    done, pending = await asyncio.wait(
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
                    if next_task in done:
                        if cancel_task is not None:
                            cancel_task.cancel()
                        try:
                            event = next_task.result()
                        except StopAsyncIteration:
                            break
                        etype = event.get("event")
                        edata = event.get("data") or {}
                        if etype == "progress":
                            pct = int(edata.get("percent", 0))
                            job.progress = pct
                            now = time.monotonic()
                            if now - last_write >= self.PROGRESS_WRITE_INTERVAL_SECONDS:
                                await self._store.upsert(job)
                                last_write = now
                        elif etype == "result":
                            result_bytes = base64.b64decode(edata["file_base64"])
                            estimate = edata.get("estimate")
                            settings_transfer = edata.get("settings_transfer")
                        elif etype == "done":
                            break
            finally:
                try:
                    await agen.aclose()
                except Exception:
                    pass
```

(Note: the new block reuses the same `result_bytes` / `estimate` / `settings_transfer` / `last_write` locals declared earlier in `_run_job`.)

- [ ] **Step 4: Run, expect PASS**

Run: `pytest tests/test_slice_job_manager.py::test_cancel_during_slicing_aborts -v`
Expected: PASS within 2s.

- [ ] **Step 5: Run full manager suite to catch regressions**

Run: `pytest tests/test_slice_job_manager.py -v`
Expected: all prior tests still pass.

- [ ] **Step 6: Commit**

```bash
git add app/slice_jobs.py tests/test_slice_job_manager.py
git commit -m "Cancel in-flight slice by racing slicer against cancel event"
```

---

## Task 8b: Extract AMS / tray helpers from `main.py`

**Files:**
- Modify: `app/filament_selection.py` (add helpers)
- Modify: `app/main.py` (import from new home, delete local copies)
- Test: `tests/test_filament_selection.py`

Auto-print (Task 9) needs `_build_ams_mapping`, `_extract_selected_tray_slots`, and `_validate_selected_trays`. They currently live as private helpers in `app/main.py`. Extract them so `slice_jobs.py` can use them without circular imports.

- [ ] **Step 1: Move helpers to `app/filament_selection.py`**

Cut the function bodies of `_extract_selected_tray_slots`, `_build_ams_mapping`, and `_validate_selected_trays` from `app/main.py` (around lines 151, 173, 741) and paste them into `app/filament_selection.py` as public functions: `extract_selected_tray_slots`, `build_ams_mapping`, `validate_selected_trays`. Strip the leading underscore.

`validate_selected_trays` takes `printer_service` as a parameter (it currently uses the module-level global). Update its signature:

```python
async def validate_selected_trays(
    filament_payload, printer_id: str, printer_service,
) -> str | None:
    # ... (same body as before, replacing `printer_service` global with the param)
```

- [ ] **Step 2: Update `app/main.py` to import from new home**

Add at the top:

```python
from app.filament_selection import (
    build_ams_mapping,
    extract_selected_tray_slots,
    validate_selected_trays,
)
```

Replace each call site:
- `_validate_selected_trays(payload, pid)` → `await validate_selected_trays(payload, pid, printer_service)`
- `_build_ams_mapping(...)` → `build_ams_mapping(...)`
- `_extract_selected_tray_slots(...)` → `extract_selected_tray_slots(...)`

Delete the now-unused private definitions in `app/main.py`.

- [ ] **Step 3: Add a small test to lock down behavior**

Create `tests/test_filament_selection.py`:

```python
from app.filament_selection import build_ams_mapping, extract_selected_tray_slots


def test_extract_returns_empty_for_list_payload():
    assert extract_selected_tray_slots(["GFL99"]) == {}


def test_extract_picks_tray_slots():
    payload = {"0": {"tray_slot": 2}, "1": {"tray_slot": 0}}
    assert extract_selected_tray_slots(payload) == {0: 2, 1: 0}


def test_build_ams_mapping_with_count():
    payload = {"0": {"tray_slot": 2}, "1": {"tray_slot": 0}}
    mapping, use = build_ams_mapping(payload, project_filament_count=3)
    assert mapping == [2, 0, -1]
    assert use is True


def test_build_ams_mapping_no_selection():
    mapping, use = build_ams_mapping(["GFL99"])
    assert mapping is None
    assert use is False
```

- [ ] **Step 4: Run tests**

Run: `pytest tests -v`
Expected: all green; the `/api/print` and `/api/print-stream` endpoints still work because they call the same logic via the new module.

- [ ] **Step 5: Commit**

```bash
git add app/filament_selection.py app/main.py tests/test_filament_selection.py
git commit -m "Extract AMS mapping helpers to filament_selection module"
```

---

## Task 9: Auto-print — happy path

**Files:**
- Modify: `app/slice_jobs.py` (add upload+print branch)
- Test: `tests/test_slice_job_manager.py` (extend)

After slicing succeeds, if `auto_print` and the printer is idle, upload the sliced bytes and start the print. Reuse `printer_service.submit_print` (the same call `_background_submit` makes today). AMS mapping comes from the helpers extracted in Task 8b.

- [ ] **Step 1: Write failing test**

Append to `tests/test_slice_job_manager.py`:

```python
async def test_auto_print_uploads_when_printer_idle(tmp_jobs_dir: Path):
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        {"event": "result",
         "data": {"file_base64": base64.b64encode(b"sliced").decode(),
                  "file_size": 6}},
        {"event": "done", "data": {}},
    ])

    submit_calls = []
    printer_service = MagicMock()

    def fake_submit(printer_id, file_data, filename, **kwargs):
        submit_calls.append((printer_id, filename, len(file_data)))
        # Simulate progress callback
        cb = kwargs.get("progress_callback")
        if cb:
            cb(len(file_data))

    printer_service.submit_print = fake_submit

    # Mock printer status: gcode_state=IDLE
    status = MagicMock()
    status.gcode_state = "IDLE"
    status.online = True
    printer_service.get_status = MagicMock(return_value=status)

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=printer_service,
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="cube.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id="PRINTER1",
            auto_print=True,
        )
        terminal = await _wait_for_status(store, job.id, SliceJobStatus.PRINTING)
        assert submit_calls == [("PRINTER1", "cube.3mf", 6)]
        assert terminal.error is None
    finally:
        await manager.stop()
```

- [ ] **Step 2: Run, expect FAIL**

Expected: job lands in `READY` (auto-print not yet implemented), test asserts PRINTING.

- [ ] **Step 3: Add helper + auto-print branch**

In `app/slice_jobs.py`, add a module-level helper:

```python
_PRINTER_BUSY_STATES = {"RUNNING", "PAUSE", "PREPARE"}


def _is_printer_idle(printer_service, printer_id: str) -> bool:
    """Return True iff the named printer is online and not currently printing."""
    if not printer_id:
        return False
    status = printer_service.get_status(printer_id)
    if status is None or not getattr(status, "online", False):
        return False
    return getattr(status, "gcode_state", "IDLE") not in _PRINTER_BUSY_STATES
```

Then in `_run_job`, replace the final block:

```python
        # No auto-print yet (handled in later task) — settle in READY.
        await self._set_status(job, SliceJobStatus.READY)
        await self._notify(job, "ready")
```

with:

```python
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
```

Add the `_auto_print` method. It validates trays + derives AMS mapping using the helpers from Task 8b, then uploads via `printer_service.submit_print` in a thread, polling for cancel.

Add this import to the top of `app/slice_jobs.py`:

```python
from app.filament_selection import build_ams_mapping, validate_selected_trays
from app.upload_tracker import UploadCancelledError
```

Add the method:

```python
    async def _auto_print(self, job: SliceJob, file_data: bytes) -> None:
        """Validate trays, derive AMS mapping, upload + start the print."""
        # Validate filament/tray selections (auto_print can't proceed without).
        tray_error = await validate_selected_trays(
            job.filament_profiles, job.printer_id, self._printer_service,
        )
        if tray_error is not None:
            # Degrade to ready: sliced bytes are good, just can't auto-print.
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
```

- [ ] **Step 4: Run, expect PASS**

Run: `pytest tests/test_slice_job_manager.py::test_auto_print_uploads_when_printer_idle -v`
Expected: PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest tests/test_slice_job_manager.py -v`
Expected: all prior tests still pass (auto_print=False jobs still settle in READY).

- [ ] **Step 6: Commit**

```bash
git add app/slice_jobs.py tests/test_slice_job_manager.py
git commit -m "Auto-print sliced job when target printer is idle"
```

---

## Task 10: Auto-print degrades to READY when printer busy

**Files:**
- Test: `tests/test_slice_job_manager.py` (extend)

The behavior is already implemented in Task 9; this task locks it down with a test.

- [ ] **Step 1: Write test**

Append to `tests/test_slice_job_manager.py`:

```python
async def test_auto_print_degrades_to_ready_when_printer_busy(tmp_jobs_dir: Path):
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        {"event": "result",
         "data": {"file_base64": base64.b64encode(b"sliced").decode(),
                  "file_size": 6}},
        {"event": "done", "data": {}},
    ])

    printer_service = MagicMock()
    busy_status = MagicMock()
    busy_status.gcode_state = "RUNNING"
    busy_status.online = True
    printer_service.get_status = MagicMock(return_value=busy_status)
    printer_service.submit_print = MagicMock()

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=printer_service,
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="c.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id="PRINTER1", auto_print=True,
        )
        ready = await _wait_for_status(store, job.id, SliceJobStatus.READY)
        assert ready.output_path is not None
        printer_service.submit_print.assert_not_called()
    finally:
        await manager.stop()


async def test_auto_print_degrades_to_ready_when_printer_offline(tmp_jobs_dir: Path):
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        {"event": "result",
         "data": {"file_base64": base64.b64encode(b"sliced").decode(),
                  "file_size": 6}},
        {"event": "done", "data": {}},
    ])

    printer_service = MagicMock()
    offline = MagicMock()
    offline.gcode_state = "IDLE"
    offline.online = False
    printer_service.get_status = MagicMock(return_value=offline)
    printer_service.submit_print = MagicMock()

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=printer_service,
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="c.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id="PRINTER1", auto_print=True,
        )
        await _wait_for_status(store, job.id, SliceJobStatus.READY)
        printer_service.submit_print.assert_not_called()
    finally:
        await manager.stop()
```

- [ ] **Step 2: Run, expect PASS**

Run: `pytest tests/test_slice_job_manager.py -v -k "degrades"`
Expected: 2 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_slice_job_manager.py
git commit -m "Lock down auto-print degrade-to-ready behavior"
```

---

## Task 11: Failure paths

**Files:**
- Test: `tests/test_slice_job_manager.py` (extend)

Lock down: slicer raises → `failed`; slicer yields no result → `failed`; auto_print upload exception → `failed`.

- [ ] **Step 1: Write tests**

Append to `tests/test_slice_job_manager.py`:

```python
async def test_slicer_exception_marks_job_failed(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")

    async def boom(*a, **kw):
        if False:
            yield {}
        raise RuntimeError("slicer boom")

    slicer = MagicMock()
    slicer.slice_stream = boom

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="c.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id=None, auto_print=False,
        )
        failed = await _wait_for_status(store, job.id, SliceJobStatus.FAILED)
        assert "slicer boom" in (failed.error or "")
    finally:
        await manager.stop()


async def test_no_result_event_marks_job_failed(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([{"event": "done", "data": {}}])
    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="c.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id=None, auto_print=False,
        )
        failed = await _wait_for_status(store, job.id, SliceJobStatus.FAILED)
        assert "no output" in (failed.error or "").lower()
    finally:
        await manager.stop()


async def test_upload_exception_marks_job_failed(tmp_jobs_dir: Path):
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        {"event": "result",
         "data": {"file_base64": base64.b64encode(b"sliced").decode(),
                  "file_size": 6}},
        {"event": "done", "data": {}},
    ])

    printer_service = MagicMock()
    idle = MagicMock()
    idle.gcode_state = "IDLE"
    idle.online = True
    printer_service.get_status = MagicMock(return_value=idle)

    def boom(*a, **kw):
        raise RuntimeError("ftp boom")

    printer_service.submit_print = boom

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=printer_service,
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="c.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id="PRINTER1", auto_print=True,
        )
        failed = await _wait_for_status(store, job.id, SliceJobStatus.FAILED)
        assert "ftp boom" in (failed.error or "")
        # Output blob preserved so user can retry via /api/print { job_id }
        assert failed.output_path is not None
        assert Path(failed.output_path).exists()
    finally:
        await manager.stop()
```

- [ ] **Step 2: Add cancel-during-upload test**

Append:

```python
async def test_cancel_during_upload_aborts(tmp_jobs_dir: Path):
    import base64

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    slicer = make_slicer([
        {"event": "result",
         "data": {"file_base64": base64.b64encode(b"sliced" * 1024).decode(),
                  "file_size": 6144}},
        {"event": "done", "data": {}},
    ])

    printer_service = MagicMock()
    idle = MagicMock()
    idle.gcode_state = "IDLE"
    idle.online = True
    printer_service.get_status = MagicMock(return_value=idle)

    upload_started = asyncio.Event()

    def slow_submit(printer_id, file_data, filename, **kwargs):
        upload_started.set()
        cb = kwargs.get("progress_callback")
        # Simulate FTP chunk loop; progress_cb raises when cancelled.
        for _ in range(100):
            cb(64)
            import time as _t
            _t.sleep(0.05)

    printer_service.submit_print = slow_submit

    # Stub validate_selected_trays / build_ams_mapping by clearing filament_profiles
    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=printer_service,
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    try:
        job = await manager.submit(
            file_data=b"x", filename="c.3mf",
            machine_profile="GM014", process_profile="0.20mm",
            filament_profiles={}, plate_id=1, plate_type="",
            project_filament_count=0, printer_id="PRINTER1", auto_print=True,
        )
        await asyncio.wait_for(upload_started.wait(), timeout=2.0)
        await manager.cancel(job.id)
        cancelled = await _wait_for_status(
            store, job.id, SliceJobStatus.CANCELLED, timeout=2.0,
        )
        assert cancelled.error is None
    finally:
        await manager.stop()
```

(Note: `validate_selected_trays` is called with empty filament_profiles, which short-circuits to "no selection → no validation needed → returns None". If your implementation differs, add a monkeypatch on `app.slice_jobs.validate_selected_trays` to return `None` directly.)

- [ ] **Step 3: Run, expect PASS**

Run: `pytest tests/test_slice_job_manager.py -v -k "failed or fail or cancel_during_upload"`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_slice_job_manager.py
git commit -m "Lock down slice-job failure and upload-cancel paths"
```

---

## Task 12: Startup recovery

**Files:**
- Modify: `app/slice_jobs.py` (add `recover_on_startup` to manager)
- Test: `tests/test_slice_job_recovery.py`

On gateway start, jobs in `slicing`/`uploading` flip to `failed("interrupted")`. Jobs in `queued` are re-enqueued.

- [ ] **Step 1: Write failing test**

Create `tests/test_slice_job_recovery.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.slice_jobs import (
    SliceJob,
    SliceJobManager,
    SliceJobStatus,
    SliceJobStore,
)


def _seed_job(tmp_jobs_dir: Path, status: SliceJobStatus) -> SliceJob:
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    job = SliceJob.new(
        filename="x.3mf",
        machine_profile="GM014",
        process_profile="0.20mm",
        filament_profiles={},
        plate_id=1,
        plate_type="",
        project_filament_count=0,
        printer_id=None,
        auto_print=False,
        input_path=tmp_jobs_dir / "slice_jobs" / "x.input.3mf",
    )
    job.status = status
    Path(job.input_path).write_bytes(b"x")
    import asyncio
    asyncio.run(store.upsert(job))
    return job


async def test_interrupted_slicing_marked_failed(tmp_jobs_dir: Path):
    job = _seed_job(tmp_jobs_dir, SliceJobStatus.SLICING)

    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    manager = SliceJobManager(
        store=store, slicer=MagicMock(), printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.recover_on_startup()
    recovered = await store.get(job.id)
    assert recovered.status == SliceJobStatus.FAILED
    assert "interrupted" in (recovered.error or "").lower()


async def test_interrupted_uploading_marked_failed(tmp_jobs_dir: Path):
    job = _seed_job(tmp_jobs_dir, SliceJobStatus.UPLOADING)
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    manager = SliceJobManager(
        store=store, slicer=MagicMock(), printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.recover_on_startup()
    recovered = await store.get(job.id)
    assert recovered.status == SliceJobStatus.FAILED


async def test_queued_job_is_re_enqueued(tmp_jobs_dir: Path):
    job = _seed_job(tmp_jobs_dir, SliceJobStatus.QUEUED)
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")

    import base64
    slicer = MagicMock()

    async def stream(*a, **kw):
        yield {
            "event": "result",
            "data": {"file_base64": base64.b64encode(b"x").decode(), "file_size": 1},
        }
        yield {"event": "done", "data": {}}

    slicer.slice_stream = stream

    manager = SliceJobManager(
        store=store, slicer=slicer, printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.recover_on_startup()
    await manager.start()
    try:
        import asyncio
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            cur = await store.get(job.id)
            if cur and cur.status == SliceJobStatus.READY:
                break
            await asyncio.sleep(0.02)
        assert (await store.get(job.id)).status == SliceJobStatus.READY
    finally:
        await manager.stop()


async def test_terminal_jobs_left_alone(tmp_jobs_dir: Path):
    job = _seed_job(tmp_jobs_dir, SliceJobStatus.READY)
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    manager = SliceJobManager(
        store=store, slicer=MagicMock(), printer_service=MagicMock(),
        notifier=None, max_concurrent=1,
    )
    await manager.recover_on_startup()
    assert (await store.get(job.id)).status == SliceJobStatus.READY
```

- [ ] **Step 2: Run, expect FAIL**

Run: `pytest tests/test_slice_job_recovery.py -v`
Expected: `AttributeError: 'SliceJobManager' object has no attribute 'recover_on_startup'`.

- [ ] **Step 3: Implement recovery**

Add to `SliceJobManager` in `app/slice_jobs.py`:

```python
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
```

- [ ] **Step 4: Run, expect PASS**

Run: `pytest tests/test_slice_job_recovery.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/slice_jobs.py tests/test_slice_job_recovery.py
git commit -m "Recover slice-job state on gateway startup"
```

---

## Task 13: Wire SliceJobManager into FastAPI lifespan

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Import and instantiate**

At the top of `app/main.py`, add to imports:

```python
from app.slice_jobs import SliceJobManager, SliceJobStore
```

Add a module-level global next to `printer_service`:

```python
slice_jobs: SliceJobManager | None = None
```

- [ ] **Step 2: Wire into lifespan**

Inside the `lifespan` function in `app/main.py`, after `slicer_client` is initialized and before `yield`, add:

```python
    global slice_jobs
    if slicer_client is not None:
        store_path = config_store._config_path.parent / "slice_jobs.json"
        store = SliceJobStore(store_path)
        slice_jobs = SliceJobManager(
            store=store,
            slicer=slicer_client,
            printer_service=printer_service,
            notifier=(
                notification_hub.notify_slice_terminal
                if notification_hub is not None else None
            ),
            max_concurrent=settings.slice_max_concurrent,
        )
        await slice_jobs.recover_on_startup()
        await slice_jobs.start()
```

After the existing `yield` line, add (before any other shutdown):

```python
    if slice_jobs is not None:
        await slice_jobs.stop()
```

- [ ] **Step 3: Verify the app still starts**

Run: `python -c "import app.main"`
Expected: no import errors.

Run: `pytest tests -v`
Expected: all existing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add app/main.py
git commit -m "Wire SliceJobManager into FastAPI lifespan"
```

(Note: `notification_hub.notify_slice_terminal` doesn't exist yet; Task 15 adds it. Until then, lifespan will pass `None` because `notification_hub` is `None` when push is disabled, which is the typical dev setup. If you have push enabled in dev, temporarily change the wiring to `notifier=None` and revert it in Task 15.)

---

## Task 14: Add `/api/slice-jobs` REST endpoints

**Files:**
- Modify: `app/main.py`
- Modify: `app/models.py`
- Test: `tests/test_slice_jobs_api.py`

- [ ] **Step 1: Add response model**

In `app/models.py`, add (after the existing model classes):

```python
class SliceJobResponse(BaseModel):
    job_id: str
    status: str
    progress: int
    phase: str | None = None
    filename: str
    printer_id: str | None = None
    auto_print: bool
    created_at: str
    updated_at: str
    estimate: dict | None = None
    settings_transfer: dict | None = None
    output_size: int | None = None
    error: str | None = None


class SliceJobListResponse(BaseModel):
    jobs: list[SliceJobResponse]
```

- [ ] **Step 2: Add helper to build response**

In `app/main.py`, add (near other helpers around line ~140):

```python
def _slice_job_to_response(job) -> SliceJobResponse:
    return SliceJobResponse(
        job_id=job.id,
        status=job.status.value,
        progress=job.progress,
        phase=job.phase,
        filename=job.filename,
        printer_id=job.printer_id,
        auto_print=job.auto_print,
        created_at=job.created_at,
        updated_at=job.updated_at,
        estimate=job.estimate,
        settings_transfer=job.settings_transfer,
        output_size=job.output_size,
        error=job.error,
    )
```

Import `SliceJobResponse, SliceJobListResponse` from `app.models`.

- [ ] **Step 3: Add the create endpoint**

In `app/main.py`, near the other `/api/print*` endpoints, add:

```python
@app.post("/api/slice-jobs", response_model=SliceJobResponse, status_code=202)
async def create_slice_job(
    file: UploadFile,
    machine_profile: str = Form(...),
    process_profile: str = Form(...),
    filament_profiles: str = Form(...),
    plate_id: int = Form(0),
    plate_type: str = Form(""),
    printer_id: str = Form(""),
    auto_print: bool = Form(False),
):
    if slice_jobs is None or slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="Slicing not available: ORCASLICER_API_URL not configured",
        )
    if not file.filename or not file.filename.lower().endswith(".3mf"):
        raise HTTPException(status_code=400, detail="File must be a .3mf file")

    file_data = await file.read()
    if len(file_data) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {settings.max_file_size_mb} MB limit",
        )
    try:
        info = parse_3mf(file_data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse 3MF: {e}")

    try:
        parsed_filaments = json.loads(filament_profiles)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="filament_profiles must be valid JSON")

    if auto_print and not printer_id:
        raise HTTPException(
            status_code=400,
            detail="printer_id is required when auto_print=true",
        )

    job = await slice_jobs.submit(
        file_data=file_data,
        filename=file.filename,
        machine_profile=machine_profile,
        process_profile=process_profile,
        filament_profiles=parsed_filaments,
        plate_id=plate_id,
        plate_type=plate_type.strip(),
        project_filament_count=len(info.filaments),
        printer_id=printer_id or None,
        auto_print=auto_print,
    )
    return _slice_job_to_response(job)
```

- [ ] **Step 4: Add list / get / cancel / delete / clear endpoints**

```python
@app.get("/api/slice-jobs", response_model=SliceJobListResponse)
async def list_slice_jobs():
    if slice_jobs is None:
        return SliceJobListResponse(jobs=[])
    jobs = await slice_jobs.list()
    return SliceJobListResponse(
        jobs=[_slice_job_to_response(j) for j in jobs],
    )


@app.get("/api/slice-jobs/{job_id}", response_model=SliceJobResponse)
async def get_slice_job(job_id: str):
    if slice_jobs is None:
        raise HTTPException(status_code=404, detail="Slice jobs disabled")
    job = await slice_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _slice_job_to_response(job)


@app.post("/api/slice-jobs/{job_id}/cancel", response_model=SliceJobResponse)
async def cancel_slice_job(job_id: str):
    if slice_jobs is None:
        raise HTTPException(status_code=404, detail="Slice jobs disabled")
    job = await slice_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    await slice_jobs.cancel(job_id)
    job = await slice_jobs.get(job_id)
    return _slice_job_to_response(job)


@app.delete("/api/slice-jobs/{job_id}", status_code=204)
async def delete_slice_job(job_id: str):
    if slice_jobs is None:
        raise HTTPException(status_code=404, detail="Slice jobs disabled")
    job = await slice_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.status.is_terminal:
        await slice_jobs.cancel(job_id)
    # Wait briefly for cancel to settle
    for _ in range(20):
        cur = await slice_jobs.get(job_id)
        if cur is None or cur.status.is_terminal:
            break
        await asyncio.sleep(0.05)
    await slice_jobs._store.delete(job_id)


class _ClearBody(BaseModel):
    statuses: list[str] | None = None


_DEFAULT_CLEAR_STATUSES = {"ready", "printing", "failed", "cancelled"}


@app.post("/api/slice-jobs/clear", response_model=SliceJobListResponse)
async def clear_slice_jobs(body: _ClearBody | None = None):
    if slice_jobs is None:
        return SliceJobListResponse(jobs=[])
    targets = set(body.statuses) if body and body.statuses else _DEFAULT_CLEAR_STATUSES
    deleted = []
    for job in await slice_jobs.list():
        if job.status.value in targets and job.status.is_terminal:
            await slice_jobs._store.delete(job.id)
            deleted.append(_slice_job_to_response(job))
    return SliceJobListResponse(jobs=deleted)
```

(Note: reaching into `slice_jobs._store` is intentional. The store is the single source of truth for deletion; the manager's surface is about workers, not record retention. Add `delete_terminal(job_id)` to `SliceJobManager` if you prefer a clean API surface.)

- [ ] **Step 5: Add `_ClearBody` import**

`app/main.py` does NOT currently import `BaseModel` directly (it gets all models from `app.models`). Add at the top of `app/main.py`:

```python
from pydantic import BaseModel
```

Verify with: `grep -n "from pydantic import BaseModel" app/main.py` — expected: one match.

- [ ] **Step 6: Write API tests**

Create `tests/test_slice_jobs_api.py`:

```python
import asyncio
import base64
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest


@pytest.fixture
async def app_client(tmp_path: Path, monkeypatch):
    """Boot the FastAPI app with stubbed printer service and slicer."""
    from app import config_store
    from app.config import settings
    import app.main as main_mod

    config_store.set_path(tmp_path / "printers.json")
    monkeypatch.setattr(settings, "orcaslicer_api_url", "http://stub")

    # Stub parse_3mf so any byte payload parses to an empty filament list
    monkeypatch.setattr(main_mod, "parse_3mf", lambda data: MagicMock(filaments=[]))

    # Stub printer service
    main_mod.printer_service = MagicMock()
    main_mod.printer_service.default_printer_id.return_value = "PRINTER1"
    idle_status = MagicMock()
    idle_status.gcode_state = "IDLE"
    idle_status.online = True
    main_mod.printer_service.get_status = MagicMock(return_value=idle_status)

    # Stub slicer
    from app.slice_jobs import SliceJobManager, SliceJobStore
    slicer = MagicMock()

    async def stream(*a, **kw):
        yield {
            "event": "result",
            "data": {"file_base64": base64.b64encode(b"sliced").decode(),
                     "file_size": 6},
        }
        yield {"event": "done", "data": {}}

    slicer.slice_stream = stream
    main_mod.slicer_client = slicer

    # Stub manager
    store = SliceJobStore(tmp_path / "slice_jobs.json")
    main_mod.slice_jobs = SliceJobManager(
        store=store, slicer=slicer, printer_service=main_mod.printer_service,
        notifier=None, max_concurrent=1,
    )
    await main_mod.slice_jobs.start()

    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await main_mod.slice_jobs.stop()


async def test_create_returns_202_with_job_id(app_client):
    resp = await app_client.post(
        "/api/slice-jobs",
        files={"file": ("cube.3mf", b"x", "application/octet-stream")},
        data={
            "machine_profile": "GM014",
            "process_profile": "0.20mm",
            "filament_profiles": "{}",
            "auto_print": "false",
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    assert body["status"] in ("queued", "slicing", "ready")


async def test_list_includes_created_jobs(app_client):
    await app_client.post(
        "/api/slice-jobs",
        files={"file": ("cube.3mf", b"x", "application/octet-stream")},
        data={
            "machine_profile": "GM014",
            "process_profile": "0.20mm",
            "filament_profiles": "{}",
        },
    )
    resp = await app_client.get("/api/slice-jobs")
    assert resp.status_code == 200
    assert len(resp.json()["jobs"]) == 1


async def test_auto_print_without_printer_id_is_400(app_client):
    resp = await app_client.post(
        "/api/slice-jobs",
        files={"file": ("cube.3mf", b"x", "application/octet-stream")},
        data={
            "machine_profile": "GM014",
            "process_profile": "0.20mm",
            "filament_profiles": "{}",
            "auto_print": "true",
        },
    )
    assert resp.status_code == 400


async def test_get_unknown_job_404(app_client):
    resp = await app_client.get("/api/slice-jobs/deadbeef")
    assert resp.status_code == 404


async def test_clear_terminal_jobs(app_client):
    # Create + wait for terminal
    create = await app_client.post(
        "/api/slice-jobs",
        files={"file": ("cube.3mf", b"x", "application/octet-stream")},
        data={
            "machine_profile": "GM014",
            "process_profile": "0.20mm",
            "filament_profiles": "{}",
        },
    )
    job_id = create.json()["job_id"]
    for _ in range(40):
        cur = await app_client.get(f"/api/slice-jobs/{job_id}")
        if cur.json()["status"] == "ready":
            break
        await asyncio.sleep(0.05)
    resp = await app_client.post("/api/slice-jobs/clear", json={})
    assert resp.status_code == 200
    assert any(j["job_id"] == job_id for j in resp.json()["jobs"])
    assert (await app_client.get(f"/api/slice-jobs/{job_id}")).status_code == 404
```

- [ ] **Step 7: Run, expect PASS**

Run: `pytest tests/test_slice_jobs_api.py -v`
Expected: 5 passed.

- [ ] **Step 8: Commit**

```bash
git add app/main.py app/models.py tests/test_slice_jobs_api.py
git commit -m "Expose /api/slice-jobs REST endpoints"
```

---

## Task 15: APNs notifications for slice terminal states

**Files:**
- Modify: `app/notification_hub.py`
- Test: `tests/test_notification_hub_slice.py`

Add `notify_slice_terminal(job, kind)` method that sends a simple alert to all devices subscribed to the job's `printer_id` (if set) or all devices (if not).

- [ ] **Step 1: Inspect the existing alert send pattern**

Read `app/notification_hub.py` lines 269-298 (the `_send_alerts` method) to confirm the `apns.send_alert` signature: `device_token`, `title`, `body`, `event_type`, `printer_id`.

- [ ] **Step 2: Write failing test**

Create `tests/test_notification_hub_slice.py`:

```python
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.notification_hub import NotificationHub
from app.slice_jobs import SliceJob, SliceJobStatus


def _job(printer_id: str | None = "PRINTER1", status=SliceJobStatus.READY) -> SliceJob:
    j = SliceJob.new(
        filename="cube.3mf", machine_profile="GM014", process_profile="0.20mm",
        filament_profiles={}, plate_id=1, plate_type="",
        project_filament_count=0, printer_id=printer_id, auto_print=False,
        input_path=Path("/tmp/x.3mf"),
    )
    j.status = status
    return j


async def test_notify_ready_pushes_to_subscribers():
    apns = MagicMock()
    apns.send_alert = AsyncMock(return_value=SimpleNamespace(should_remove=False))
    store = MagicMock()
    store.subscribers_for_printer.return_value = [
        SimpleNamespace(device_token="tok-A"),
        SimpleNamespace(device_token="tok-B"),
    ]
    hub = NotificationHub(apns=apns, device_store=store)

    await hub.notify_slice_terminal(_job(), "ready")

    assert apns.send_alert.await_count == 2
    args = apns.send_alert.await_args_list[0].kwargs
    assert args["event_type"] == "slice_ready"


async def test_notify_failed_pushes_with_error():
    apns = MagicMock()
    apns.send_alert = AsyncMock(return_value=SimpleNamespace(should_remove=False))
    store = MagicMock()
    store.subscribers_for_printer.return_value = [
        SimpleNamespace(device_token="tok-A"),
    ]
    hub = NotificationHub(apns=apns, device_store=store)
    job = _job(status=SliceJobStatus.FAILED)
    job.error = "slicer unreachable"

    await hub.notify_slice_terminal(job, "failed")

    assert apns.send_alert.await_count == 1
    body = apns.send_alert.await_args.kwargs["body"]
    assert "slicer unreachable" in body


async def test_notify_with_no_printer_id_broadcasts_to_all():
    apns = MagicMock()
    apns.send_alert = AsyncMock(return_value=SimpleNamespace(should_remove=False))
    store = MagicMock()
    store.list_devices.return_value = [
        SimpleNamespace(device_token="tok-A"),
        SimpleNamespace(device_token="tok-B"),
    ]
    hub = NotificationHub(apns=apns, device_store=store)

    await hub.notify_slice_terminal(_job(printer_id=None), "ready")

    assert apns.send_alert.await_count == 2
    store.list_devices.assert_called_once()
```

- [ ] **Step 3: Run, expect FAIL**

Run: `pytest tests/test_notification_hub_slice.py -v`
Expected: `AttributeError: 'NotificationHub' object has no attribute 'notify_slice_terminal'`.

- [ ] **Step 4: Implement notify_slice_terminal**

Inside `class NotificationHub` in `app/notification_hub.py`, add:

```python
    async def notify_slice_terminal(self, job, kind: str) -> None:
        """Push a slice_ready / slice_failed alert.

        Scoped to subscribers of `job.printer_id` if set; otherwise broadcasts
        to all known devices. Safe no-op if the device store is empty.
        """
        if kind == "ready":
            event_type = "slice_ready"
            title = "Slice ready"
            body = f"{job.filename} is ready to print"
        elif kind == "failed":
            event_type = "slice_failed"
            title = "Slice failed"
            body = job.error or f"Slicing {job.filename} failed"
        else:
            return

        if job.printer_id:
            subs = self._store.subscribers_for_printer(job.printer_id)
        else:
            subs = self._store.list_devices()

        for dev in subs:
            if not getattr(dev, "device_token", None):
                continue
            try:
                result = await self._apns.send_alert(
                    device_token=dev.device_token,
                    title=title, body=body,
                    event_type=event_type,
                    printer_id=job.printer_id or "",
                )
                self._handle_result(result, dev.device_token)
            except Exception:
                logger.exception("notify_slice_terminal: APNs send failed")
```

- [ ] **Step 5: Verify `DeviceStore.list_devices` exists**

If `DeviceStore` lacks a `list_devices()` method, add one. Search:

```bash
grep -n "def " app/device_store.py
```

If not present, add the simplest implementation that returns all registered devices.

- [ ] **Step 6: Run, expect PASS**

Run: `pytest tests/test_notification_hub_slice.py -v`
Expected: 3 passed.

- [ ] **Step 7: Verify lifespan wiring picks it up**

Re-read the `lifespan` block in `app/main.py` from Task 13. Confirm `notifier=notification_hub.notify_slice_terminal if notification_hub is not None else None` is there. If you temporarily set it to `None` in Task 13, restore it now.

- [ ] **Step 8: Commit**

```bash
git add app/notification_hub.py app/device_store.py tests/test_notification_hub_slice.py
git commit -m "Add slice_ready / slice_failed APNs notifications"
```

---

## Task 16: Rewrite `/api/print-stream` as wrapper

**Files:**
- Modify: `app/main.py`

The handler creates a slice job (via the manager), then SSE-tails the job's progress + terminal state. Output bytes for `slice_only`/`preview` come from the manager's output blob.

- [ ] **Step 1: Write replacement handler**

In `app/main.py`, replace the body of `print_file_stream` (the function defined around line 1291) with a job-based implementation. Keep the function signature unchanged so existing clients keep working.

```python
@app.post("/api/print-stream")
async def print_file_stream(
    file: UploadFile,
    printer_id: str = Form(""),
    plate_id: int = Form(0),
    machine_profile: str = Form(""),
    process_profile: str = Form(""),
    filament_profiles: str = Form(""),
    plate_type: str = Form(""),
    slice_only: bool = Form(False),
    preview: bool = Form(False),
):
    """Slice and optionally print a 3MF, streaming progress via SSE.

    Implemented as a thin wrapper over the slice-job manager: creates a job,
    then tails its progress until terminal. Output bytes for slice_only /
    preview are read from the job's output blob and base64-encoded into the
    final `result` SSE event for backward compat with existing clients.
    """
    if slice_jobs is None or slicer_client is None:
        raise HTTPException(status_code=400, detail="Slicing not available")
    if not file.filename or not file.filename.lower().endswith(".3mf"):
        raise HTTPException(status_code=400, detail="File must be a .3mf file")
    if not machine_profile or not process_profile:
        raise HTTPException(
            status_code=400,
            detail="machine_profile and process_profile are required",
        )

    file_data = await file.read()
    if len(file_data) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {settings.max_file_size_mb} MB limit",
        )
    try:
        info = parse_3mf(file_data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse 3MF: {e}")

    filament_payload, filament_error = await _resolve_slice_filament_payload(
        [f.setting_id for f in info.filaments],
        filament_profiles,
        printer_id,
    )
    if filament_error is not None or filament_payload is None:
        raise HTTPException(status_code=400, detail=filament_error)

    auto_print = not slice_only and not preview
    pid = printer_id or printer_service.default_printer_id() or ""
    if auto_print and not pid:
        raise HTTPException(status_code=404, detail="No printers configured")

    job = await slice_jobs.submit(
        file_data=file_data,
        filename=file.filename,
        machine_profile=machine_profile,
        process_profile=process_profile,
        filament_profiles=filament_payload,
        plate_id=plate_id,
        plate_type=plate_type.strip(),
        project_filament_count=len(info.filaments),
        printer_id=pid or None,
        auto_print=auto_print,
    )

    async def generate():
        last_progress = -1
        last_status = None
        while True:
            cur = await slice_jobs.get(job.id)
            if cur is None:
                yield _sse_event("error", {"error": "Job disappeared"})
                yield _sse_event("done", {})
                return

            if cur.progress != last_progress:
                last_progress = cur.progress
                yield _sse_event("progress", {"percent": cur.progress})

            if cur.status.value != last_status:
                last_status = cur.status.value
                yield _sse_event("status", {
                    "phase": cur.phase or cur.status.value,
                    "message": cur.status.value,
                })

            if cur.status.is_terminal:
                if cur.status.value == "failed":
                    yield _sse_event("error", {"error": cur.error or "Failed"})
                else:
                    payload = {}
                    if (slice_only or preview) and cur.output_path:
                        out_bytes = Path(cur.output_path).read_bytes()
                        payload["file_base64"] = base64.b64encode(out_bytes).decode()
                        payload["file_size"] = len(out_bytes)
                    if cur.estimate:
                        payload["estimate"] = cur.estimate
                    if cur.settings_transfer:
                        payload["settings_transfer"] = cur.settings_transfer
                    if preview:
                        payload["preview_id"] = cur.id  # backward-compat alias
                    yield _sse_event("result", payload)
                    if auto_print and cur.status.value == "printing":
                        yield _sse_event("print_started", {
                            "printer_id": cur.printer_id,
                            "file_name": cur.filename,
                            "settings_transfer": cur.settings_transfer,
                            "estimate": cur.estimate,
                        })
                yield _sse_event("done", {"job_id": cur.id})
                return
            await asyncio.sleep(0.2)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 2: Smoke-test in a browser**

Boot the dev server: `uvicorn app.main:app --reload` (with a real slicer URL configured).

Open the dashboard, upload a 3MF, and verify the slice progress + completion behave the same as before. Cancel mid-slice should also work (uses existing upload-cancel UI; the new in-flight cancel for the slicer will be a separate UI follow-up).

- [ ] **Step 3: Run unit tests**

Run: `pytest tests -v`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add app/main.py
git commit -m "Rewrite /api/print-stream as a slice-job wrapper"
```

---

## Task 17: Rewrite `/api/print-preview` as wrapper

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Replace handler body**

Replace the existing `/api/print-preview` handler with:

```python
@app.post("/api/print-preview")
async def print_preview(
    file: UploadFile,
    printer_id: str = Form(""),
    plate_id: int = Form(0),
    machine_profile: str = Form(""),
    process_profile: str = Form(""),
    filament_profiles: str = Form(""),
    plate_type: str = Form(""),
):
    """Slice synchronously via the job manager, return the sliced bytes.

    Kept for backward compat with iOS clients that still use the sync
    preview endpoint. Internally a job with auto_print=false; we wait
    for terminal state.
    """
    if slice_jobs is None or slicer_client is None:
        raise HTTPException(status_code=400, detail="Slicing not available")
    if not file.filename or not file.filename.lower().endswith(".3mf"):
        raise HTTPException(status_code=400, detail="File must be a .3mf file")

    file_data = await file.read()
    if len(file_data) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {settings.max_file_size_mb} MB limit",
        )
    if not machine_profile or not process_profile:
        raise HTTPException(
            status_code=400,
            detail="machine_profile and process_profile are required",
        )
    try:
        info = parse_3mf(file_data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse 3MF: {e}")

    filament_payload, filament_error = await _resolve_slice_filament_payload(
        [f.setting_id for f in info.filaments],
        filament_profiles,
        printer_id,
    )
    if filament_error is not None or filament_payload is None:
        raise HTTPException(status_code=400, detail=filament_error)

    job = await slice_jobs.submit(
        file_data=file_data,
        filename=file.filename,
        machine_profile=machine_profile,
        process_profile=process_profile,
        filament_profiles=filament_payload,
        plate_id=plate_id,
        plate_type=plate_type.strip(),
        project_filament_count=len(info.filaments),
        printer_id=printer_id or None,
        auto_print=False,
    )

    # Wait for terminal state (bounded — preview is meant to be sync-ish).
    deadline = asyncio.get_event_loop().time() + 600
    while asyncio.get_event_loop().time() < deadline:
        cur = await slice_jobs.get(job.id)
        if cur is None:
            raise HTTPException(status_code=500, detail="Job disappeared")
        if cur.status.is_terminal:
            break
        await asyncio.sleep(0.2)
    else:
        raise HTTPException(status_code=504, detail="Slicing timed out")

    if cur.status.value != "ready":
        raise HTTPException(
            status_code=502,
            detail=f"Slicing {cur.status.value}: {cur.error or 'no result'}",
        )

    output_bytes = Path(cur.output_path).read_bytes()
    headers = {
        "Content-Disposition": f'attachment; filename="{file.filename}"',
        "X-Preview-Id": cur.id,           # backward compat
        "X-Job-Id": cur.id,
    }
    if cur.estimate:
        headers["X-Print-Estimate"] = base64.b64encode(
            json.dumps(cur.estimate).encode(),
        ).decode()
    if cur.settings_transfer:
        if cur.settings_transfer.get("status"):
            headers["X-Settings-Transfer-Status"] = cur.settings_transfer["status"]
        if cur.settings_transfer.get("transferred"):
            headers["X-Settings-Transferred"] = json.dumps(
                cur.settings_transfer["transferred"]
            )
        if cur.settings_transfer.get("filaments"):
            headers["X-Filament-Settings-Transferred"] = json.dumps(
                cur.settings_transfer["filaments"]
            )

    return Response(
        content=output_bytes,
        media_type="application/octet-stream",
        headers=headers,
    )
```

- [ ] **Step 2: Run all tests**

Run: `pytest tests -v`
Expected: all green.

- [ ] **Step 3: Smoke test**

`curl` against a running dev server with a small 3MF + valid profiles:

```bash
curl -F file=@cube.3mf -F machine_profile=GM014 -F process_profile=0.20mm \
     -F filament_profiles='{"0":"GFL99"}' \
     http://localhost:4844/api/print-preview \
     -D /tmp/headers.txt -o /tmp/sliced.3mf
grep -i "x-preview-id\|x-job-id" /tmp/headers.txt
```
Expected: both headers present, `/tmp/sliced.3mf` is non-empty.

- [ ] **Step 4: Commit**

```bash
git add app/main.py
git commit -m "Rewrite /api/print-preview as a slice-job wrapper"
```

---

## Task 18: `/api/print` accepts `job_id`

**Files:**
- Modify: `app/main.py`

`/api/print { preview_id }` becomes `/api/print { job_id }` with `preview_id` accepted as an alias. The fast path loads from the slice-job store instead of the legacy `_pop_preview` helper.

- [ ] **Step 1: Modify the print endpoint signature**

Locate `/api/print` (around line 941). Add `job_id` parameter; keep `preview_id` as alias.

```python
@app.post("/api/print")
async def print_file(
    file: UploadFile = None,
    printer_id: str = Form(""),
    plate_id: int = Form(0),
    job_id: str = Form(""),
    preview_id: str = Form(""),       # deprecated alias for job_id
    machine_profile: str = Form(""),
    process_profile: str = Form(""),
    filament_profiles: str = Form(""),
    plate_type: str = Form(""),
    slice_only: bool = Form(False),
):
    effective_job_id = job_id or preview_id

    if effective_job_id and slice_jobs is not None:
        job = await slice_jobs.get(effective_job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status.value != "ready":
            raise HTTPException(
                status_code=409,
                detail=f"Job is {job.status.value}, not ready",
            )
        pid = printer_id or job.printer_id or printer_service.default_printer_id()
        if pid is None:
            raise HTTPException(status_code=404, detail="No printers configured")

        client = printer_service.get_client(pid)
        if client is None:
            raise HTTPException(status_code=404, detail=f"Printer {pid} not found")
        try:
            client.ensure_connected()
            if not client.get_status().online:
                raise ConnectionError(f"Printer {pid} is offline")
        except ConnectionError as e:
            raise HTTPException(status_code=409, detail=str(e))

        tray_error = await _validate_selected_trays(job.filament_profiles, pid)
        if tray_error is not None:
            raise HTTPException(status_code=409, detail=tray_error)
        ams_mapping, use_ams = _build_ams_mapping(
            job.filament_profiles,
            project_filament_count=job.project_filament_count,
        )

        file_data_job = Path(job.output_path).read_bytes()
        upload_state = upload_tracker.create(job.filename, pid, len(file_data_job))
        asyncio.get_running_loop().run_in_executor(None, lambda: _background_submit(
            upload_state, pid, file_data_job, job.filename,
            plate_id=1, ams_mapping=ams_mapping, use_ams=use_ams,
        ))

        # Mark the slice job as printing (terminal from its own perspective)
        job.status = job.status.PRINTING if False else __import__(
            "app.slice_jobs", fromlist=["SliceJobStatus"]
        ).SliceJobStatus.PRINTING
        await slice_jobs._store.upsert(job)

        return PrintResponse(
            status="uploading",
            file_name=job.filename,
            printer_id=pid,
            was_sliced=True,
            upload_id=upload_state.upload_id,
            estimate=PrintEstimate(**job.estimate) if job.estimate else None,
        )

    # ... existing non-job_id flow continues unchanged below
```

(The bulk import shenanigan is ugly — clean it up by importing `SliceJobStatus` at the top of `app/main.py`:)

```python
from app.slice_jobs import SliceJobManager, SliceJobStatus, SliceJobStore
```

Then replace:

```python
        job.status = job.status.PRINTING if False else __import__(...).SliceJobStatus.PRINTING
```

with:

```python
        job.status = SliceJobStatus.PRINTING
```

- [ ] **Step 2: Drop the legacy preview helpers**

The `_store_preview` / `_pop_preview` / `_PREVIEW_DIR` block at the top of `app/main.py` is no longer used by any production code path after Tasks 16–18. Remove them and the `_estimate_from_preview_meta` helper (also unused). This is a safe deletion: the slice-job store fully replaces the preview store.

Verify nothing imports them:

```bash
grep -rn "_pop_preview\|_store_preview\|_PREVIEW_DIR\|_estimate_from_preview_meta" app/ tests/
```
Expected: zero matches outside the lines being deleted.

- [ ] **Step 3: Run all tests + smoke-test**

Run: `pytest tests -v`
Expected: all green.

Smoke flow:
1. `POST /api/slice-jobs` (no auto_print) → job_id
2. Wait for `status=ready` via `GET /api/slice-jobs/{id}`
3. `POST /api/print` with `{job_id}` → upload starts
4. Verify printer receives the file.

- [ ] **Step 4: Commit**

```bash
git add app/main.py
git commit -m "Accept job_id on /api/print; remove legacy preview helpers"
```

---

## Task 19: End-to-end manual smoke test

**Files:** none

Final pass against a running gateway + real slicer + real printer.

- [ ] **Step 1: Restart with fresh state**

```bash
rm -f printers.json slice_jobs.json
rm -rf slice_jobs/
# (configure printers.json or env vars as usual)
python -m app
```

- [ ] **Step 2: Submit two parallel slice jobs**

In two terminals, simultaneously:

```bash
curl -F file=@bigmodel.3mf -F machine_profile=GM014 -F process_profile=0.20mm \
     -F filament_profiles='{"0":"GFL99"}' \
     http://localhost:4844/api/slice-jobs
```

With `SLICE_MAX_CONCURRENT=1` (default), verify:
- First call returns 202 with `status=queued` or `slicing`.
- Second call returns 202 with `status=queued`.
- `GET /api/slice-jobs` shows one slicing, one queued.
- Once first completes, second transitions to slicing.

- [ ] **Step 3: Restart mid-slice**

Submit a job, wait until `status=slicing`, kill the gateway (`Ctrl-C`), restart.
Expected: `GET /api/slice-jobs/{id}` returns `status=failed` with `error="interrupted by gateway restart"`. Other terminal jobs untouched.

- [ ] **Step 4: Auto-print path**

Submit with `auto_print=true` against an idle printer. Expected:
- Job transitions queued → slicing → uploading → printing.
- Printer actually starts the print.
- Submit a second auto_print job while the first is still printing → second job lands in `ready` (degraded), with APNs `slice_ready` push (if configured).

- [ ] **Step 5: Cancel mid-slice**

Submit a large file. While `status=slicing`, `POST /api/slice-jobs/{id}/cancel`.
Expected: status flips to `cancelled` within ~1s. (Slicer host keeps burning CPU until natural completion — known limitation, tracked separately on `orcaslicer-cli`.)

- [ ] **Step 6: Clear**

`POST /api/slice-jobs/clear` → all terminal jobs gone, blob directory cleaned up.

- [ ] **Step 7: Web UI sanity check**

Open the dashboard, upload via the existing UI (which uses `/api/print-stream`). Verify the wrapper rewrite preserves the same UX — slice progress, result, printer start. Check browser network tab for any unexpected 4xx/5xx.

---

## Self-review checklist (run before declaring done)

- [ ] All 19 tasks committed; `git log --oneline` is clean and descriptive.
- [ ] `pytest tests -v` is fully green.
- [ ] No references to `_pop_preview` / `_store_preview` / `_PREVIEW_DIR` remain.
- [ ] `SliceJobStatus` enum values match the strings in `SliceJobResponse.status` and the `/api/slice-jobs/clear` defaults (`"queued"`, `"slicing"`, `"uploading"`, `"printing"`, `"ready"`, `"failed"`, `"cancelled"`).
- [ ] `SliceJobManager` method names referenced from `app/main.py` (`submit`, `get`, `list`, `cancel`, `recover_on_startup`, `start`, `stop`) all exist with matching signatures.
- [ ] `notification_hub.notify_slice_terminal` is wired in lifespan and gracefully no-ops when `notification_hub is None`.
- [ ] iOS app (`../bambu-gateway-ios`) is on the to-update list; `preview_id` alias remains for one release.
- [ ] orcaslicer-cli `/cancel` endpoint is filed as a separate work item.
