"""Shared pytest fixtures for the gateway test suite."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

FAKE_CLOUD_HOST = Path(__file__).parent / "cloud_fake_host.py"


@pytest.fixture
def frozen_now():
    """Provide a predictable 'now' for time-sensitive tests."""
    from freezegun import freeze_time
    with freeze_time("2026-04-20T17:00:00Z") as frozen:
        yield frozen


@pytest.fixture
def tmp_jobs_dir(tmp_path: Path) -> Path:
    """Return a temp directory with a slice_jobs/ subdir for blob storage."""
    (tmp_path / "slice_jobs").mkdir()
    return tmp_path


@pytest.fixture
def fake_slicer():
    """Return a MagicMock SlicerClient. Tests configure slice_stream() per case."""
    return MagicMock()


@pytest.fixture
def fake_printer_service():
    """Return a MagicMock PrinterService with sensible defaults."""
    svc = MagicMock()
    svc.get_cloud_client.return_value = None
    svc.default_printer_id.return_value = "PRINTER1"
    return svc


@pytest.fixture
def cloud_app_factory(monkeypatch, tmp_path):
    """Factory for a TestClient running the real lifespan in cloud mode.

    The C++ host binary is replaced with the Python fake host
    (``tests/cloud_fake_host.py``); the plugin download is stubbed out.
    Every RPC request the lifespan makes is recorded to a JSONL file exposed
    as ``client.rpc_record_file``.

    Usage::

        with cloud_app_factory(
            printers=[{"serial": "DEV1", "ip": "10.0.0.9", "access_code": "ac"}],
            fake_env={"FAKE_HOST_USER_LOGGED_IN": "1"},
        ) as client:
            ...
    """
    import app.main as main_mod
    from app.cloud.plugin_host import PluginHost
    from fastapi.testclient import TestClient

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_mod.settings, "bambu_cloud_enabled", True)
    monkeypatch.setattr(main_mod.settings, "bambu_cloud_plugin_dir", tmp_path)
    monkeypatch.setattr(main_mod.settings, "bambu_cloud_region", "US")
    monkeypatch.setattr(
        main_mod.settings, "bambu_cloud_host_binary", tmp_path / "ignored",
    )

    record_file = tmp_path / "rpc_requests.jsonl"

    @contextmanager
    def factory(*, printers: list[dict] | None = None,
                fake_env: dict[str, str] | None = None,
                download_error: Exception | None = None):
        # Seed printers.json explicitly (an empty file also blocks env-var seeding).
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)
        (data_dir / "printers.json").write_text(json.dumps([
            {"name": "", "access_code": "ac", **p} for p in (printers or [])
        ]))

        env = {"FAKE_HOST_RECORD_FILE": str(record_file)}
        env.update(fake_env or {})

        # The lifespan instantiates PluginHost(cmd=[<real binary>]); swap in a
        # wrapper that starts the Python fake host inside the test event loop.
        real_host_holder: list = []

        class _AlreadyOpenHost:
            def __init__(self, *_, **__): pass

            async def __aenter__(self):
                host = PluginHost(
                    cmd=[sys.executable, str(FAKE_CLOUD_HOST)], env=env,
                )
                await host.start()
                real_host_holder.append(host)
                return self

            async def __aexit__(self, *_):
                if real_host_holder:
                    await real_host_holder[0].stop()

            async def call(self, method, params):
                return await real_host_holder[0].call(method, params)

        with (
            patch(
                "app.cloud.plugin_downloader.PluginDownloader.ensure_active",
                new=AsyncMock(
                    return_value=None, side_effect=download_error,
                ),
            ),
            patch("app.printer_service.PrinterService.start", new=MagicMock()),
            patch("app.main.PluginHost", _AlreadyOpenHost),
        ):
            with TestClient(main_mod.app) as client:
                client.rpc_record_file = record_file
                yield client

    factory.record_file = record_file
    return factory
