"""Tests for the cloud plugin downloader."""
from __future__ import annotations

from app.cloud import BAMBU_NETWORK_AGENT_VERSION
from app.cloud.plugin_downloader import bambu_studio_headers


def test_headers_brand_as_bambu_studio_linux():
    headers = bambu_studio_headers()
    assert headers["User-Agent"] == f"BambuStudio/{BAMBU_NETWORK_AGENT_VERSION}"
    assert headers["X-BBL-Client-Type"] == "slicer"
    assert headers["X-BBL-Client-Name"] == "BambuStudio"
    assert headers["X-BBL-Client-Version"] == BAMBU_NETWORK_AGENT_VERSION
    assert headers["X-BBL-OS-Type"] == "linux"


def test_headers_returns_a_fresh_dict_each_call():
    a = bambu_studio_headers()
    a["User-Agent"] = "mutated"
    b = bambu_studio_headers()
    assert b["User-Agent"] == f"BambuStudio/{BAMBU_NETWORK_AGENT_VERSION}"
