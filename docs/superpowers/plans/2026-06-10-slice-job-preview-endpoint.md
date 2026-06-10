# Slice Job Preview Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `GET /api/slice-jobs/{job_id}/preview` returns the raw `Metadata/preview.bin` bytes from a ready job's sliced 3MF, so iOS can render previews without downloading the whole 3MF.

**Architecture:** One new FastAPI route in `app/main.py`, mirroring the existing `/api/slice-jobs/{job_id}/output` route's job-resolution and error semantics, plus a zip extraction using the repo's `zipfile` idiom (`_extract_plate_thumbnail` in `app/slice_jobs.py:292`). A `GCPV` magic check guards against malformed producer output.

**Tech Stack:** Python / FastAPI, stdlib `zipfile`, pytest (asyncio_mode=auto) with the existing `app_client` fixture pattern from `tests/test_slice_jobs_api.py`.

**Spec:** `../GCodePreview/docs/superpowers/specs/2026-06-10-preview-bin-pipeline-rollout-design.md` (Stage 2). Note: per repo convention, tests synthesize the 3MF in-memory rather than checking in a binary fixture file.

---

### Task 0: Branch

- [ ] **Step 1: Create the branch from main**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
git checkout main
git checkout -b feat/gcode-preview-v2
```

---

### Task 1: Endpoint tests (failing first)

**Files:**
- Create: `tests/test_slice_preview.py`

- [ ] **Step 1: Write the test module**

Reuse the `app_client` fixture by importing it from the existing module (pytest fixtures don't import across files without conftest; the simplest repo-consistent approach is to copy the fixture reference via `from tests.test_slice_jobs_api import app_client  # noqa: F401` — if that import trips on collection, move the fixture into `tests/conftest.py` instead and update both files).

```python
"""Tests for GET /api/slice-jobs/{job_id}/preview."""
import io
import zipfile
from pathlib import Path

from tests.test_slice_jobs_api import app_client  # noqa: F401


def _make_3mf(entries: dict[str, bytes]) -> bytes:
    """Minimal zip standing in for a sliced 3MF."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return out.getvalue()


# 4-byte root offset + 'GCPV' file identifier, as FlatBuffers lays it out.
_VALID_PREVIEW = b"\x10\x00\x00\x00GCPV" + b"\x00" * 32


async def _seed_ready_job(job_id: str, threemf: bytes | None) -> None:
    """Insert a ready job; when `threemf` is None, output_path points nowhere."""
    import app.main as main_mod
    from app.slice_jobs import SliceJob, SliceJobStatus

    store = main_mod.slice_jobs._store
    seed = SliceJob.new(
        filename="x.3mf", machine_profile="GM014", process_profile="0.20mm",
        filament_profiles={}, plate_id=1, plate_type="",
        project_filament_count=0, printer_id=None, auto_print=False,
        input_path=store.input_path(job_id),
    )
    seed.id = job_id
    Path(seed.input_path).parent.mkdir(parents=True, exist_ok=True)
    Path(seed.input_path).write_bytes(b"x")
    seed.status = SliceJobStatus.READY
    out_path = store.output_path(job_id)
    if threemf is not None:
        out_path.write_bytes(threemf)
    seed.output_path = str(out_path)
    await store.upsert(seed)


async def test_preview_returns_bytes_and_headers(app_client):
    await _seed_ready_job(
        "pvjob1", _make_3mf({"Metadata/preview.bin": _VALID_PREVIEW,
                             "Metadata/plate_1.gcode": b"G1"}),
    )
    resp = await app_client.get("/api/slice-jobs/pvjob1/preview")
    assert resp.status_code == 200
    assert resp.content == _VALID_PREVIEW
    assert resp.headers["content-type"] == "application/octet-stream"
    assert resp.headers["x-preview-format-version"] == "1"
    assert "immutable" in resp.headers["cache-control"]


async def test_preview_unknown_job_is_404(app_client):
    resp = await app_client.get("/api/slice-jobs/nope/preview")
    assert resp.status_code == 404


async def test_preview_job_not_ready_is_409(app_client):
    import app.main as main_mod
    from app.slice_jobs import SliceJob

    store = main_mod.slice_jobs._store
    seed = SliceJob.new(
        filename="x.3mf", machine_profile="GM014", process_profile="0.20mm",
        filament_profiles={}, plate_id=1, plate_type="",
        project_filament_count=0, printer_id=None, auto_print=False,
        input_path=store.input_path("pvjob2"),
    )
    seed.id = "pvjob2"
    Path(seed.input_path).parent.mkdir(parents=True, exist_ok=True)
    Path(seed.input_path).write_bytes(b"x")
    await store.upsert(seed)  # status stays QUEUED

    resp = await app_client.get("/api/slice-jobs/pvjob2/preview")
    assert resp.status_code == 409


async def test_preview_missing_output_blob_is_410(app_client):
    await _seed_ready_job("pvjob3", threemf=None)
    resp = await app_client.get("/api/slice-jobs/pvjob3/preview")
    assert resp.status_code == 410


async def test_preview_entry_absent_is_404(app_client):
    await _seed_ready_job(
        "pvjob4", _make_3mf({"Metadata/plate_1.gcode": b"G1"}),
    )
    resp = await app_client.get("/api/slice-jobs/pvjob4/preview")
    assert resp.status_code == 404
    assert "preview" in resp.json()["detail"].lower()


async def test_preview_bad_magic_is_502(app_client):
    await _seed_ready_job(
        "pvjob5", _make_3mf({"Metadata/preview.bin": b"\x10\x00\x00\x00XXXX" + b"\x00" * 32}),
    )
    resp = await app_client.get("/api/slice-jobs/pvjob5/preview")
    assert resp.status_code == 502
```

- [ ] **Step 2: Run them to verify they fail for the right reason**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
pytest tests/test_slice_preview.py -v
```

Expected: FAIL — the 200/409/410/502 tests get 404/405 responses (route doesn't exist yet). If collection fails on the cross-module fixture import, move `app_client` to `tests/conftest.py` first (mechanical move, keep `test_slice_jobs_api.py` green), then re-run.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_slice_preview.py
git commit -m "test: cover GET /api/slice-jobs/{job_id}/preview"
```

---

### Task 2: Implement the route

**Files:**
- Modify: `app/main.py` (add `import zipfile` to the stdlib imports — it is not imported today; add the route next to `get_slice_job_output`, ~line 2599)

- [ ] **Step 1: Add the route**

```python
@app.get("/api/slice-jobs/{job_id}/preview")
async def get_slice_job_preview(job_id: str):
    """Return the sliced 3MF's embedded ``Metadata/preview.bin``.

    The blob is a FlatBuffers ``PreviewData`` (magic ``GCPV``) produced by
    orcaslicer-headless at slice time and rendered by the iOS GCodePreview
    v2 package. Artifacts are immutable per job id, hence the aggressive
    cache headers.
    """
    if slice_jobs is None:
        raise HTTPException(status_code=404, detail="Slice jobs disabled")
    job = await slice_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status.value != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Job is {job.status.value}, no output available",
        )
    if not job.output_path or not Path(job.output_path).exists():
        raise HTTPException(status_code=410, detail="Output blob is gone")

    try:
        with zipfile.ZipFile(job.output_path) as zf:
            try:
                data = zf.read("Metadata/preview.bin")
            except KeyError:
                raise HTTPException(
                    status_code=404,
                    detail="Preview data not present in this 3MF "
                           "(sliced by an older orcaslicer-headless?)",
                )
    except zipfile.BadZipFile:
        raise HTTPException(status_code=502, detail="Sliced 3MF is unreadable")

    # FlatBuffers file identifier sits at bytes 4-7 (0-3 are the root offset).
    if len(data) < 8 or data[4:8] != b"GCPV":
        raise HTTPException(status_code=502, detail="Malformed preview data")

    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "X-Preview-Format-Version": "1",
            "X-Job-Id": job.id,
            "Cache-Control": "public, max-age=86400, immutable",
        },
    )
```

- [ ] **Step 2: Run the new tests**

```bash
pytest tests/test_slice_preview.py -v
```

Expected: 6 passed.

- [ ] **Step 3: Run the full suite**

```bash
pytest -q
```

Expected: PASS, no regressions (esp. `tests/test_slice_jobs_api.py` if the fixture moved to conftest).

- [ ] **Step 4: Commit**

```bash
git add app/main.py tests/
git commit -m "feat: serve Metadata/preview.bin per slice job"
```

---

### Task 3: Manual curl verification (needs Stage 1 deployed locally)

Run only once orcaslicer-headless embeds preview.bin (Stage 1 of the rollout plan); until then the endpoint correctly 404s with "Preview data not present".

- [ ] **Step 1: Start the gateway locally** (per repo README/Makefile — dev server against the local orcaslicer-headless at :8070), slice something via `POST /api/slice-jobs`, wait for `ready`, then:

```bash
curl -sD - -o /tmp/job.preview.bin http://localhost:8000/api/slice-jobs/<job_id>/preview
xxd -l 8 /tmp/job.preview.bin
```

Expected: `200`, `x-preview-format-version: 1`, and bytes 4–7 = `47 43 50 56` (`GCPV`).

---

### Done criteria (Stage 2 of the rollout spec)

- All six endpoint tests green; full pytest suite green.
- curl against a locally sliced job returns the blob with correct headers (once Stage 1 is in place).
- No deploy to 10.0.1.9 without explicit user approval.
