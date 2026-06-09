"""Contract test: the fake host, the real C++ host, and the Python callers
must agree on the RPC method surface.

The cloud test suite runs against ``tests/cloud_fake_host.py``, so a method
that exists only in the fake passes every test while failing in production
against the real binary (this happened with ``is_user_login``/``user_logout``).
This test parses the dispatch tables out of both hosts' sources — no C++
build needed — and cross-checks them against every method name the app
actually calls.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
METHODS_CPP = REPO / "tools" / "bambu_cloud_host" / "methods.cpp"
FAKE_HOST = REPO / "tests" / "cloud_fake_host.py"
APP_DIR = REPO / "app"


def real_host_methods() -> set[str]:
    """Method names registered in the C++ dispatch table."""
    src = METHODS_CPP.read_text()
    dispatch = src[src.index("json dispatch_method"):]
    return set(re.findall(r'if \(method == "([^"]+)"\)', dispatch))


def fake_host_methods() -> set[str]:
    """Method names the fake host's _dispatch handles."""
    src = FAKE_HOST.read_text()
    return set(re.findall(r'if method == "([^"]+)"', src))


def python_called_methods() -> set[str]:
    """Every RPC method name production code passes to PluginHost.call."""
    called: set[str] = set()
    for path in APP_DIR.rglob("*.py"):
        called.update(re.findall(r'\.call\(\s*"([^"]+)"', path.read_text()))
    return called


def test_parsers_find_the_dispatch_tables():
    assert "echo" in real_host_methods()
    assert "echo" in fake_host_methods()
    assert "init_plugin" in python_called_methods()


def test_production_calls_only_methods_the_real_host_implements():
    missing = python_called_methods() - real_host_methods()
    assert not missing, (
        f"app/ calls RPC methods the C++ host does not implement: {missing}. "
        "Add them to dispatch_method in tools/bambu_cloud_host/methods.cpp."
    )


def test_fake_host_matches_real_host_method_for_method():
    real = real_host_methods()
    fake = fake_host_methods()
    assert fake == real, (
        f"fake-only (tests pass but production breaks): {fake - real}; "
        f"real-only (untestable without the binary): {real - fake}"
    )
