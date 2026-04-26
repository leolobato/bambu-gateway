"""Tests for Settings class slice job concurrency configuration."""

from __future__ import annotations

from app.config import Settings


def test_slice_max_concurrent_defaults_to_one():
    s = Settings(_env_file=None)
    assert s.slice_max_concurrent == 1


def test_slice_max_concurrent_reads_env(monkeypatch):
    monkeypatch.setenv("SLICE_MAX_CONCURRENT", "3")
    s = Settings(_env_file=None)
    assert s.slice_max_concurrent == 3
