"""Live HTTP smoke test for the options pass-through endpoints.

Skipped when ``$ORCASLICER_API_URL`` isn't reachable.

NOTE: The slicer's /options/process and /options/process/layout endpoints
return 503 ``options_not_loaded`` when the options metadata cache is empty
(a valid startup state). ``SlicerClient`` raises ``SlicingError`` on non-200
responses. When the slicer is running but the options cache hasn't been
populated yet, the tests are marked xfail with the 503 reason so they remain
visible in the output (not silently skipped).
"""
from __future__ import annotations

import os

import httpx
import pytest

from app.slicer_client import SlicerClient, SlicingError

API = os.environ.get("ORCASLICER_API_URL", "http://localhost:8000")


def _reachable() -> bool:
    try:
        return httpx.get(f"{API}/health", timeout=2.0).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(),
    reason=f"orcaslicer-cli unreachable at {API}",
)


@pytest.mark.asyncio
async def test_get_process_options_returns_catalogue():
    client = SlicerClient(API)
    try:
        payload = await client.get_process_options()
    except SlicingError as e:
        msg = str(e)
        if "503" in msg and "options_not_loaded" in msg:
            pytest.xfail(
                "Slicer options cache not populated (503 options_not_loaded); "
                "this is a known startup-state divergence from the spec"
            )
        raise

    assert "version" in payload
    assert payload["version"].startswith("2.")
    options = payload.get("options")
    assert isinstance(options, dict)
    # Spec says ~609 entries; assert a generous lower bound.
    assert len(options) >= 400, f"only got {len(options)} options"

    # Spot-check a known option.
    layer_height = options.get("layer_height")
    assert layer_height is not None
    for required in ("key", "label", "category", "type", "default"):
        assert required in layer_height


@pytest.mark.asyncio
async def test_get_process_layout_returns_pages():
    client = SlicerClient(API)
    try:
        payload = await client.get_process_layout()
    except SlicingError as e:
        msg = str(e)
        if "503" in msg and "options_layout_not_loaded" in msg:
            pytest.xfail(
                "Slicer options layout cache not populated (503 options_layout_not_loaded); "
                "this is a known startup-state divergence from the spec"
            )
        raise

    assert "version" in payload
    assert "allowlist_revision" in payload
    pages = payload.get("pages")
    assert isinstance(pages, list)
    assert len(pages) >= 1

    page = pages[0]
    assert "label" in page
    assert "optgroups" in page
    assert isinstance(page["optgroups"], list)
