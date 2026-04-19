"""Shared pytest fixtures for the gateway test suite."""

from __future__ import annotations

import pytest


@pytest.fixture
def frozen_now():
    """Provide a predictable 'now' for time-sensitive tests."""
    from freezegun import freeze_time
    with freeze_time("2026-04-20T17:00:00Z") as frozen:
        yield frozen
