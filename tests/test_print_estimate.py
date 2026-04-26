"""Tests for slicer print estimate propagation."""

from __future__ import annotations

import base64
import json
import zipfile
from io import BytesIO
from types import SimpleNamespace

from fastapi import UploadFile

from app import main as app_main
from app.models import PrintEstimate, PrintResponse
from app.print_estimate import extract_print_estimate
from app.slicer_client import SliceResult


def _decode_estimate_header(value: str) -> dict:
    return json.loads(base64.b64decode(value).decode())


def _zip_bytes(files: dict[str, str | bytes]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            data = content.encode() if isinstance(content, str) else content
            zf.writestr(name, data)
    return buf.getvalue()


def test_print_response_includes_optional_estimate():
    response = PrintResponse(
        status="uploading",
        file_name="demo.3mf",
        printer_id="P01",
        was_sliced=True,
        estimate=PrintEstimate(
            total_filament_millimeters=9280.0,
            total_filament_grams=29.46,
            model_filament_millimeters=9120.0,
            model_filament_grams=28.96,
            prepare_seconds=356,
            model_print_seconds=9000,
            total_seconds=9356,
        ),
    )

    assert response.model_dump()["estimate"] == {
        "total_filament_millimeters": 9280.0,
        "total_filament_grams": 29.46,
        "model_filament_millimeters": 9120.0,
        "model_filament_grams": 28.96,
        "prepare_seconds": 356,
        "model_print_seconds": 9000,
        "total_seconds": 9356,
    }


def test_extract_print_estimate_from_sliced_3mf_slice_info():
    archive = _zip_bytes({
        "Metadata/slice_info.config": """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="prediction" value="7518"/>
    <metadata key="weight" value="80.41"/>
    <filament id="1" used_m="26.53" used_g="80.41" />
    <filament id="2" used_m="1.25" used_g="2.50" />
  </plate>
</config>
""",
    })

    estimate = extract_print_estimate(archive)

    assert estimate == PrintEstimate(
        total_filament_millimeters=27780.0,
        total_filament_grams=82.91,
        model_filament_millimeters=27780.0,
        model_filament_grams=82.91,
        model_print_seconds=7518,
        total_seconds=7518,
    )


async def test_print_preview_returns_base64_estimate_header(monkeypatch):
    estimate = PrintEstimate(total_seconds=9356, total_filament_grams=29.46)

    class StubSlicer:
        async def slice(self, *args, **kwargs):
            return SliceResult(content=b"sliced-3mf", estimate=estimate)

    monkeypatch.setattr(app_main, "slicer_client", StubSlicer())
    monkeypatch.setattr(
        app_main,
        "parse_3mf",
        lambda data: SimpleNamespace(filaments=[]),
    )

    upload = UploadFile(filename="demo.3mf", file=BytesIO(b"raw-3mf"))
    response = await app_main.print_preview(
        file=upload,
        printer_id="P01",
        plate_id=1,
        machine_profile="GM020",
        process_profile="0.16mm",
        filament_profiles="",
        plate_type="",
    )

    assert response.headers["X-Print-Estimate"]
    assert _decode_estimate_header(response.headers["X-Print-Estimate"]) == {
        "total_seconds": 9356,
        "total_filament_grams": 29.46,
    }


async def test_print_stream_preview_result_includes_estimate(monkeypatch, tmp_path):
    import asyncio as _asyncio
    from unittest.mock import MagicMock
    from app.slice_jobs import SliceJobManager, SliceJobStore

    async def stream_events(*args, **kwargs):
        yield {
            "event": "result",
            "data": {
                "file_base64": base64.b64encode(b"sliced-3mf").decode(),
                "estimate": {"total_seconds": 9356},
            },
        }
        yield {"event": "done", "data": {}}

    class StubSlicer:
        def slice_stream(self, *args, **kwargs):
            return stream_events()

    stub_slicer = StubSlicer()
    monkeypatch.setattr(app_main, "slicer_client", stub_slicer)
    monkeypatch.setattr(
        app_main,
        "parse_3mf",
        lambda data: SimpleNamespace(filaments=[]),
    )

    async def _fake_resolve(*a, **kw):
        return {}, None
    monkeypatch.setattr(app_main, "_resolve_slice_filament_payload", _fake_resolve)

    printer_service = MagicMock()
    printer_service.default_printer_id.return_value = None
    monkeypatch.setattr(app_main, "printer_service", printer_service)

    store = SliceJobStore(tmp_path / "slice_jobs.json")
    manager = SliceJobManager(
        store=store, slicer=stub_slicer, printer_service=printer_service,
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    monkeypatch.setattr(app_main, "slice_jobs", manager)

    try:
        upload = UploadFile(filename="demo.3mf", file=BytesIO(b"raw-3mf"))
        response = await app_main.print_file_stream(
            file=upload,
            printer_id="P01",
            plate_id=1,
            machine_profile="GM020",
            process_profile="0.16mm",
            filament_profiles="",
            plate_type="",
            preview=True,
        )

        body = b""
        async for chunk in response.body_iterator:
            body += chunk.encode() if isinstance(chunk, str) else chunk

        text = body.decode()
        assert 'event: result' in text
        assert '"total_seconds": 9356' in text
        assert '"preview_id":' in text
    finally:
        await manager.stop()


async def test_print_stream_preview_derives_estimate_from_sliced_3mf(monkeypatch, tmp_path):
    import asyncio as _asyncio
    from unittest.mock import MagicMock
    from app.slice_jobs import SliceJobManager, SliceJobStore

    sliced = _zip_bytes({
        "Metadata/slice_info.config": """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="prediction" value="120"/>
    <filament id="1" used_m="3.5" used_g="10.25" />
  </plate>
</config>
""",
    })

    async def stream_events(*args, **kwargs):
        yield {
            "event": "result",
            "data": {"file_base64": base64.b64encode(sliced).decode()},
        }
        yield {"event": "done", "data": {}}

    class StubSlicer:
        def slice_stream(self, *args, **kwargs):
            return stream_events()

    stub_slicer = StubSlicer()
    monkeypatch.setattr(app_main, "slicer_client", stub_slicer)
    monkeypatch.setattr(
        app_main,
        "parse_3mf",
        lambda data: SimpleNamespace(filaments=[]),
    )

    async def _fake_resolve(*a, **kw):
        return {}, None
    monkeypatch.setattr(app_main, "_resolve_slice_filament_payload", _fake_resolve)

    printer_service = MagicMock()
    printer_service.default_printer_id.return_value = None
    monkeypatch.setattr(app_main, "printer_service", printer_service)

    store = SliceJobStore(tmp_path / "slice_jobs.json")
    manager = SliceJobManager(
        store=store, slicer=stub_slicer, printer_service=printer_service,
        notifier=None, max_concurrent=1,
    )
    await manager.start()
    monkeypatch.setattr(app_main, "slice_jobs", manager)

    try:
        upload = UploadFile(filename="demo.3mf", file=BytesIO(b"raw-3mf"))
        response = await app_main.print_file_stream(
            file=upload,
            printer_id="P01",
            plate_id=1,
            machine_profile="GM020",
            process_profile="0.16mm",
            filament_profiles="",
            plate_type="",
            preview=True,
        )

        body = b""
        async for chunk in response.body_iterator:
            body += chunk.encode() if isinstance(chunk, str) else chunk

        text = body.decode()
        assert '"estimate": {' in text or '"estimate":{' in text
        assert '"total_filament_millimeters": 3500.0' in text
        assert '"total_filament_grams": 10.25' in text
        assert '"total_seconds": 120' in text
    finally:
        await manager.stop()
