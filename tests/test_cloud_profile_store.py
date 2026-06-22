from __future__ import annotations

from app.cloud.profile_store import (
    CloudProfile,
    clear_profile,
    load_profile,
    save_profile,
)


def test_from_api_extracts_uid_and_fields():
    p = CloudProfile.from_api(
        {"uidStr": "42", "name": "Alice", "account": "a@b", "avatar": "http://x/y.png"}
    )
    assert p == CloudProfile(name="Alice", account="a@b", avatar="http://x/y.png", uid="42")


def test_from_api_falls_back_for_missing_fields():
    p = CloudProfile.from_api({"uid": "7"})
    assert p == CloudProfile(name="", account="", avatar="", uid="7")


def test_save_load_round_trip(tmp_path):
    path = tmp_path / "state" / "gateway_profile.json"
    profile = CloudProfile(name="Alice", account="a@b", avatar="", uid="42")
    save_profile(path, profile)
    assert load_profile(path) == profile


def test_load_missing_returns_none(tmp_path):
    assert load_profile(tmp_path / "nope.json") is None


def test_load_corrupt_returns_none(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    assert load_profile(path) is None


def test_clear_removes_file(tmp_path):
    path = tmp_path / "state" / "gateway_profile.json"
    save_profile(path, CloudProfile(uid="1"))
    clear_profile(path)
    assert load_profile(path) is None
    clear_profile(path)  # idempotent — no error on missing
