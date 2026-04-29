from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.notification_hub import NotificationHub
from app.slice_jobs import SliceJob, SliceJobStatus


def _job(printer_id: str | None = "PRINTER1", status=SliceJobStatus.READY) -> SliceJob:
    j = SliceJob.new(
        filename="cube.3mf", machine_profile="GM014", process_profile="0.20mm",
        filament_profiles={}, plate_id=1, plate_type="",
        project_filament_count=0, printer_id=printer_id, auto_print=False,
        input_path=Path("/tmp/x.3mf"),
    )
    j.status = status
    return j


async def test_notify_ready_pushes_to_subscribers():
    apns = MagicMock()
    apns.send_alert = AsyncMock(return_value=SimpleNamespace(should_remove=False))
    store = MagicMock()
    store.subscribers_for_printer.return_value = [
        SimpleNamespace(device_token="tok-A"),
        SimpleNamespace(device_token="tok-B"),
    ]
    hub = NotificationHub(apns=apns, device_store=store, slice_store=MagicMock())

    await hub.notify_slice_terminal(_job(), "ready")

    assert apns.send_alert.await_count == 2
    args = apns.send_alert.await_args_list[0].kwargs
    assert args["event_type"] == "slice_ready"


async def test_notify_printing_includes_printer_name():
    apns = MagicMock()
    apns.send_alert = AsyncMock(return_value=SimpleNamespace(should_remove=False))
    store = MagicMock()
    store.subscribers_for_printer.return_value = [
        SimpleNamespace(device_token="tok-A"),
    ]
    printer_service = MagicMock()
    printer_service.get_config.return_value = SimpleNamespace(name="Garage")
    hub = NotificationHub(apns=apns, device_store=store, slice_store=MagicMock())
    hub.set_printer_service(printer_service)

    await hub.notify_slice_terminal(_job(), "printing")

    assert apns.send_alert.await_count == 1
    kwargs = apns.send_alert.await_args.kwargs
    assert kwargs["event_type"] == "slice_printing"
    assert kwargs["title"] == "Print started"
    assert "cube.3mf" in kwargs["body"]
    assert "Garage" in kwargs["body"]


async def test_notify_printing_omits_name_when_unresolved():
    apns = MagicMock()
    apns.send_alert = AsyncMock(return_value=SimpleNamespace(should_remove=False))
    store = MagicMock()
    store.subscribers_for_printer.return_value = [
        SimpleNamespace(device_token="tok-A"),
    ]
    hub = NotificationHub(apns=apns, device_store=store, slice_store=MagicMock())
    # No printer_service wired — body should still render without a name.

    await hub.notify_slice_terminal(_job(), "printing")

    body = apns.send_alert.await_args.kwargs["body"]
    assert body == "Started printing cube.3mf"


async def test_notify_failed_pushes_with_error():
    apns = MagicMock()
    apns.send_alert = AsyncMock(return_value=SimpleNamespace(should_remove=False))
    store = MagicMock()
    store.subscribers_for_printer.return_value = [
        SimpleNamespace(device_token="tok-A"),
    ]
    hub = NotificationHub(apns=apns, device_store=store, slice_store=MagicMock())
    job = _job(status=SliceJobStatus.FAILED)
    job.error = "slicer unreachable"

    await hub.notify_slice_terminal(job, "failed")

    assert apns.send_alert.await_count == 1
    body = apns.send_alert.await_args.kwargs["body"]
    assert "slicer unreachable" in body


async def test_notify_with_no_printer_id_broadcasts_to_all():
    apns = MagicMock()
    apns.send_alert = AsyncMock(return_value=SimpleNamespace(should_remove=False))
    store = MagicMock()
    store.list_devices.return_value = [
        SimpleNamespace(device_token="tok-A"),
        SimpleNamespace(device_token="tok-B"),
    ]
    hub = NotificationHub(apns=apns, device_store=store, slice_store=MagicMock())

    await hub.notify_slice_terminal(_job(printer_id=None), "ready")

    assert apns.send_alert.await_count == 2
    store.list_devices.assert_called_once()


async def test_notify_unknown_kind_is_noop():
    apns = MagicMock()
    apns.send_alert = AsyncMock()
    store = MagicMock()
    store.subscribers_for_printer.return_value = [
        SimpleNamespace(device_token="tok-A"),
    ]
    hub = NotificationHub(apns=apns, device_store=store, slice_store=MagicMock())

    await hub.notify_slice_terminal(_job(), "unknown")
    apns.send_alert.assert_not_awaited()
