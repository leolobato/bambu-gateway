# Live Activity Push-to-Start Thumbnail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate `attributes.thumbnailData` in the gateway's APNs Live Activity `start` payload with a base64-encoded JPEG ≤2.5 KB, so prints initiated outside the iOS app render with a thumbnail.

**Architecture:** A new helper module `app/live_activity_thumbnail.py` looks up a matching `SliceJob` by filename (normalized) and returns a Pillow-compressed JPEG within the APNs size budget. `NotificationHub` gains a `slice_store` dependency and calls the helper from `_send_push_to_start`.

**Tech Stack:** Python 3.12, Pillow (new), asyncio, pytest.

**Spec:** `docs/superpowers/specs/2026-04-27-live-activity-thumbnail-design.md`

---

## File Structure

- **Create:** `app/live_activity_thumbnail.py` — `_compress_for_push` (sync) and `lookup_push_thumbnail` (async)
- **Create:** `tests/test_live_activity_thumbnail.py` — unit tests for both helpers
- **Modify:** `app/notification_hub.py` — accept `slice_store`, call helper in `_send_push_to_start`
- **Modify:** `app/main.py` — construct `SliceJobStore` once, pass to both `NotificationHub` and `SliceJobManager`
- **Modify:** `tests/test_notification_hub.py` — update `_make_hub` for new signature, add thumbnail assertions
- **Modify:** `requirements.txt` — add `Pillow`

---

## Task 1: Add Pillow dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add the dependency**

Append to `requirements.txt`:

```
Pillow==11.1.0
```

- [ ] **Step 2: Install into the project venv**

Run: `.venv/bin/pip install -r requirements.txt`
Expected: ends with `Successfully installed pillow-11.1.0` (or "already satisfied" if cached).

- [ ] **Step 3: Confirm import works**

Run: `.venv/bin/python -c "from PIL import Image; print(Image.__name__)"`
Expected: `PIL.Image`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "Add Pillow for Live Activity thumbnail compression"
```

---

## Task 2: Build `_compress_for_push` (sync helper, TDD)

**Files:**
- Create: `app/live_activity_thumbnail.py`
- Create: `tests/test_live_activity_thumbnail.py`

This task implements the pure compression function — no I/O, no slice-store coupling. Lookup orchestration is Task 3.

- [ ] **Step 1: Create the test file with the first failing test**

Create `tests/test_live_activity_thumbnail.py`:

```python
"""Unit tests for the Live Activity thumbnail helpers."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from app.live_activity_thumbnail import _compress_for_push


_PUSH_BUDGET_BYTES = 2400


def _make_png_data_url(size: tuple[int, int] = (1024, 1024)) -> str:
    """Build a realistic plate-thumbnail-style PNG data URL."""
    img = Image.new("RGBA", size, (255, 255, 255, 0))
    # A few coloured rectangles so the encoder has actual data to compress.
    for x in range(0, size[0], 64):
        for y in range(0, size[1], 64):
            color = ((x * 3) % 255, (y * 5) % 255, (x + y) % 255, 255)
            for dx in range(48):
                for dy in range(48):
                    if x + dx < size[0] and y + dy < size[1]:
                        img.putpixel((x + dx, y + dy), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def test_compress_returns_short_base64_for_realistic_input():
    data_url = _make_png_data_url()
    out = _compress_for_push(data_url)
    assert out is not None
    assert isinstance(out, str)
    # No data: prefix — raw base64.
    assert not out.startswith("data:")
    assert len(out) <= _PUSH_BUDGET_BYTES
    # Decoded bytes must be a valid JPEG.
    raw = base64.b64decode(out)
    assert raw[:3] == b"\xff\xd8\xff"
```

- [ ] **Step 2: Create a stub helper module**

Create `app/live_activity_thumbnail.py`:

```python
"""Thumbnail helpers for the Live Activity push-to-start payload."""

from __future__ import annotations


def _compress_for_push(data_url: str) -> str | None:
    raise NotImplementedError
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_live_activity_thumbnail.py -v`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 4: Implement `_compress_for_push`**

Replace the contents of `app/live_activity_thumbnail.py`:

```python
"""Thumbnail helpers for the Live Activity push-to-start payload."""

from __future__ import annotations

import base64
import io
import logging

from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

_PUSH_BUDGET_BYTES = 2400  # safety margin under Apple's 2.5 KB iOS-side cap

# Each rung is (max_dimension, jpeg_quality). We try in order; first one
# whose base64 length fits the budget wins.
_COMPRESSION_LADDER: tuple[tuple[int, int], ...] = (
    (192, 60),
    (192, 40),
    (192, 25),
    (128, 40),
)


def _strip_data_url(data_url: str) -> bytes | None:
    """Return raw image bytes from a `data:image/...;base64,...` URL."""
    if not data_url:
        return None
    marker = ";base64,"
    idx = data_url.find(marker)
    if idx < 0:
        return None
    try:
        return base64.b64decode(data_url[idx + len(marker):], validate=False)
    except (ValueError, base64.binascii.Error):
        return None


def _compress_for_push(data_url: str) -> str | None:
    """Compress a plate-thumbnail data URL to a base64 JPEG fitting the
    Live Activity push budget. Returns None on any failure or if the
    image cannot be made small enough.

    The returned string is raw base64 (no `data:` prefix), matching the
    encoding the iOS local-start path produces for
    `PrintActivityAttributes.thumbnailData`.
    """
    raw = _strip_data_url(data_url)
    if raw is None:
        logger.warning("thumbnail compress: malformed data URL")
        return None
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img.load()
            rgb = img.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.warning("thumbnail compress: cannot decode image: %s", exc)
        return None

    final_size = 0
    for max_dim, quality in _COMPRESSION_LADDER:
        candidate = rgb.copy()
        candidate.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        candidate.save(buf, format="JPEG", quality=quality, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        final_size = len(b64)
        if final_size <= _PUSH_BUDGET_BYTES:
            return b64
    logger.warning(
        "thumbnail compress: cannot fit budget; final size=%d", final_size,
    )
    return None
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_live_activity_thumbnail.py -v`
Expected: PASS.

- [ ] **Step 6: Add the remaining compression-helper tests**

Append to `tests/test_live_activity_thumbnail.py`:

```python
def test_compress_returns_none_for_empty_input():
    assert _compress_for_push("") is None


def test_compress_returns_none_for_missing_base64_marker():
    assert _compress_for_push("not a data url at all") is None


def test_compress_returns_none_for_invalid_base64():
    assert _compress_for_push("data:image/png;base64,!!!not-base64!!!") is None


def test_compress_returns_none_for_bytes_that_arent_an_image():
    junk = base64.b64encode(b"this is not an image").decode()
    assert _compress_for_push(f"data:image/png;base64,{junk}") is None


def test_compress_handles_rgba_input_by_dropping_alpha():
    # Transparent PNG — must still produce a valid JPEG (no alpha).
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    out = _compress_for_push(data_url)
    assert out is not None
    raw = base64.b64decode(out)
    assert raw[:3] == b"\xff\xd8\xff"
```

- [ ] **Step 7: Run all tests in the new file**

Run: `.venv/bin/pytest tests/test_live_activity_thumbnail.py -v`
Expected: 5 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add app/live_activity_thumbnail.py tests/test_live_activity_thumbnail.py
git commit -m "$(cat <<'EOF'
Add thumbnail compression helper for Live Activity push payloads

- Decode a slicer plate PNG data URL, downscale, and re-encode as JPEG until the base64 length fits the 2.4 KB safety budget.
- Step through a quality/size ladder; return `None` when the image cannot be made small enough or input is malformed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Build `lookup_push_thumbnail` (async orchestrator, TDD)

**Files:**
- Modify: `app/live_activity_thumbnail.py`
- Modify: `tests/test_live_activity_thumbnail.py`

- [ ] **Step 1: Add the first lookup test**

Append to `tests/test_live_activity_thumbnail.py`:

```python
import pytest

from app.live_activity_thumbnail import lookup_push_thumbnail
from app.slice_jobs import SliceJob, SliceJobStore


async def _seed_job(
    store: SliceJobStore,
    *,
    filename: str,
    thumbnail: str | None,
    updated_at: str | None = None,
) -> SliceJob:
    job = SliceJob.new(
        filename=filename,
        machine_profile="GM014",
        process_profile="0.20mm",
        filament_profiles={"0": "GFL99"},
        plate_id=1,
        plate_type="",
        project_filament_count=1,
        printer_id="PRINTER1",
        auto_print=False,
        input_path=Path(store._blob_dir) / f"{filename}.in.3mf",
    )
    job.thumbnail = thumbnail
    await store.upsert(job)
    # `upsert` calls `job.touch()` which resets `updated_at`. The store
    # caches the same instance, so mutating it here also affects what
    # `list_all` later returns. Set the override after upsert.
    if updated_at is not None:
        job.updated_at = updated_at
    return job


async def test_lookup_returns_compressed_thumbnail_for_exact_filename(
    tmp_jobs_dir: Path,
):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    await _seed_job(store, filename="cube.gcode.3mf", thumbnail=_make_png_data_url())
    out = await lookup_push_thumbnail(store, "cube.gcode.3mf")
    assert out is not None
    assert len(out) <= _PUSH_BUDGET_BYTES
```

- [ ] **Step 2: Add a stub for the orchestrator**

Append to `app/live_activity_thumbnail.py`:

```python
from app.slice_jobs import SliceJobStore


async def lookup_push_thumbnail(
    slice_store: SliceJobStore, file_name: str,
) -> str | None:
    raise NotImplementedError
```

- [ ] **Step 3: Run the test — confirm it fails**

Run: `.venv/bin/pytest tests/test_live_activity_thumbnail.py::test_lookup_returns_compressed_thumbnail_for_exact_filename -v`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 4: Implement `lookup_push_thumbnail`**

Replace the stub in `app/live_activity_thumbnail.py` (keep the existing `_compress_for_push`):

```python
def _normalize_filename(name: str) -> str:
    """Lowercase and strip Bambu's `.gcode.3mf` / `.3mf` suffixes."""
    n = (name or "").lower().strip()
    for suffix in (".gcode.3mf", ".3mf"):
        if n.endswith(suffix):
            return n[: -len(suffix)]
    return n


async def lookup_push_thumbnail(
    slice_store: SliceJobStore, file_name: str,
) -> str | None:
    """Find a matching SliceJob and return its thumbnail compressed for
    the Live Activity push payload, or None if no usable match exists."""
    target = _normalize_filename(file_name)
    if not target:
        return None
    jobs = await slice_store.list_all()
    candidates = [
        j for j in jobs
        if j.thumbnail and _normalize_filename(j.filename) == target
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda j: j.updated_at, reverse=True)
    return _compress_for_push(candidates[0].thumbnail)
```

- [ ] **Step 5: Verify the first test passes**

Run: `.venv/bin/pytest tests/test_live_activity_thumbnail.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Add the remaining lookup tests**

Append to `tests/test_live_activity_thumbnail.py`:

```python
async def test_lookup_normalizes_gcode_3mf_suffix(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    await _seed_job(store, filename="cube.3mf", thumbnail=_make_png_data_url())
    # Printer reports `subtask_name` with the `.gcode.3mf` suffix.
    out = await lookup_push_thumbnail(store, "cube.gcode.3mf")
    assert out is not None


async def test_lookup_normalizes_bare_subtask_name(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    await _seed_job(
        store, filename="cube.gcode.3mf", thumbnail=_make_png_data_url(),
    )
    # Printer reports `subtask_name` without any suffix.
    out = await lookup_push_thumbnail(store, "cube")
    assert out is not None


async def test_lookup_is_case_insensitive(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    await _seed_job(store, filename="Cube.3mf", thumbnail=_make_png_data_url())
    out = await lookup_push_thumbnail(store, "CUBE.gcode.3mf")
    assert out is not None


async def test_lookup_picks_most_recently_updated_match(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    older = await _seed_job(
        store, filename="cube.3mf",
        thumbnail=_make_png_data_url((128, 128)),
        updated_at="2026-04-26T10:00:00+00:00",
    )
    newer = await _seed_job(
        store, filename="cube.3mf",
        thumbnail=_make_png_data_url((256, 256)),
        updated_at="2026-04-27T10:00:00+00:00",
    )
    out = await lookup_push_thumbnail(store, "cube.3mf")
    assert out is not None
    # We can't compare to the source PNGs directly (they get re-compressed),
    # but we can confirm it's the larger-source match by checking dimensions
    # via PIL after decode.
    decoded = Image.open(io.BytesIO(base64.b64decode(out)))
    # The 256-source thumbnails to (192, 192); the 128 stays at 128.
    assert max(decoded.size) > 128
    assert older.id != newer.id  # sanity: distinct jobs


async def test_lookup_returns_none_when_no_match(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    await _seed_job(store, filename="other.3mf", thumbnail=_make_png_data_url())
    out = await lookup_push_thumbnail(store, "cube.3mf")
    assert out is None


async def test_lookup_returns_none_when_match_has_no_thumbnail(
    tmp_jobs_dir: Path,
):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    await _seed_job(store, filename="cube.3mf", thumbnail=None)
    out = await lookup_push_thumbnail(store, "cube.3mf")
    assert out is None


async def test_lookup_returns_none_for_empty_filename(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    await _seed_job(store, filename="cube.3mf", thumbnail=_make_png_data_url())
    assert await lookup_push_thumbnail(store, "") is None


async def test_lookup_swallows_compression_failure(tmp_jobs_dir: Path):
    store = SliceJobStore(tmp_jobs_dir / "slice_jobs.json")
    await _seed_job(
        store, filename="cube.3mf",
        thumbnail="data:image/png;base64,not-real-image-bytes-AAAA",
    )
    out = await lookup_push_thumbnail(store, "cube.3mf")
    assert out is None
```

- [ ] **Step 7: Run all tests**

Run: `.venv/bin/pytest tests/test_live_activity_thumbnail.py -v`
Expected: all tests PASS (13 total: 5 from Task 2, 8 here).

- [ ] **Step 8: Commit**

```bash
git add app/live_activity_thumbnail.py tests/test_live_activity_thumbnail.py
git commit -m "$(cat <<'EOF'
Look up Live Activity thumbnails by filename in the slice-job store

- Normalize Bambu's `.gcode.3mf` / `.3mf` suffixes so prints reported by MQTT match the slice-job filename regardless of how the
  printer announces them.
- Pick the most recently updated `SliceJob` when multiple jobs share a filename, and fall through to `None` when no match exists or
  compression fails.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire helper into `NotificationHub` (TDD)

**Files:**
- Modify: `app/notification_hub.py`
- Modify: `tests/test_notification_hub.py`

- [ ] **Step 1: Update `_make_hub` and add a positive thumbnail test**

In `tests/test_notification_hub.py`, change the imports and `_make_hub` signature, and add a new test. Modify the imports block at the top:

```python
"""Tests for NotificationHub dispatch, dedupe, and throttle."""

from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from app.apns_client import ApnsResult
from app.device_store import ActiveActivity, DeviceRecord, DeviceStore
from app.models import PrinterState, PrinterStatus, PrintJob
from app.notification_hub import NotificationHub
from app.slice_jobs import SliceJob, SliceJobStore
```

Replace the existing `_make_hub` helper (around line 60):

```python
def _make_hub(
    tmp_path, apns: FakeApns, slice_store: SliceJobStore | None = None,
) -> tuple[NotificationHub, DeviceStore, SliceJobStore]:
    store = DeviceStore(tmp_path / "devices.json")
    if slice_store is None:
        (tmp_path / "slice_jobs").mkdir(exist_ok=True)
        slice_store = SliceJobStore(tmp_path / "slice_jobs.json")
    hub = NotificationHub(
        apns=apns, device_store=store, slice_store=slice_store,
    )
    hub.start()
    hub._seen_printers.add("P01")  # skip first-status guard for tests
    return hub, store, slice_store
```

Update every existing call site of `_make_hub` in this file from:

```python
hub, store = _make_hub(tmp_path, apns)
```

to:

```python
hub, store, _slice_store = _make_hub(tmp_path, apns)
```

(There are five such call sites: the existing tests `test_pause_transition_sends_alert_to_subscribed_devices`, `test_duplicate_pause_within_30s_is_deduped`, `test_progress_tick_throttled_to_once_per_10s_per_printer`, `test_invalid_token_response_is_removed_from_store`, `test_print_started_sends_push_to_start_when_no_activity`, `test_print_started_skips_push_to_start_when_activity_exists`, and `test_terminal_state_ends_live_activity`. Update each.)

- [ ] **Step 2: Add new tests for the thumbnail wiring**

Append to `tests/test_notification_hub.py`:

```python
def _seed_thumbnail_job(slice_store: SliceJobStore, filename: str) -> None:
    """Synchronously seed a SliceJob with a real PNG thumbnail."""
    img = Image.new("RGB", (256, 256), (200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    job = SliceJob.new(
        filename=filename,
        machine_profile="GM014",
        process_profile="0.20mm",
        filament_profiles={"0": "GFL99"},
        plate_id=1,
        plate_type="",
        project_filament_count=1,
        printer_id="P01",
        auto_print=False,
        input_path=Path(slice_store._blob_dir) / f"{filename}.in.3mf",
    )
    job.thumbnail = data_url
    import asyncio
    asyncio.get_event_loop().run_until_complete(slice_store.upsert(job))


def test_print_started_includes_thumbnail_when_slice_job_matches(tmp_path):
    apns = FakeApns()
    hub, store, slice_store = _make_hub(tmp_path, apns)
    _seed_thumbnail_job(slice_store, "test.3mf")
    store.upsert_device(DeviceRecord(
        id="dev", name="iPhone", device_token="tok",
        live_activity_start_token="start-tok", subscribed_printers=["*"],
    ))
    try:
        hub.on_status_change(
            _status(PrinterState.idle, progress=0),
            _status(PrinterState.printing, progress=1),
        )
        _wait_for(lambda: len(apns.starts) == 1)
        attributes = apns.starts[0]["attributes"]
        thumb = attributes.get("thumbnailData")
        assert isinstance(thumb, str)
        assert len(thumb) > 0
        assert len(thumb) <= 2400
        # Decoded bytes must be a JPEG.
        assert base64.b64decode(thumb)[:3] == b"\xff\xd8\xff"
    finally:
        hub.stop()


def test_print_started_thumbnail_is_none_when_no_slice_job_matches(tmp_path):
    apns = FakeApns()
    hub, store, _slice_store = _make_hub(tmp_path, apns)
    store.upsert_device(DeviceRecord(
        id="dev", name="iPhone", device_token="tok",
        live_activity_start_token="start-tok", subscribed_printers=["*"],
    ))
    try:
        hub.on_status_change(
            _status(PrinterState.idle, progress=0),
            _status(PrinterState.printing, progress=1),
        )
        _wait_for(lambda: len(apns.starts) == 1)
        assert apns.starts[0]["attributes"]["thumbnailData"] is None
    finally:
        hub.stop()
```

- [ ] **Step 3: Run the tests — confirm both new ones FAIL**

Run: `.venv/bin/pytest tests/test_notification_hub.py -v`
Expected: every test fails to even import (because `NotificationHub.__init__` does not yet accept `slice_store=`). That's fine — we fix the implementation next.

- [ ] **Step 4: Update `NotificationHub.__init__`**

In `app/notification_hub.py`, add an import at the top of the file (near the other `from app.` imports):

```python
from app.live_activity_thumbnail import lookup_push_thumbnail
from app.slice_jobs import SliceJobStore
```

Update the constructor (currently around line 161):

```python
class NotificationHub:
    """Serialises event detection + APNs dispatch on a background thread."""

    def __init__(
        self,
        apns: _ApnsProtocol,
        device_store: DeviceStore,
        slice_store: SliceJobStore,
    ) -> None:
        self._apns = apns
        self._store = device_store
        self._slice_store = slice_store
        self._queue: queue.Queue[NotificationEvent | None] = queue.Queue()
        self._dedupe: dict[tuple[str, str], float] = {}
        self._last_progress: dict[str, float] = {}
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._seen_printers: set[str] = set()
```

- [ ] **Step 5: Wire the helper into `_send_push_to_start`**

In `app/notification_hub.py`, replace the body of `_send_push_to_start` (currently around lines 337-362):

```python
    async def _send_push_to_start(self, event: NotificationEvent) -> None:
        snapshot = event.snapshot
        subscribers = self._store.subscribers_for_printer(event.printer_id)
        existing_device_ids = {
            a.device_id
            for a in self._store.list_activities_for_printer(event.printer_id)
        }
        file_name = snapshot.job.file_name if snapshot.job else ""
        thumbnail_data = await lookup_push_thumbnail(
            self._slice_store, file_name,
        )
        attributes = {
            "printerId": snapshot.id,
            "printerName": snapshot.name,
            "fileName": file_name,
            "thumbnailData": thumbnail_data,
        }
        content = _content_state_from(snapshot)
        for dev in subscribers:
            if dev.id in existing_device_ids:
                continue
            if not dev.live_activity_start_token:
                continue
            result = await self._apns.send_live_activity_start(
                start_token=dev.live_activity_start_token,
                attributes_type="PrintActivityAttributes",
                attributes=attributes,
                content_state=content,
            )
            self._handle_result(result, dev.live_activity_start_token)
```

- [ ] **Step 6: Run the notification hub tests — they should pass**

Run: `.venv/bin/pytest tests/test_notification_hub.py -v`
Expected: all tests PASS, including the two new thumbnail tests.

- [ ] **Step 7: Run the full test suite to catch any breakage**

Run: `.venv/bin/pytest -v`
Expected: every test passes. If any unrelated test fails because it constructed `NotificationHub` directly without `slice_store=`, update it the same way the hub test was updated.

- [ ] **Step 8: Commit**

```bash
git add app/notification_hub.py tests/test_notification_hub.py
git commit -m "$(cat <<'EOF'
Include plate thumbnail in Live Activity push-to-start payloads

- Look up the printing file in the slice-job store at start time and attach a base64 JPEG thumbnail to the immutable
  `attributes.thumbnailData`, so prints initiated outside the iOS app render with a thumbnail in their Live Activity.
- Fall back to `null` (current behavior) when no slice job matches or the thumbnail cannot be compressed under budget — the activity
  still starts, just without artwork.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Wire `SliceJobStore` into `main.py`

**Files:**
- Modify: `app/main.py`

The notification hub now requires a `SliceJobStore`. Today, the slice store is constructed inside the `slicer_client is not None` branch (lines 158-169). It must move earlier so the hub can take it.

- [ ] **Step 1: Read the current lifespan startup**

Run: `.venv/bin/python -c "import ast; src=open('app/main.py').read(); print('ok')"` to confirm syntax baseline.

Open `app/main.py` and review lines 125-185 (the lifespan body around device store / APNs / hub / slicer / printer service construction).

- [ ] **Step 2: Hoist `SliceJobStore` construction**

In `app/main.py`, modify the lifespan startup section. Replace lines 125-184 (the entire block from the `# Device registry + APNs` comment through `await apns_client.aclose()`) with:

```python
    # Device registry + APNs
    device_store_path = config_store._config_path.parent / "devices.json"
    device_store = DeviceStore(device_store_path)

    # Slice-job store is also a thumbnail source for Live Activity pushes, so
    # construct it before the notification hub even when no slicer is wired
    # up — an empty store just yields no thumbnails, which is the correct
    # graceful-degradation behavior.
    slice_store_path = config_store._config_path.parent / "slice_jobs.json"
    slice_store = SliceJobStore(slice_store_path)

    apns_client: ApnsClient | None = None
    notification_hub: NotificationHub | None = None
    status_change_callback = None
    if settings.push_enabled:
        signer = ApnsJwtSigner(
            key_path=settings.apns_key_path,
            key_id=settings.apns_key_id,
            team_id=settings.apns_team_id,
        )
        apns_client = ApnsClient(
            signer=signer,
            bundle_id=settings.apns_bundle_id,
            environment=settings.apns_environment,
        )
        notification_hub = NotificationHub(
            apns=apns_client,
            device_store=device_store,
            slice_store=slice_store,
        )
        notification_hub.start()
        status_change_callback = notification_hub.on_status_change
        logger.info("APNs push enabled")
    else:
        logger.info("APNs push disabled — set APNS_KEY_PATH and related vars to enable")

    printer_service = PrinterService(
        configs, status_change_callback=status_change_callback,
    )
    printer_service.start()
    if settings.orcaslicer_api_url:
        slicer_client = SlicerClient(settings.orcaslicer_api_url)

    if slicer_client is not None:
        slice_jobs = SliceJobManager(
            store=slice_store,
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

    app.state.device_store = device_store
    app.state.notification_hub = notification_hub
    app.state.apns_client = apns_client

    yield
    if slice_jobs is not None:
        await slice_jobs.stop()
    printer_service.stop()
    if notification_hub is not None:
        notification_hub.stop()
    if apns_client is not None:
        await apns_client.aclose()
```

The two semantic changes are:
1. `slice_store = SliceJobStore(...)` is hoisted out of the slicer branch.
2. `NotificationHub(...)` now passes `slice_store=slice_store`.
3. `SliceJobManager(store=...)` reuses the same `slice_store` object instead of constructing a new one.

- [ ] **Step 3: Verify the file still imports cleanly**

Run: `.venv/bin/python -c "import app.main"`
Expected: no exceptions; returns to prompt.

- [ ] **Step 4: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: every test passes.

- [ ] **Step 5: Smoke-test the running app**

Run: `.venv/bin/python -m app &` then wait 2 seconds, then `curl -sS http://127.0.0.1:8000/api/printers | head -c 200`, then `kill %1`.
Expected: the gateway starts without error and the printers endpoint returns JSON. The smoke test confirms the lifespan startup wires everything correctly even when no real printer is reachable.

- [ ] **Step 6: Commit**

```bash
git add app/main.py
git commit -m "$(cat <<'EOF'
Share one SliceJobStore between slicer manager and notification hub

- Hoist `SliceJobStore` out of the slicer branch so the notification hub can read thumbnails from it on every Live Activity start,
  including when no slicer is configured (the hub then sees an empty store and falls back to `null` thumbnails).
- Continue to construct `SliceJobManager` only when an OrcaSlicer URL is configured, but reuse the shared store instance.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review checklist (run after all tasks complete)

- [ ] `git diff main..HEAD --stat` shows only the files listed in **File Structure** are touched.
- [ ] `.venv/bin/pytest -v` reports all tests passing, including the 8 new tests in `tests/test_live_activity_thumbnail.py` and the 2 new tests in `tests/test_notification_hub.py`.
- [ ] Manual sanity: re-read `app/notification_hub.py:_send_push_to_start` and confirm `attributes["thumbnailData"]` is the helper result, not `None`.
- [ ] Manual sanity: re-read `app/main.py` lifespan and confirm only one `SliceJobStore(...)` construction.
- [ ] Confirm no `data:image/jpeg;base64,` prefix is being attached to `thumbnailData` — must be raw base64 to match the iOS local-start path.
