"""Tests for GET /api/printers/{id}/current-job/file."""

from __future__ import annotations

import httpx
import pytest

from app import main as main_mod


def _make_stub_service(*, project_file_payload=None):
    """Build a minimal printer_service stub for these tests."""
    class _Client:
        def __init__(self, host="10.0.0.5", access_code="x"):
            self.host = host
            self.access_code = access_code
            self.latest_project_file_payload = project_file_payload

    class _Service:
        def get_client(self, pid):
            return _Client() if pid == "S1" else None
        def default_printer_id(self):
            return "S1"

    return _Service()


@pytest.mark.asyncio
async def test_404_when_no_cached_project_file(monkeypatch):
    monkeypatch.setattr(main_mod, "printer_service", _make_stub_service(project_file_payload=None))
    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/printers/S1/current-job/file")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ftp_url_triggers_ftps_download(monkeypatch):
    payload = {"command": "project_file", "url": "file:///cache/model.3mf", "task_id": "T1"}
    monkeypatch.setattr(main_mod, "printer_service", _make_stub_service(project_file_payload=payload))

    captured = {}
    def _fake_download(*, host, access_code, remote_path, port=990):
        captured["host"] = host
        captured["remote_path"] = remote_path
        return b"FAKE3MFBYTES"
    monkeypatch.setattr(main_mod, "ftp_download_file", _fake_download)

    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/printers/S1/current-job/file")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert resp.content == b"FAKE3MFBYTES"
    assert captured["remote_path"] == "/cache/model.3mf"


@pytest.mark.asyncio
async def test_http_url_passes_through(monkeypatch):
    payload = {"command": "project_file", "url": "https://example.com/m.3mf", "task_id": "T1"}
    monkeypatch.setattr(main_mod, "printer_service", _make_stub_service(project_file_payload=payload))

    class _FakeAsyncClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, follow_redirects=True):
            return httpx.Response(200, content=b"HTTP3MF", request=httpx.Request("GET", url))

    monkeypatch.setattr(main_mod, "httpx_AsyncClient", _FakeAsyncClient)

    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/printers/S1/current-job/file")

    assert resp.status_code == 200
    assert resp.content == b"HTTP3MF"


@pytest.mark.asyncio
async def test_task_id_mismatch_returns_409(monkeypatch):
    payload = {"command": "project_file", "url": "file:///cache/x.3mf", "task_id": "T1"}
    monkeypatch.setattr(main_mod, "printer_service", _make_stub_service(project_file_payload=payload))
    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/printers/S1/current-job/file?task_id=OTHER")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_404_after_finish_clears_cache(monkeypatch):
    """Once the project_file cache is None (e.g. after FINISH), endpoint 404s."""
    monkeypatch.setattr(
        main_mod, "printer_service",
        _make_stub_service(project_file_payload=None),
    )
    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/printers/S1/current-job/file")
    assert resp.status_code == 404
