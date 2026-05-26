"""Tests for cloud-mode settings."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

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
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings()


def test_lifespan_calls_downloader_when_cloud_enabled(monkeypatch, tmp_path):
    # settings is a module-level singleton; patch its attributes directly.
    monkeypatch.chdir(tmp_path)
    import app.main as main_mod
    monkeypatch.setattr(main_mod.settings, "bambu_cloud_enabled", True)
    monkeypatch.setattr(main_mod.settings, "bambu_cloud_plugin_dir", tmp_path)
    monkeypatch.setattr(main_mod.settings, "bambu_cloud_region", "US")
    with (
        patch(
            "app.cloud.plugin_downloader.PluginDownloader.ensure_active",
            new=AsyncMock(return_value=None),
        ) as mock_ensure,
        patch(
            "app.printer_service.PrinterService.start",
            new=MagicMock(),
        ),
    ):
        from app.main import app
        with TestClient(app):
            pass
        mock_ensure.assert_awaited_once()


def test_cloud_host_binary_default(monkeypatch):
    monkeypatch.delenv("BAMBU_CLOUD_HOST_BINARY", raising=False)
    settings = Settings()
    assert settings.bambu_cloud_host_binary == Path("/usr/local/bin/bambu_cloud_host")


def test_cloud_host_binary_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BAMBU_CLOUD_HOST_BINARY", str(tmp_path / "host"))
    settings = Settings()
    assert settings.bambu_cloud_host_binary == tmp_path / "host"


def test_lifespan_skips_downloader_when_cloud_disabled(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    import app.main as main_mod
    monkeypatch.setattr(main_mod.settings, "bambu_cloud_enabled", False)
    with (
        patch(
            "app.cloud.plugin_downloader.PluginDownloader.ensure_active",
            new=AsyncMock(return_value=None),
        ) as mock_ensure,
        patch(
            "app.printer_service.PrinterService.start",
            new=MagicMock(),
        ),
    ):
        from app.main import app
        with TestClient(app):
            pass
        mock_ensure.assert_not_awaited()
