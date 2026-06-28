from app.print_sessions import resolve_plate_type, build_handoff_url


def test_plate_precedence_request_wins():
    assert resolve_plate_type("cool_plate", "textured_pei_plate", "smooth_plate", "x") == "cool_plate"

def test_plate_precedence_printer_default_over_authored():
    assert resolve_plate_type("", "textured_pei_plate", "smooth_plate", "x") == "textured_pei_plate"

def test_plate_precedence_authored_over_machine():
    assert resolve_plate_type("", "", "smooth_plate", "machine_def") == "smooth_plate"

def test_plate_precedence_machine_default_last():
    assert resolve_plate_type("", "", "", "machine_def") == "machine_def"

def test_build_handoff_url():
    assert build_handoff_url("https://g.example/", "ab12") == "https://g.example/print?reprint=ab12"
