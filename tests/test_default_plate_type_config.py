from app.config import PrinterConfig
from app.config_store import _serialize, _deserialize


def test_default_plate_type_round_trips():
    cfg = PrinterConfig(ip="1.1.1.1", access_code="x", serial="S1",
                        name="P", machine_model="GM020",
                        default_plate_type="textured_pei_plate")
    out = _deserialize(_serialize([cfg]))[0]
    assert out.default_plate_type == "textured_pei_plate"


def test_default_plate_type_defaults_empty_and_legacy_loads():
    assert PrinterConfig(ip="i", access_code="a", serial="s").default_plate_type == ""
    # legacy printers.json entry without the key still loads
    legacy = [{"serial": "s", "ip": "i", "access_code": "a", "name": "n"}]
    assert _deserialize(legacy)[0].default_plate_type == ""
