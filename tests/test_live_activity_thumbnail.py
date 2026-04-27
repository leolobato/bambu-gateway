"""Unit tests for the Live Activity thumbnail helpers."""

from __future__ import annotations

import base64
import io

from PIL import Image

from app.live_activity_thumbnail import _compress_for_push


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
