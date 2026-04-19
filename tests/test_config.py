"""Tests for APNs config parsing."""

from __future__ import annotations

import os

from app.config import Settings


def test_push_enabled_when_all_apns_vars_set(tmp_path):
    key_file = tmp_path / "AuthKey.p8"
    key_file.write_text("-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n")
    s = Settings(
        apns_key_path=str(key_file),
        apns_key_id="KEY123",
        apns_team_id="TEAM456",
        apns_bundle_id="org.example.app",
        apns_environment="sandbox",
    )
    assert s.push_enabled is True


def test_push_disabled_when_key_path_missing():
    s = Settings(
        apns_key_path="",
        apns_key_id="KEY123",
        apns_team_id="TEAM456",
        apns_bundle_id="org.example.app",
    )
    assert s.push_enabled is False


def test_push_disabled_when_key_file_does_not_exist():
    s = Settings(
        apns_key_path="/nonexistent/path/AuthKey.p8",
        apns_key_id="KEY123",
        apns_team_id="TEAM456",
        apns_bundle_id="org.example.app",
    )
    assert s.push_enabled is False


def test_push_disabled_when_any_field_missing(tmp_path):
    key_file = tmp_path / "AuthKey.p8"
    key_file.write_text("fake")
    s = Settings(
        apns_key_path=str(key_file),
        apns_key_id="KEY123",
        apns_team_id="",  # missing
        apns_bundle_id="org.example.app",
    )
    assert s.push_enabled is False
