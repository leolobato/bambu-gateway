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
# Real plate thumbnails (sparse content on transparent background) typically
# fit on the first rung.  Pathologically dense images fall through to the
# smaller/lower-quality rungs at the bottom of the ladder.
_COMPRESSION_LADDER: tuple[tuple[int, int], ...] = (
    (192, 60),
    (192, 40),
    (192, 25),
    (128, 40),
    (128, 25),
    (96, 40),
    (96, 25),
    (64, 40),
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
