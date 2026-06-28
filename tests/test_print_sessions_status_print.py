import pytest
from httpx import AsyncClient, ASGITransport
import app.main as app_main


@pytest.mark.asyncio
async def test_print_action_403_when_disabled(monkeypatch):
    monkeypatch.setattr(app_main.settings, "allow_agent_print", False, raising=False)
    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/print-sessions/anyid/print")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_print_action_409_when_not_sliced(tmp_path, monkeypatch):
    from app.slice_jobs import SliceJob
    monkeypatch.setattr(app_main.settings, "allow_agent_print", True, raising=False)
    class _Stub:
        async def get(self, jid):
            blob = tmp_path / "in.3mf"; blob.write_bytes(b"x")
            return SliceJob.new(filename="a.3mf", machine_profile="GM020",
                process_profile="GP109", filament_profiles=["GFL99"], plate_id=0,
                plate_type="", project_filament_count=1, printer_id="p1",
                auto_print=False, input_path=blob)  # no output_path → unsliced
    monkeypatch.setattr(app_main, "slice_jobs", _Stub())
    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/print-sessions/anyid/print")
    assert r.status_code == 409
