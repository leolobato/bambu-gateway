import pytest
from app.slice_jobs import SliceJobManager, SliceJobStore


def _manager(tmp_path):
    store = SliceJobStore(tmp_path / "jobs.json")
    return SliceJobManager(store=store, slicer=object(), printer_service=None, notifier=None)


@pytest.mark.asyncio
async def test_submit_enqueue_false_does_not_queue(tmp_path):
    mgr = _manager(tmp_path)
    job = await mgr.submit(
        file_data=b"x", filename="a.3mf", machine_profile="GM020",
        process_profile="GP109", filament_profiles=["GFL99"], plate_id=0,
        plate_type="", project_filament_count=1, printer_id=None,
        auto_print=False, enqueue=False,
    )
    assert mgr._queue.empty()
    assert (await mgr.get(job.id)) is not None
    assert job.output_path is None


@pytest.mark.asyncio
async def test_submit_default_enqueues(tmp_path):
    mgr = _manager(tmp_path)
    await mgr.submit(
        file_data=b"x", filename="a.3mf", machine_profile="GM020",
        process_profile="GP109", filament_profiles=["GFL99"], plate_id=0,
        plate_type="", project_filament_count=1, printer_id=None,
        auto_print=False,
    )
    assert not mgr._queue.empty()
