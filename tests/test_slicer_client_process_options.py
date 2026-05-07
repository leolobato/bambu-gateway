"""Unit tests for SlicerClient.get_process_options / get_process_layout."""
from __future__ import annotations

import json

import httpx
import pytest

from app.slicer_client import SlicerClient, SlicingError


def _stub_transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_get_process_options_pass_through():
    catalogue = {"version": "2.3.2-41", "options": {"layer_height": {"key": "layer_height"}}}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/options/process"
        return httpx.Response(200, json=catalogue)

    client = SlicerClient("http://slicer", transport=_stub_transport(handler))
    out = await client.get_process_options()
    assert out == catalogue


@pytest.mark.asyncio
async def test_get_process_layout_pass_through():
    layout = {
        "version": "2.3.2-41",
        "allowlist_revision": "2026-05-06.1",
        "pages": [{"label": "Quality", "optgroups": []}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/options/process/layout"
        return httpx.Response(200, json=layout)

    client = SlicerClient("http://slicer", transport=_stub_transport(handler))
    out = await client.get_process_layout()
    assert out == layout


@pytest.mark.asyncio
async def test_get_process_options_raises_on_503():
    """503 options_not_loaded must surface as SlicingError so the route can forward it."""
    body = {"code": "options_not_loaded", "detail": "options cache failed to populate"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json=body)

    client = SlicerClient("http://slicer", transport=_stub_transport(handler))
    with pytest.raises(SlicingError) as exc:
        await client.get_process_options()
    assert "503" in str(exc.value)


@pytest.mark.asyncio
async def test_get_process_options_raises_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    client = SlicerClient("http://slicer", transport=_stub_transport(handler))
    with pytest.raises(SlicingError):
        await client.get_process_options()
