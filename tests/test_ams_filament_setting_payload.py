"""Tests for send_ams_filament_setting MQTT payload composition."""

from __future__ import annotations

from unittest.mock import patch

from app.config import PrinterConfig
from app.mqtt_client import BambuMQTTClient


def _make_client() -> BambuMQTTClient:
    return BambuMQTTClient(PrinterConfig(
        ip="1.2.3.4", access_code="0000", serial="P01", machine_model="X1C",
    ))


def test_send_ams_filament_setting_minimal_omits_optionals():
    client = _make_client()
    with patch.object(BambuMQTTClient, "publish") as publish:
        client.send_ams_filament_setting(
            ams_id=0,
            tray_id=1,
            tray_info_idx="GFA00",
            tray_color="00FF00FF",
            tray_type="PLA",
            nozzle_temp_min=190,
            nozzle_temp_max=240,
            setting_id="GFSA00_02",
        )
    publish.assert_called_once()
    payload = publish.call_args.args[0]
    inner = payload["print"]
    assert inner["command"] == "ams_filament_setting"
    assert inner["ams_id"] == 0
    assert inner["tray_id"] == 1
    assert inner["tray_info_idx"] == "GFA00"
    assert inner["tray_color"] == "00FF00FF"
    assert inner["tray_type"] == "PLA"
    assert inner["setting_id"] == "GFSA00_02"
    # Optional extras must be absent when not provided
    for absent in ("tag_uid", "bed_temp", "tray_weight", "remain", "k", "n", "tray_uuid", "cali_idx"):
        assert absent not in inner, f"unexpected key {absent!r} in payload"


def test_send_ams_filament_setting_includes_provided_extras():
    client = _make_client()
    with patch.object(BambuMQTTClient, "publish") as publish:
        client.send_ams_filament_setting(
            ams_id=0,
            tray_id=2,
            tray_info_idx="GFA00",
            tray_color="FFFFFFFF",
            tray_type="PLA",
            nozzle_temp_min=200,
            nozzle_temp_max=250,
            setting_id="GFSA00_02",
            tag_uid="ABCDEF0123456789",
            bed_temp=55,
            tray_weight=950,
            remain=80,
            k=0.025,
            n=1.4,
            tray_uuid="uuid-xyz",
            cali_idx=3,
        )
    inner = publish.call_args.args[0]["print"]
    assert inner["tag_uid"] == "ABCDEF0123456789"
    assert inner["bed_temp"] == 55
    assert inner["tray_weight"] == 950
    assert inner["remain"] == 80
    assert inner["k"] == 0.025
    assert inner["n"] == 1.4
    assert inner["tray_uuid"] == "uuid-xyz"
    assert inner["cali_idx"] == 3


def test_send_ams_filament_setting_partial_extras_only_included_keys():
    client = _make_client()
    with patch.object(BambuMQTTClient, "publish") as publish:
        client.send_ams_filament_setting(
            ams_id=0,
            tray_id=0,
            tray_info_idx="GFA00",
            tray_color="FFFFFFFF",
            tray_type="PLA",
            nozzle_temp_min=200,
            nozzle_temp_max=250,
            setting_id="GFSA00_02",
            tag_uid="abc",
            cali_idx=-1,
        )
    inner = publish.call_args.args[0]["print"]
    assert inner["tag_uid"] == "abc"
    assert inner["cali_idx"] == -1
    for absent in ("bed_temp", "tray_weight", "remain", "k", "n", "tray_uuid"):
        assert absent not in inner
