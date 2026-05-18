from __future__ import annotations

import json

import httpx
import pytest

from app.slicer_client import SlicerClient, SlicingError


@pytest.mark.asyncio
async def test_inspect_3mf_token_gets_existing_token():
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/3mf/tok3mf/inspect"
        return httpx.Response(200, json={"plates": [], "filaments": [], "thumbnail_urls": []})

    client = SlicerClient("http://slicer", transport=httpx.MockTransport(_handler))

    assert await client.inspect_3mf_token("tok3mf") == {
        "plates": [],
        "filaments": [],
        "thumbnail_urls": [],
    }


@pytest.mark.asyncio
async def test_prepare_3mf_token_posts_final_settings_and_downloads_prepared_bytes():
    requests: list[tuple[str, str]] = []
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/3mf/tok3mf/prepare":
            captured["body"] = json.loads(request.content.decode())
            return httpx.Response(200, json={"input_token": "preparedtok"})
        if request.method == "GET" and request.url.path == "/3mf/preparedtok":
            return httpx.Response(200, content=b"prepared-3mf")
        return httpx.Response(404)

    client = SlicerClient("http://slicer", transport=httpx.MockTransport(_handler))

    out = await client.prepare_3mf_token(
        "tok3mf",
        machine_profile="GM020",
        process_profile="GP000",
        plate_type="textured_pei_plate",
        process_overrides={"layer_height": "0.16"},
        thumbnail_png_data_url="data:image/png;base64,UE5H",
    )

    assert captured["body"] == {
        "machine_id": "GM020",
        "process_id": "GP000",
        "plate_type": "textured_pei_plate",
        "process_overrides": {"layer_height": "0.16"},
        "thumbnail_png_base64": "UE5H",
    }
    assert out == {"input_token": "preparedtok", "content": b"prepared-3mf"}
    assert requests == [("POST", "/3mf/tok3mf/prepare"), ("GET", "/3mf/preparedtok")]


@pytest.mark.asyncio
async def test_prepare_3mf_token_requires_input_token():
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = SlicerClient("http://slicer", transport=httpx.MockTransport(_handler))

    with pytest.raises(SlicingError) as exc:
        await client.prepare_3mf_token(
            "tok3mf",
            machine_profile="GM020",
            process_profile="GP000",
        )

    assert "prepare response did not include input_token" in str(exc.value)
