"""Tests for cloud-mode settings."""
from __future__ import annotations

from pathlib import Path

from app.config import Settings


def test_cloud_disabled_by_default(monkeypatch):
    monkeypatch.delenv("BAMBU_CLOUD_ENABLED", raising=False)
    monkeypatch.delenv("BAMBU_CLOUD_REGION", raising=False)
    monkeypatch.delenv("BAMBU_CLOUD_PLUGIN_DIR", raising=False)
    settings = Settings()
    assert settings.bambu_cloud_enabled is False
    assert settings.bambu_cloud_region == "US"
    assert settings.bambu_cloud_plugin_dir == Path("/data/bambu-plugin")


def test_cloud_enabled_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BAMBU_CLOUD_ENABLED", "true")
    monkeypatch.setenv("BAMBU_CLOUD_REGION", "CN")
    monkeypatch.setenv("BAMBU_CLOUD_PLUGIN_DIR", str(tmp_path))
    settings = Settings()
    assert settings.bambu_cloud_enabled is True
    assert settings.bambu_cloud_region == "CN"
    assert settings.bambu_cloud_plugin_dir == tmp_path


def test_cloud_region_rejects_unknown(monkeypatch):
    monkeypatch.setenv("BAMBU_CLOUD_REGION", "EU")
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings()
