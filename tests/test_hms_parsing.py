"""Tests for HMS array parsing in MQTT client."""

from __future__ import annotations

from app.config import PrinterConfig
from app.models import HMSCode
from app.mqtt_client import BambuMQTTClient


def _make_client() -> BambuMQTTClient:
    return BambuMQTTClient(PrinterConfig(
        ip="1.2.3.4", access_code="0000", serial="P01",
    ))


def test_hms_codes_parsed_from_print_info():
    client = _make_client()
    client._update_status({
        "hms": [
            {"attr": "0300200000010001", "code": "0001000A"},
            {"attr": "07008001", "code": "00020001"},
        ]
    })
    status = client._status
    assert status.hms_codes == [
        HMSCode(attr="0300200000010001", code="0001000A"),
        HMSCode(attr="07008001", code="00020001"),
    ]


def test_empty_hms_clears_existing_codes():
    client = _make_client()
    client._update_status({"hms": [{"attr": "a", "code": "b"}]})
    assert len(client._status.hms_codes) == 1
    client._update_status({"hms": []})
    assert client._status.hms_codes == []


def test_hms_missing_key_leaves_codes_unchanged():
    client = _make_client()
    client._update_status({"hms": [{"attr": "a", "code": "b"}]})
    client._update_status({"nozzle_temper": 200.0})
    assert len(client._status.hms_codes) == 1


def test_malformed_hms_entry_is_skipped():
    client = _make_client()
    client._update_status({
        "hms": [
            {"attr": "a", "code": "b"},
            {"attr": "only_attr"},  # missing code
            "not a dict",
        ]
    })
    assert client._status.hms_codes == [HMSCode(attr="a", code="b")]
