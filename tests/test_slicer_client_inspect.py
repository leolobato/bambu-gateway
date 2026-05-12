"""Unit tests for the inspect surface of SlicerClient.

Uses ``httpx.MockTransport`` so the tests run without a live
``orcaslicer-headless`` container.
"""
from __future__ import annotations

import httpx
import pytest

from app.slicer_client import SlicerClient, SlicingError


def _mock_transport(handlers: dict[tuple[str, str], httpx.Response]):
    def _handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key not in handlers:
            return httpx.Response(404, json={"code": "unmocked", "key": list(key)})
        return handlers[key]
    return httpx.MockTransport(_handler)


async def test_upload_inspect_delete_roundtrip():
    transport = _mock_transport({
        ("POST", "/3mf"): httpx.Response(
            200, json={"token": "tok-abc", "sha256": "deadbeef", "size": 100, "evicts": []},
        ),
        ("GET", "/3mf/tok-abc/inspect"): httpx.Response(
            200, json={
                "schema_version": 2,
                "is_sliced": False,
                "plate_count": 1,
                "plates": [{"id": 1, "name": "", "used_filament_indices": [0]}],
                "filaments": [],
                "estimate": None,
                "bbox": None,
                "printer_model": "",
                "printer_variant": "",
                "curr_bed_type": "",
                "thumbnail_urls": [],
                "use_set_per_plate": {"1": [0]},
            },
        ),
        ("DELETE", "/3mf/tok-abc"): httpx.Response(204),
    })

    client = SlicerClient("http://test", transport=transport)
    upload = await client.upload_3mf(b"\x50\x4b\x03\x04dummy")
    assert upload["token"] == "tok-abc"

    insp = await client.inspect("tok-abc")
    assert insp["plate_count"] == 1
    assert insp["plates"][0]["used_filament_indices"] == [0]

    deleted = await client.delete_token("tok-abc")
    assert deleted is True


async def test_delete_token_returns_false_on_404():
    transport = _mock_transport({
        ("DELETE", "/3mf/tok-gone"): httpx.Response(404, json={"code": "token_unknown"}),
    })
    client = SlicerClient("http://test", transport=transport)
    assert await client.delete_token("tok-gone") is False


async def test_inspect_propagates_http_errors():
    transport = _mock_transport({
        ("GET", "/3mf/tok/inspect"): httpx.Response(500, json={"code": "boom"}),
    })
    client = SlicerClient("http://test", transport=transport)
    # raise_for_status() is called outside the try/except in inspect(), so a
    # 500 response surfaces as httpx.HTTPStatusError (not wrapped in SlicingError).
    with pytest.raises(httpx.HTTPStatusError):
        await client.inspect("tok")
