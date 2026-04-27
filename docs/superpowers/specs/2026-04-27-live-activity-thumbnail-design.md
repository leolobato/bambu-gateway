# Live Activity push-to-start: include plate thumbnail

## Background

When a print starts, the gateway pushes an APNs Live Activity `start` event so
subscribed iPhones display an ongoing-print activity even if the print was
initiated outside the iOS app (Bambu Studio, Bambu Handy, the printer's screen).

Today, the start payload's `attributes` block carries `thumbnailData: null`
(`app/notification_hub.py:344-349`). The iOS app's local-start path
(`handlePrintResponse` calling `Activity.request` directly) populates the
thumbnail itself, so users who initiate prints from the iOS app see a thumbnail
in the Live Activity. Prints started elsewhere take the gateway-pushed path and
land with no thumbnail.

`PrintActivityAttributes.thumbnailData` is part of the immutable `attributes`
struct on iOS — it can only be set at start time and cannot be changed via
update events.

## Goal

Populate `attributes.thumbnailData` in the gateway-pushed Live Activity `start`
payload with a base64-encoded JPEG sized to fit the iOS team's per-thumbnail
budget of ≤2.5 KB (Apple's overall APNs payload limit is 4 KB).

## Non-goals

- Fetching `.gcode.3mf` from the printer over FTP when the gateway has no
  cached copy. Out of scope; deferred until we see real-world misses.
- Updating thumbnails after start. iOS `attributes` are immutable per-activity,
  so this is structurally impossible.
- Pre-compressing the push-sized JPEG at slice time and persisting it on
  `SliceJob`. Will add only if profiling shows compression cost matters.
- Cleanup or eviction policy for slice-job thumbnails. They live with the
  slice job and follow its lifecycle.

## Architecture

### New module: `app/live_activity_thumbnail.py`

A single helper:

```python
async def lookup_push_thumbnail(
    slice_store: SliceJobStore, file_name: str,
) -> str | None:
    """Return a base64-encoded JPEG of a slice-job thumbnail matching
    ``file_name``, sized to fit the Live Activity push budget. Returns
    None if no slice job matches, the matched job has no thumbnail, or
    the thumbnail cannot be compressed under budget."""
```

The returned string is **raw base64** (no `data:image/jpeg;base64,` prefix),
matching the encoding the iOS local-start path produces for
`PrintActivityAttributes.thumbnailData`.

### Wiring

`NotificationHub.__init__` gains a `slice_store: SliceJobStore` parameter.
The lifespan in `app/main.py` originally constructed `SliceJobStore` *inside*
the `slicer_client is not None` branch, so the hub couldn't reach it. The
fix is to hoist the store construction to before the hub is built and reuse
the same instance in `SliceJobManager`. Two consumers, one store, one
`asyncio.Lock`, one in-memory cache. When no slicer is configured the store
is still constructed (cheap — only `mkdir`) and stays empty, which makes
the hub's lookup gracefully return `None` thumbnails.

### Call site

`_send_push_to_start` in `app/notification_hub.py` calls the helper:

```python
thumbnail_data = await lookup_push_thumbnail(
    self._slice_store, snapshot.job.file_name if snapshot.job else "",
)
attributes = {
    "printerId": snapshot.id,
    "printerName": snapshot.name,
    "fileName": snapshot.job.file_name if snapshot.job else "",
    "thumbnailData": thumbnail_data,
}
```

Everything else in `_send_push_to_start` is unchanged.

## Lookup logic

The MQTT field `subtask_name` becomes `PrinterStatus.job.file_name`. Bambu
firmware sometimes drops or includes the `.gcode.3mf` suffix, so both sides are
normalized before comparison.

```
def _normalize(name: str) -> str:
    name = name.lower()
    for suffix in (".gcode.3mf", ".3mf"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name
```

Algorithm:

1. `jobs = await slice_store.list_all()`
2. Keep jobs where `_normalize(job.filename) == _normalize(file_name)` and
   `job.thumbnail` is non-empty.
3. Sort the matches by `updated_at` descending; pick the first.
4. Compress its `thumbnail` and return the result.
5. If steps 2 or 3 yield nothing, or compression fails, return `None`.

`SliceJobStore.list_all` is a small in-memory dict load behind a lock. No
secondary index is needed — print starts are infrequent and the job count is
bounded by the user's slice history.

## Image compression

### Dependency

Add `Pillow` to `requirements.txt`. Pin to a current stable
release (latest 11.x at design time).

### Pipeline

`SliceJob.thumbnail` is a `data:image/png;base64,...` data URL of the slicer's
plate-1 PNG (full resolution, often 100s of KB). To fit ≤2.5 KB encoded:

1. Strip the `data:image/png;base64,` prefix and base64-decode.
2. `PIL.Image.open(io.BytesIO(...))`, then `convert("RGB")` to drop alpha
   (Live Activity thumbnails do not need transparency, and JPEG cannot encode
   alpha anyway).
3. `image.thumbnail((192, 192))` to bound the longest dimension while
   preserving aspect ratio.
4. Save as JPEG at quality 60 to a `BytesIO`.
5. Base64-encode the JPEG bytes and measure length. If `len(b64) <= 2400`
   (a small safety margin below 2500), return.
6. If over budget, retry the ladder:
   - quality 40
   - quality 25
   - resize to `(128, 128)` at quality 40
7. If still over budget after the full ladder, return `None`.

The 2400-byte cap leaves ~100 bytes of headroom for the rest of `attributes`
(printerId, printerName, fileName, JSON structural overhead) plus the
`content-state` block and APNs envelope, all of which must collectively fit in
4 KB.

### Sync vs. async

Pillow operations are synchronous CPU work. They run on the
`NotificationHub`'s asyncio event loop (which already lives on a dedicated
background thread, not the FastAPI request loop). One JPEG re-encode per print
start is far below the threshold that would warrant `run_in_executor`.

## Error handling

The helper catches every failure mode and returns `None`. Specifically:

- No matching slice job → `None`
- Matched job's `thumbnail` is `None` or empty → `None`
- Base64 decode raises → `None`, log `warning`
- Pillow open or save raises → `None`, log `warning`
- Compression ladder exhausts without fitting → `None`, log `warning` with the
  final byte count

The Live Activity start payload still goes out with `thumbnailData: null`,
matching the current behavior. The Live Activity itself is unaffected.

## Tests

### `tests/test_live_activity_thumbnail.py` (new)

Unit tests for `lookup_push_thumbnail`. Use a small in-memory `SliceJobStore`
backed by a temp dir, populated with synthetic `SliceJob` objects.

Cases:

1. Exact-filename match returns a base64 string of length ≤ 2400.
2. Match works when slice-job filename has `.gcode.3mf` and search key does
   not (and vice versa).
3. Case-insensitive match.
4. Multiple matching jobs → the one with the latest `updated_at` is chosen.
5. No matching job → `None`.
6. Match exists but `thumbnail` is `None` → `None`.
7. Malformed base64 in `thumbnail` → `None`, no exception.
8. Pathological input (an image that cannot fit in budget at any rung — e.g.
   a synthetic high-entropy 4096×4096) → `None`.

A test fixture supplies a representative sliced-3mf-style plate PNG. Generate
it programmatically with Pillow rather than committing a binary so the test is
self-contained.

### `tests/test_notification_hub.py` (extend)

Add cases asserting that:

1. When a slice job's filename matches the printing file's `subtask_name`,
   the captured APNs `start` payload's `attributes.thumbnailData` is a
   non-empty base64 string.
2. When no slice job matches, `attributes.thumbnailData` is `None`.

Use the existing fake APNs / device-store fixtures in `tests/conftest.py`.

## Files touched

- `app/live_activity_thumbnail.py` — new
- `app/notification_hub.py` — inject `slice_store`, call helper in
  `_send_push_to_start`
- `app/main.py` — pass `slice_store` when constructing `NotificationHub`
- `requirements.txt` — add `Pillow`
- `tests/test_live_activity_thumbnail.py` — new
- `tests/test_notification_hub.py` — extend
- `tests/conftest.py` — extend if a `slice_store` fixture is needed for the
  hub tests
