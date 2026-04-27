"""Unit tests for the Live Activity thumbnail helpers."""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from app.live_activity_thumbnail import _compress_for_push, lookup_push_thumbnail
from app.slice_jobs import SliceJob, SliceJobStore


_PUSH_BUDGET_BYTES = 2400


def _make_png_data_url(size: tuple[int, int] = (512, 512)) -> str:
    """Build a realistic plate-thumbnail-style PNG data URL.

    Real Bambu slicer plate thumbnails are mostly transparent with a small
    coloured object in the centre. Match that shape so the test exercises
    the same compressibility envelope as production input.
    """
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    cx, cy = size[0] // 2, size[1] // 2
    radius = min(size) // 6
    for x in range(cx - radius, cx + radius):
        for y in range(cy - radius, cy + radius):
            dx = x - cx
            dy = y - cy
            if dx * dx + dy * dy <= radius * radius:
                # Slight gradient so the encoder has real bytes to compress.
                shade = 80 + ((dx + dy) % 96)
                img.putpixel((x, y), (shade, 60, 40, 255))
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
    decoded = Image.open(io.BytesIO(base64.b64decode(out)))
    assert decoded.mode == "RGB"


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
