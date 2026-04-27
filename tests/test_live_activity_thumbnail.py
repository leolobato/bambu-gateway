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
