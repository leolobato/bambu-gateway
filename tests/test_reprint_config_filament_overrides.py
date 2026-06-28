import pytest
from httpx import AsyncClient, ASGITransport
import app.main as app_main


@pytest.mark.asyncio
async def test_reprint_config_includes_filament_overrides(tmp_path, monkeypatch):
    # Build a manager with one job carrying filament_overrides, injected as the
    # module-level `slice_jobs`. Mirror the harness in
    # tests/test_slice_job_manager.py for constructing a SliceJobManager.
    from app.slice_jobs import SliceJob

    class _Stub:
        async def get(self, job_id):
            blob = tmp_path / "in.3mf"; blob.write_bytes(b"x")
            return SliceJob.new(
                filename="a.3mf", machine_profile="GM020", process_profile="GP109",
                filament_profiles=["GFL99"], plate_id=0, plate_type="",
                project_filament_count=1, printer_id=None, auto_print=False,
                input_path=blob,
                filament_overrides={"0": {"nozzle_temperature": "230"}},
            )
    monkeypatch.setattr(app_main, "slice_jobs", _Stub())
    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/slice-jobs/anyid/reprint-config")
    assert r.status_code == 200
    assert r.json()["filament_overrides"] == {"0": {"nozzle_temperature": "230"}}
