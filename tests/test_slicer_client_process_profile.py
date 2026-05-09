"""Unit tests for SlicerClient.get_process_profile."""
from __future__ import annotations

import httpx
import pytest

from app.slicer_client import SlicerClient, SlicingError


def _stub_transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_get_process_profile_returns_flat_dict():
    payload = {"layer_height": "0.16", "sparse_infill_density": "20%"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/profiles/processes/abc-123"
        return httpx.Response(200, json=payload)

    client = SlicerClient("http://slicer", transport=_stub_transport(handler))
    result = await client.get_process_profile("abc-123")
    assert result == payload


@pytest.mark.asyncio
async def test_get_process_profile_url_encodes_setting_id():
    """Setting IDs with spaces or special chars must be URL-encoded."""
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(200, json={})

    client = SlicerClient("http://slicer", transport=_stub_transport(handler))
    await client.get_process_profile("0.16mm Standard @P1P")
    assert len(captured) == 1
    assert "0.16mm%20Standard%20%40P1P" in captured[0]


@pytest.mark.asyncio
async def test_get_process_profile_raises_on_404():
    """404 from the slicer must surface as SlicingError so the route can forward it."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "profile not found"})

    client = SlicerClient("http://slicer", transport=_stub_transport(handler))
    with pytest.raises(SlicingError) as exc:
        await client.get_process_profile("nonexistent")
    assert "404" in str(exc.value)


@pytest.mark.asyncio
async def test_get_process_profile_raises_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    client = SlicerClient("http://slicer", transport=_stub_transport(handler))
    with pytest.raises(SlicingError):
        await client.get_process_profile("abc-123")
