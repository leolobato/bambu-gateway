"""Tests for camera info + chamber light control."""

from __future__ import annotations

import asyncio

import pytest

from app.config import PrinterConfig
from app.mqtt_client import BambuMQTTClient
from app.printer_service import PrinterService, _classify_camera_transport


# ---------------------------------------------------------------------------
# Transport classification (pure function)

@pytest.mark.parametrize(
    "model,expected",
    [
        # Human-readable names
        ("X1C", "rtsps"),
        ("X1-Carbon", "rtsps"),
        ("X1E", "rtsps"),
        ("x1c", "rtsps"),  # case-insensitive
        ("P2S", "rtsps"),
        ("A1", "tcp_jpeg"),
        ("A1 Mini", "tcp_jpeg"),
        ("A1M", "tcp_jpeg"),
        ("P1P", "tcp_jpeg"),
        ("P1S", "tcp_jpeg"),
        # Bambu internal machine codes (what printers.json actually carries)
        ("GM001", "rtsps"),     # X1 Carbon
        ("GM002", "rtsps"),     # X1
        ("GM003", "rtsps"),     # X1E
        ("GM017", "tcp_jpeg"),  # P1P
        ("GM018", "tcp_jpeg"),  # P1S
        ("GM020", "tcp_jpeg"),  # A1 Mini
        ("GM021", "tcp_jpeg"),  # A1
        ("gm020", "tcp_jpeg"),  # case-insensitive on codes too
        # Empty / unknown
        ("", None),
        ("  ", None),
        ("Unknown", None),
        ("X3", None),  # hypothetical future model
        ("GM999", None),  # unknown code
    ],
)
def test_classifyCameraTransport_knownModels_maps(model, expected):
    assert _classify_camera_transport(model) == expected


# ---------------------------------------------------------------------------
# Lights report parsing

def _make_client(machine_model: str = "X1C") -> BambuMQTTClient:
    return BambuMQTTClient(PrinterConfig(
        ip="1.2.3.4", access_code="0000", serial="P01", machine_model=machine_model,
    ))


def test_chamberLight_unknownBeforeFirstReport():
    client = _make_client()
    assert client.chamber_light_on is None


def test_chamberLight_reportOn_setsTrue():
    client = _make_client()
    client._update_status({
        "lights_report": [{"node": "chamber_light", "mode": "on"}],
    })
    assert client.chamber_light_on is True


def test_chamberLight_reportOff_setsFalse():
    client = _make_client()
    client._update_status({
        "lights_report": [{"node": "chamber_light", "mode": "off"}],
    })
    assert client.chamber_light_on is False


def test_chamberLight_reportFlashing_treatedAsOn():
    client = _make_client()
    client._update_status({
        "lights_report": [{"node": "chamber_light", "mode": "flashing"}],
    })
    assert client.chamber_light_on is True


def test_chamberLight_ignoresOtherNodes():
    client = _make_client()
    client._update_status({
        "lights_report": [{"node": "work_light", "mode": "on"}],
    })
    assert client.chamber_light_on is None


def test_chamberLight_malformedEntries_skipped():
    client = _make_client()
    client._update_status({
        "lights_report": [
            "not a dict",
            {"node": "chamber_light"},  # no mode
            {"node": "chamber_light", "mode": 42},  # non-string mode
            {"node": "chamber_light", "mode": "on"},
        ],
    })
    assert client.chamber_light_on is True


# ---------------------------------------------------------------------------
# Service attaches camera info to PrinterStatus

def test_getStatus_xcModel_emitsRtspsCamera():
    service = PrinterService([PrinterConfig(
        ip="192.168.1.42", access_code="12345678",
        serial="P01", machine_model="X1C", name="Basement",
    )])
    status = service.get_status("P01")
    assert status is not None
    assert status.camera is not None
    assert status.camera.ip == "192.168.1.42"
    assert status.camera.access_code == "12345678"
    assert status.camera.transport == "rtsps"
    assert status.camera.chamber_light is not None
    assert status.camera.chamber_light.supported is True
    assert status.camera.chamber_light.on is None  # no report yet


def test_getStatus_a1Model_emitsTcpJpegCamera():
    service = PrinterService([PrinterConfig(
        ip="10.0.0.5", access_code="abc", serial="P02", machine_model="A1",
    )])
    status = service.get_status("P02")
    assert status.camera is not None
    assert status.camera.transport == "tcp_jpeg"


def test_getStatus_unknownModel_omitsCamera():
    service = PrinterService([PrinterConfig(
        ip="10.0.0.5", access_code="abc", serial="P03", machine_model="Future9000",
    )])
    status = service.get_status("P03")
    assert status.camera is None


def test_getStatus_missingAccessCode_omitsCamera():
    service = PrinterService([PrinterConfig(
        ip="10.0.0.5", access_code="", serial="P04", machine_model="X1C",
    )])
    status = service.get_status("P04")
    assert status.camera is None


def test_getStatus_reflectsReportedChamberLight():
    service = PrinterService([PrinterConfig(
        ip="1.1.1.1", access_code="z", serial="P05", machine_model="X1C",
    )])
    client = service.get_client("P05")
    client._update_status({
        "lights_report": [{"node": "chamber_light", "mode": "on"}],
    })
    status = service.get_status("P05")
    assert status.camera.chamber_light.on is True


# ---------------------------------------------------------------------------
# Endpoint smoke (validation only — we don't have a live MQTT mock)

def test_lightEndpoint_unknownPrinter_returns404(tmp_path, monkeypatch):
    monkeypatch.setenv("APNS_KEY_PATH", "")
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        res = c.post(
            "/api/printers/does-not-exist/light",
            json={"on": True, "node": "chamber_light"},
        )
        assert res.status_code == 404


def test_lightEndpoint_missingBody_returns422(tmp_path, monkeypatch):
    monkeypatch.setenv("APNS_KEY_PATH", "")
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        res = c.post("/api/printers/x/light", json={})
        # `on` is required — pydantic rejects the body.
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# CameraProxy wiring into PrinterService


@pytest.mark.asyncio
async def test_getCameraProxy_tcpJpegPrinter_returnsProxy():
    cfg = PrinterConfig(
        serial="01PXXX",
        ip="10.0.0.99",
        access_code="abcd",
        name="A1 Mini",
        machine_model="GM020",  # A1 Mini = tcp_jpeg
    )
    svc = PrinterService([cfg])
    try:
        proxy = svc.get_camera_proxy("01PXXX")
        assert proxy is not None
        # Same instance returned on repeat calls.
        assert svc.get_camera_proxy("01PXXX") is proxy
    finally:
        await svc.stop_async()


@pytest.mark.asyncio
async def test_getCameraProxy_rtspsPrinter_returnsNone():
    cfg = PrinterConfig(
        serial="X1S001",
        ip="10.0.0.50",
        access_code="abcd",
        name="X1C",
        machine_model="GM001",  # X1C = rtsps
    )
    svc = PrinterService([cfg])
    try:
        assert svc.get_camera_proxy("X1S001") is None
    finally:
        await svc.stop_async()


@pytest.mark.asyncio
async def test_getCameraProxy_unknownPrinter_returnsNone():
    svc = PrinterService([])
    try:
        assert svc.get_camera_proxy("MISSING") is None
    finally:
        await svc.stop_async()


@pytest.mark.asyncio
async def test_syncPrinters_ipChange_recreatesProxy():
    cfg = PrinterConfig(
        serial="01PXXX", ip="10.0.0.99", access_code="abcd",
        name="A1", machine_model="GM021",
    )
    svc = PrinterService([cfg])
    try:
        old = svc.get_camera_proxy("01PXXX")
        assert old is not None

        new_cfg = PrinterConfig(
            serial="01PXXX", ip="10.0.0.100", access_code="abcd",
            name="A1", machine_model="GM021",
        )
        svc.sync_printers([new_cfg])
        # Allow stop_async to run on the proxy.
        await asyncio.sleep(0)
        new = svc.get_camera_proxy("01PXXX")
        assert new is not None
        assert new is not old
    finally:
        await svc.stop_async()
