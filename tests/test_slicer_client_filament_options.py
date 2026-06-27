import httpx
import pytest

from app.slicer_client import SlicerClient, SlicingError


def _client(handler):
    return SlicerClient("http://slicer", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_get_filament_options_returns_payload():
    def handler(request):
        assert request.url.path == "/options/filament"
        return httpx.Response(200, json={"version": "2.3.2-41", "options": {}})

    result = await _client(handler).get_filament_options()
    assert result["version"] == "2.3.2-41"


@pytest.mark.asyncio
async def test_get_filament_layout_returns_payload():
    def handler(request):
        assert request.url.path == "/options/filament/layout"
        return httpx.Response(200, json={"version": "v", "pages": [{"label": "Filament", "optgroups": []}]})

    result = await _client(handler).get_filament_layout()
    assert result["pages"][0]["label"] == "Filament"


@pytest.mark.asyncio
async def test_get_filament_options_raises_on_500():
    def handler(request):
        return httpx.Response(500, text="boom")

    with pytest.raises(SlicingError):
        await _client(handler).get_filament_options()
