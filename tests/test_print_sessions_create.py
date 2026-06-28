from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

import app.main as app_main
from app.config import PrinterConfig


async def test_print_session_unknown_printer_400(monkeypatch):
    # slice_jobs + slicer_client present, but printer_id not found
    monkeypatch.setattr(app_main, "slice_jobs", object())
    monkeypatch.setattr(app_main, "slicer_client", object())
    monkeypatch.setattr(app_main, "_printer_config_by_id", lambda pid: None, raising=False)
    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(
            "/api/print-sessions",
            data={"printer_id": "nope"},
            files={"file": ("m.3mf", b"PK\x03\x04", "model/3mf")},
        )
    assert r.status_code == 400


def _authored_filament(index: int, setting_id: str, used: bool = True):
    return SimpleNamespace(index=index, setting_id=setting_id, used=used)


def _authored_info(bed_type: str = "Cool Plate"):
    return SimpleNamespace(
        filaments=[_authored_filament(0, "GFL-AUTHORED")],
        print_profile=SimpleNamespace(print_settings_id="GP-AUTHORED"),
        bed_type=bed_type,
    )


@pytest.fixture
def session_env(monkeypatch):
    """Inject fakes for slicer_client + slice_jobs and a known printer."""
    captured: dict = {}

    # Printer carries the machine model + its preferred plate type (Task 1).
    printer = PrinterConfig(
        ip="1.2.3.4",
        access_code="x",
        serial="PRINTER1",
        machine_model="GM014",
        default_plate_type="textured_pei_plate",
    )
    monkeypatch.setattr(
        app_main, "_printer_config_by_id", lambda pid: printer if pid == "PRINTER1" else None,
        raising=False,
    )

    async def _fake_parse(data, slicer, *, plate_id=None):
        return _authored_info()
    monkeypatch.setattr(app_main, "parse_3mf_via_slicer", _fake_parse)

    slicer = MagicMock()

    async def _upload(data, *, filename="input.3mf"):
        return {"token": "srctok"}

    async def _resolve(*, machine_id, process_name="", filament_names=None, plate_type=""):
        captured["resolve"] = {
            "machine_id": machine_id,
            "process_name": process_name,
            "filament_names": list(filament_names or []),
            "plate_type": plate_type,
        }
        return {
            "process": {"setting_id": "GP-RESOLVED", "match": "alias"},
            "filaments": [
                {"slot": 0, "setting_id": "GFL-RESOLVED", "name": "PLA", "match": "alias"},
            ],
            "plate_type": {"resolved": "engineering_plate", "match": "default"},
        }

    async def _should_auto_center(token, machine):
        return False

    async def _prepare(token, **kwargs):
        captured["prepare"] = {"token": token, **kwargs}
        return {"input_token": "preptok", "content": b"prepared"}

    async def _get_profiles(kind):
        return [{"value": "cool_plate", "label": "Cool Plate"}]

    slicer.upload_3mf = _upload
    slicer.resolve_for_machine = _resolve
    slicer.should_auto_center_for_machine = _should_auto_center
    slicer.prepare_3mf_token = _prepare
    slicer.get_profiles = _get_profiles
    monkeypatch.setattr(app_main, "slicer_client", slicer)

    class FakeJobs:
        async def submit(self, **kwargs):
            captured["submit"] = kwargs
            return SimpleNamespace(id="job123")

    monkeypatch.setattr(app_main, "slice_jobs", FakeJobs())
    monkeypatch.setattr(app_main, "printer_service", MagicMock())

    return captured


async def test_print_session_3mf_happy_path(session_env):
    captured = session_env
    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/print-sessions",
            data={"printer_id": "PRINTER1"},
            files={"file": ("cube.3mf", b"PK\x03\x04", "model/3mf")},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job_id"] == "job123"
    # slice omitted → persisted unsliced, never enqueued.
    assert body["sliced"] is False
    assert body["handoff_url"].endswith("/print?reprint=job123")

    submit = captured["submit"]
    # Machine derived from the printer (no machine_profile supplied).
    assert submit["machine_profile"] == "GM014"
    # Process defaulted via resolve-for-machine.
    assert submit["process_profile"] == "GP-RESOLVED"
    # Resolve-for-machine filament substitution flows into the payload.
    assert submit["filament_profiles"] == ["GFL-RESOLVED"]
    # Plate precedence: printer default wins over authored ("Cool Plate")
    # and the machine default ("engineering_plate").
    assert submit["plate_type"] == "textured_pei_plate"
    # Not sliced now.
    assert submit["enqueue"] is False
    assert submit["auto_print"] is False

    # resolve-for-machine was fed the printer's machine + authored names.
    assert captured["resolve"]["machine_id"] == "GM014"
    assert captured["resolve"]["process_name"] == "GP-AUTHORED"
    assert captured["resolve"]["filament_names"] == ["GFL-AUTHORED"]


async def test_print_session_slice_true_enqueues(session_env):
    captured = session_env
    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/print-sessions",
            data={"printer_id": "PRINTER1", "slice": "true"},
            files={"file": ("cube.3mf", b"PK\x03\x04", "model/3mf")},
        )

    assert r.status_code == 200, r.text
    assert r.json()["sliced"] is True
    assert captured["submit"]["enqueue"] is True


async def test_print_session_machine_profile_overrides_printer(session_env):
    captured = session_env
    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/print-sessions",
            data={"printer_id": "PRINTER1", "machine_profile": "GM999"},
            files={"file": ("cube.3mf", b"PK\x03\x04", "model/3mf")},
        )

    assert r.status_code == 200, r.text
    assert captured["submit"]["machine_profile"] == "GM999"
    assert captured["resolve"]["machine_id"] == "GM999"


async def test_print_session_explicit_process_wins(session_env):
    captured = session_env
    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/print-sessions",
            data={"printer_id": "PRINTER1", "process_profile": "GP-PICKED"},
            files={"file": ("cube.3mf", b"PK\x03\x04", "model/3mf")},
        )

    assert r.status_code == 200, r.text
    assert captured["submit"]["process_profile"] == "GP-PICKED"


async def test_print_session_stl_resolves_process_before_import(session_env, monkeypatch):
    """STL import needs machine + process, so the resolver runs before import."""
    captured = session_env
    order: list[str] = []

    async def _import(data, *, filename, machine_profile, process_profile, **kw):
        order.append("import")
        captured["import"] = {"machine": machine_profile, "process": process_profile}
        return {"draft_token": "draft1"}

    async def _materialize(draft_token, **kw):
        order.append("materialize")
        return {"input_token": "stltok", "draft_token": draft_token}

    async def _parse_token(token, slicer, *, plate_id=None):
        return SimpleNamespace(
            filaments=[_authored_filament(0, "GFL-STL")],
            print_profile=SimpleNamespace(print_settings_id=""),
            bed_type="",
        )

    monkeypatch.setattr(app_main.slicer_client, "import_stl_draft", _import)
    monkeypatch.setattr(app_main.slicer_client, "materialize_stl_draft", _materialize)
    monkeypatch.setattr(app_main, "parse_3mf_token_via_slicer", _parse_token)

    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/print-sessions",
            data={"printer_id": "PRINTER1"},
            files={"file": ("widget.stl", b"solid", "model/stl")},
        )

    assert r.status_code == 200, r.text
    assert order == ["import", "materialize"]
    # Resolver default process was used for the import.
    assert captured["import"]["process"] == "GP-RESOLVED"
    assert captured["import"]["machine"] == "GM014"
    # STL has no authored plate, so the printer default still wins.
    assert captured["submit"]["plate_type"] == "textured_pei_plate"
    assert captured["submit"]["filename"] == "widget.3mf"
