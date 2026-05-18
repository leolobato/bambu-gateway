from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest


@pytest.fixture
async def app_client(tmp_path: Path, monkeypatch):
    from app import config_store
    from app.config import settings
    import app.main as main_mod

    config_store.set_path(tmp_path / "printers.json")
    monkeypatch.setattr(settings, "orcaslicer_api_url", "http://stub")

    class FakeSlicer:
        def __init__(self) -> None:
            self.import_calls = []
            self.layout_calls = []
            self.materialize_calls = []

        async def import_stl_draft(self, data: bytes, **kwargs):
            self.import_calls.append((data, kwargs))
            return {
                "draft_token": "draft1234",
                "source_filename": kwargs["filename"],
                "bed": {"width": 180, "depth": 180, "printable_area": []},
                "objects": [],
                "warnings": [],
                "actions": ["center"],
            }

        async def layout_stl_draft(self, draft_token: str, action: str):
            self.layout_calls.append((draft_token, action))
            return {
                "draft_token": draft_token,
                "source_filename": "part.stl",
                "bed": {"width": 180, "depth": 180, "printable_area": []},
                "objects": [],
                "warnings": [],
                "actions": ["center"],
            }

        async def materialize_stl_draft(self, draft_token: str, **kwargs):
            self.materialize_calls.append((draft_token, kwargs))
            return {
                "draft_token": draft_token,
                "input_token": "tok3mf",
            }

        async def inspect_3mf_token(self, token: str):
            return {
                "plates": [{"id": 1, "name": "", "objects": [], "used_filament_indices": []}],
                "filaments": [],
                "thumbnail_urls": [],
                "print_settings_id": "GP000",
                "printer_settings_id": "GM020",
                "curr_bed_type": "Textured PEI Plate",
            }

    fake = FakeSlicer()
    main_mod.slicer_client = fake
    main_mod.printer_service = MagicMock()

    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, fake, tmp_path


async def test_create_stl_draft_stores_source_and_returns_source_url(app_client):
    client, fake, tmp_path = app_client

    resp = await client.post(
        "/api/stl-drafts",
        files={"file": ("part.stl", b"solid part\nendsolid part\n", "model/stl")},
        data={
            "machine_profile": "GM020",
            "process_profile": "GP000",
            "plate_type": "textured_pei_plate",
            "auto_orient": "true",
            "arrange": "false",
            "center": "true",
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["draft_token"] == "draft1234"
    assert body["source_url"] == "/api/stl-drafts/draft1234/source.stl"
    assert fake.import_calls[0][1]["machine_profile"] == "GM020"
    assert fake.import_calls[0][1]["auto_orient"] is True
    assert (tmp_path / "stl_drafts" / "draft1234.stl").read_bytes().startswith(b"solid")


async def test_get_stl_draft_source_streams_cached_stl(app_client):
    client, _fake, _tmp_path = app_client
    await client.post(
        "/api/stl-drafts",
        files={"file": ("part.stl", b"solid part\nendsolid part\n", "model/stl")},
        data={"machine_profile": "GM020", "process_profile": "GP000"},
    )

    resp = await client.get("/api/stl-drafts/draft1234/source.stl")

    assert resp.status_code == 200
    assert resp.content.startswith(b"solid")
    assert resp.headers["content-type"].startswith("model/stl")


async def test_layout_stl_draft_proxies_action(app_client):
    client, fake, _tmp_path = app_client
    await client.post(
        "/api/stl-drafts",
        files={"file": ("part.stl", b"solid part\nendsolid part\n", "model/stl")},
        data={"machine_profile": "GM020", "process_profile": "GP000"},
    )

    resp = await client.post("/api/stl-drafts/draft1234/layout", json={"action": "center"})

    assert resp.status_code == 200, resp.text
    assert fake.layout_calls == [("draft1234", "center")]
    assert resp.json()["source_url"] == "/api/stl-drafts/draft1234/source.stl"


async def test_materialize_stl_draft_returns_token_and_info_json(app_client):
    client, fake, tmp_path = app_client
    await client.post(
        "/api/stl-drafts",
        files={"file": ("part.stl", b"solid part\nendsolid part\n", "model/stl")},
        data={"machine_profile": "GM020", "process_profile": "GP000"},
    )

    resp = await client.post(
        "/api/stl-drafts/draft1234/3mf",
        json={"thumbnail_png_data_url": "data:image/png;base64,UE5H"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["input_token"] == "tok3mf"
    assert body["filename"] == "part.3mf"
    assert body["info"]["print_profile"]["print_settings_id"] == "GP000"
    assert body["info"]["printer"]["printer_settings_id"] == "GM020"
    assert fake.materialize_calls[-1] == (
        "draft1234",
        {"thumbnail_png_data_url": "data:image/png;base64,UE5H"},
    )
    assert not (tmp_path / "stl_drafts" / "draft1234.stl").exists()


async def test_create_stl_draft_rejects_non_stl(app_client):
    client, _fake, _tmp_path = app_client

    resp = await client.post(
        "/api/stl-drafts",
        files={"file": ("part.3mf", b"x", "application/octet-stream")},
        data={"machine_profile": "GM020", "process_profile": "GP000"},
    )

    assert resp.status_code == 400
    assert "STL" in resp.json()["detail"]


async def test_layout_stl_draft_returns_410_when_source_missing(app_client):
    client, _fake, tmp_path = app_client
    await client.post(
        "/api/stl-drafts",
        files={"file": ("part.stl", b"solid part\nendsolid part\n", "model/stl")},
        data={"machine_profile": "GM020", "process_profile": "GP000"},
    )
    (tmp_path / "stl_drafts" / "draft1234.stl").unlink()

    resp = await client.post("/api/stl-drafts/draft1234/layout", json={"action": "center"})

    assert resp.status_code == 410


async def test_materialize_stl_draft_returns_410_when_source_missing(app_client):
    client, _fake, tmp_path = app_client
    await client.post(
        "/api/stl-drafts",
        files={"file": ("part.stl", b"solid part\nendsolid part\n", "model/stl")},
        data={"machine_profile": "GM020", "process_profile": "GP000"},
    )
    (tmp_path / "stl_drafts" / "draft1234.stl").unlink()

    resp = await client.post("/api/stl-drafts/draft1234/3mf")

    assert resp.status_code == 410


async def test_materialize_stl_draft_deletes_cached_source(app_client):
    client, _fake, tmp_path = app_client
    await client.post(
        "/api/stl-drafts",
        files={"file": ("part.stl", b"solid part\nendsolid part\n", "model/stl")},
        data={"machine_profile": "GM020", "process_profile": "GP000"},
    )
    source = tmp_path / "stl_drafts" / "draft1234.stl"
    assert source.exists()

    resp = await client.post("/api/stl-drafts/draft1234/3mf")

    assert resp.status_code == 200
    assert not source.exists()


def test_sweep_stl_drafts_deletes_stale_files(tmp_path, monkeypatch):
    import os
    import time as time_mod
    from app import config_store
    import app.main as main_mod

    config_store.set_path(tmp_path / "printers.json")

    drafts_dir = tmp_path / "stl_drafts"
    drafts_dir.mkdir()
    fresh = drafts_dir / "fresh1234.stl"
    stale = drafts_dir / "stale5678.stl"
    fresh.write_bytes(b"new")
    stale.write_bytes(b"old")

    old = time_mod.time() - main_mod._STL_DRAFT_MAX_AGE_SECONDS - 60
    os.utime(stale, (old, old))

    main_mod._sweep_stl_drafts()

    assert fresh.exists()
    assert not stale.exists()
