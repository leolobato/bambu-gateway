"""Tests for the BAMBU_NETWORK_ERR_* error code map."""
from app.cloud.error_codes import error_message


def test_error_message_known_code():
    assert "OSS upload" in error_message(-2110)


def test_error_message_unknown_code_falls_back():
    assert "1234" in error_message(1234)
