from __future__ import annotations

import json

import httpx
import pytest

from app.slicer_client import SlicerClient, SlicingError


@pytest.mark.asyncio
async def test_import_stl_draft_posts_multipart_contract():
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        body = request.content
        captured["body"] = body
        assert b'name="file"; filename="part.stl"' in body
        assert b'name="machine_id"' in body and b"GM020" in body
        assert b'name="process_id"' in body and b"GP000" in body
        assert b'name="auto_orient"' in body and b"true" in body
        return httpx.Response(
            200,
            json={
                "draft_token": "draft1",
                "source_filename": "part.stl",
                "bed": {"width": 180, "depth": 180, "printable_area": []},
                "objects": [],
                "warnings": [],
                "actions": ["center"],
            },
        )

    client = SlicerClient("http://slicer", transport=httpx.MockTransport(_handler))

    scene = await client.import_stl_draft(
        b"solid part\nendsolid part\n",
        filename="part.stl",
        machine_profile="GM020",
        process_profile="GP000",
        plate_type="textured_pei_plate",
        auto_orient=True,
        arrange=False,
        center=True,
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/stl/import"
    assert scene["draft_token"] == "draft1"


@pytest.mark.asyncio
async def test_layout_stl_draft_posts_action_json():
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "draft_token": "draft1",
                "source_filename": "part.stl",
                "bed": {"width": 180, "depth": 180, "printable_area": []},
                "objects": [],
                "warnings": [],
                "actions": ["center"],
            },
        )

    client = SlicerClient("http://slicer", transport=httpx.MockTransport(_handler))

    scene = await client.layout_stl_draft("draft1", "center")

    assert captured == {
        "method": "POST",
        "path": "/stl/draft1/layout",
        "body": {"action": "center"},
    }
    assert scene["draft_token"] == "draft1"


@pytest.mark.asyncio
async def test_materialize_stl_draft_downloads_returned_3mf_token():
    paths: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.method == "POST" and request.url.path == "/stl/draft1/3mf":
            return httpx.Response(200, json={"input_token": "tok3mf", "draft_token": "draft1"})
        if request.method == "GET" and request.url.path == "/3mf/tok3mf":
            return httpx.Response(200, content=b"3mf-bytes")
        return httpx.Response(404)

    client = SlicerClient("http://slicer", transport=httpx.MockTransport(_handler))

    materialized = await client.materialize_stl_draft("draft1")

    assert paths == ["/stl/draft1/3mf", "/3mf/tok3mf"]
    assert materialized["input_token"] == "tok3mf"
    assert materialized["content"] == b"3mf-bytes"


@pytest.mark.asyncio
async def test_import_stl_draft_raises_slicing_error_on_non_200():
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"code": "arrange_failed", "message": "does not fit"})

    client = SlicerClient("http://slicer", transport=httpx.MockTransport(_handler))

    with pytest.raises(SlicingError) as exc:
        await client.import_stl_draft(
            b"x",
            filename="part.stl",
            machine_profile="GM020",
            process_profile="GP000",
        )
    assert "arrange_failed" in str(exc.value)
