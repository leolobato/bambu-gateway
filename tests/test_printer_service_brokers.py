"""Tests: one PrintEventBroker per printer wired through PrinterService."""

from __future__ import annotations

import asyncio
from unittest.mock import patch, MagicMock

import pytest

from app.config import PrinterConfig
from app.print_event_broker import PrintEventBroker
from app.printer_service import PrinterService


def _make_config(serial: str) -> PrinterConfig:
    return PrinterConfig(
        ip="127.0.0.1",
        access_code="x",
        serial=serial,
        name=f"Printer {serial}",
    )


def _make_service(*serials: str) -> PrinterService:
    """Build a PrinterService without real MQTT connections."""
    configs = [_make_config(s) for s in serials]
    # Patch BambuMQTTClient so no real MQTT is created.
    with patch("app.printer_service.BambuMQTTClient") as MockClient:
        MockClient.side_effect = lambda cfg: MagicMock(spec=["attach_event_broker",
                                                              "set_status_change_callback",
                                                              "stop", "get_status",
                                                              "ensure_connected"])
        svc = PrinterService(configs)
    return svc


@pytest.mark.asyncio
async def test_each_printer_has_its_own_broker():
    svc = _make_service("AAA", "BBB")
    await svc.start()

    broker_a = svc.get_event_broker("AAA")
    broker_b = svc.get_event_broker("BBB")

    assert broker_a is not None
    assert broker_b is not None
    assert isinstance(broker_a, PrintEventBroker)
    assert isinstance(broker_b, PrintEventBroker)
    assert broker_a is not broker_b


@pytest.mark.asyncio
async def test_unknown_printer_returns_none_broker():
    svc = _make_service("AAA")
    await svc.start()

    assert svc.get_event_broker("nope") is None


@pytest.mark.asyncio
async def test_sync_printers_attaches_and_removes_brokers():
    """sync_printers adds a broker for new printers and removes it for deleted ones."""
    svc = _make_service("AAA")
    await svc.start()

    # Add a new printer via sync_printers while a running loop is present.
    new_cfg = _make_config("BBB")
    existing_cfg = _make_config("AAA")
    with patch("app.printer_service.BambuMQTTClient") as MockClient:
        MockClient.side_effect = lambda cfg: MagicMock(spec=[
            "attach_event_broker",
            "set_status_change_callback",
            "stop",
            "get_status",
            "ensure_connected",
        ])
        svc.sync_printers([existing_cfg, new_cfg])

    broker_bbb = svc.get_event_broker("BBB")
    assert broker_bbb is not None, "New printer BBB should have a broker after sync_printers"
    assert isinstance(broker_bbb, PrintEventBroker)

    # Now remove BBB via sync_printers.
    with patch("app.printer_service.BambuMQTTClient"):
        svc.sync_printers([existing_cfg])

    assert svc.get_event_broker("BBB") is None, "Removed printer BBB should have no broker"
