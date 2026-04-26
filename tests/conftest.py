"""Shared pytest fixtures for the gateway test suite."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


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
    svc.default_printer_id.return_value = "PRINTER1"
    return svc
