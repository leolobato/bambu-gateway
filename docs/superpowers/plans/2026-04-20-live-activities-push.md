# iOS Live Activities + Push Notifications — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show iOS Live Activities during prints and push alerts on fail / pause / complete / cancel / offline / HMS. APNs is optional — missing credentials degrade gracefully.

**Architecture:** Gateway parses MQTT state transitions, dedupes / throttles, and sends APNs HTTP/2 pushes (alert notifications + Live Activity updates). iOS app hosts a Widget Extension with `ActivityKit` attributes and registers push tokens against a new `DeviceStore` on the gateway.

**Tech Stack:** Python 3 / FastAPI / httpx / PyJWT / cryptography on gateway. Swift 5 / SwiftUI / ActivityKit / WidgetKit / UserNotifications on iOS. XcodeGen for project generation.

**Spec:** `docs/superpowers/specs/2026-04-20-live-activities-push-design.md`

**Repos / branches:**
- Gateway: `bambu-gateway` — branch `live-activities-push`
- iOS: `bambu-gateway-ios` — branch `live-activities-push`

**Plan location:** This file lives in the gateway repo; iOS-phase tasks apply to the iOS repo.

---

## Phase 1: Gateway

All Phase 1 work lives in `bambu-gateway` on the `live-activities-push` branch.

### Task 1: Pytest scaffolding

The gateway has no test suite today. Add `pytest` and a `tests/` directory so the remaining gateway tasks can be TDD'd.

**Files:**
- Modify: `requirements.txt`
- Create: `pytest.ini`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Add pytest to requirements**

Modify `requirements.txt` — append these lines:

```
pytest==8.3.4
pytest-asyncio==0.25.2
freezegun==1.5.1
```

- [ ] **Step 2: Install**

Run: `pip install -r requirements.txt`
Expected: pytest etc. installed successfully.

- [ ] **Step 3: Create pytest config**

Create `pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
asyncio_mode = auto
filterwarnings =
    ignore::DeprecationWarning
```

- [ ] **Step 4: Create empty test package**

Create `tests/__init__.py` — empty file.

Create `tests/conftest.py`:

```python
"""Shared pytest fixtures for the gateway test suite."""

from __future__ import annotations

import pytest


@pytest.fixture
def frozen_now():
    """Provide a predictable 'now' for time-sensitive tests."""
    from freezegun import freeze_time
    with freeze_time("2026-04-20T17:00:00Z") as frozen:
        yield frozen
```

- [ ] **Step 5: Sanity-check**

Run: `pytest -v`
Expected: `no tests ran in 0.XXs` exit 5 (no tests collected yet). This is fine — we just need pytest itself to work.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt pytest.ini tests/
git commit -m "Add pytest scaffolding for gateway test suite"
```

---

### Task 2: APNs config in Settings

Extend `Settings` with the APNs env vars plus a derived `push_enabled` flag.

**Files:**
- Modify: `app/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `Settings` has no `apns_*` fields yet.

- [ ] **Step 3: Add APNs fields to Settings**

Modify `app/config.py`. After the existing `max_file_size_mb` field and before `model_config`, add:

```python
    # APNs (optional — missing any field disables push)
    apns_key_path: str = ""
    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_bundle_id: str = ""
    apns_environment: str = "production"  # or "sandbox"
```

Then after `get_printers()`, add:

```python
    @property
    def push_enabled(self) -> bool:
        """True iff every APNs credential is present and the key file exists."""
        import os
        required = (
            self.apns_key_path,
            self.apns_key_id,
            self.apns_team_id,
            self.apns_bundle_id,
        )
        if not all(required):
            return False
        return os.path.isfile(self.apns_key_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "Add APNs settings with derived push_enabled flag"
```

---

### Task 3: APNs JWT helper

APNs HTTP/2 requires an `Authorization: bearer <jwt>` header. The JWT is signed with the `.p8` private key using ES256. Tokens must be < 60 minutes old; we cache and rotate at 50 minutes.

**Files:**
- Create: `app/apns_jwt.py`
- Create: `tests/test_apns_jwt.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add crypto deps**

Append to `requirements.txt`:

```
pyjwt[crypto]==2.10.1
cryptography==44.0.0
```

Run: `pip install -r requirements.txt`

- [ ] **Step 2: Write the failing test**

Create `tests/test_apns_jwt.py`:

```python
"""Tests for APNs JWT generation."""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ec import generate_private_key, SECP256R1
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat,
)

from app.apns_jwt import ApnsJwtSigner


@pytest.fixture
def p8_key(tmp_path):
    """Generate a throwaway ES256 key in PEM (.p8) format."""
    key = generate_private_key(SECP256R1())
    pem = key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption(),
    )
    path = tmp_path / "AuthKey_TEST.p8"
    path.write_bytes(pem)
    return str(path)


def test_sign_produces_valid_jwt(p8_key):
    signer = ApnsJwtSigner(
        key_path=p8_key, key_id="KEYID1", team_id="TEAMXYZ",
    )
    token = signer.current_token()
    # Decode without verification to inspect header/payload
    header = pyjwt.get_unverified_header(token)
    payload = pyjwt.decode(token, options={"verify_signature": False})
    assert header["alg"] == "ES256"
    assert header["kid"] == "KEYID1"
    assert payload["iss"] == "TEAMXYZ"
    assert "iat" in payload


def test_token_is_cached_within_window(p8_key):
    signer = ApnsJwtSigner(p8_key, "KEYID1", "TEAMXYZ")
    t1 = signer.current_token()
    t2 = signer.current_token()
    assert t1 == t2


def test_token_rotates_after_50_minutes(p8_key):
    signer = ApnsJwtSigner(p8_key, "KEYID1", "TEAMXYZ")
    t1 = signer.current_token()
    # Move signer's internal clock forward
    signer._issued_at = time.time() - (51 * 60)
    t2 = signer.current_token()
    assert t1 != t2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_apns_jwt.py -v`
Expected: FAIL — `app.apns_jwt` does not exist.

- [ ] **Step 4: Implement ApnsJwtSigner**

Create `app/apns_jwt.py`:

```python
"""Generate and cache APNs provider auth JWTs (ES256)."""

from __future__ import annotations

import threading
import time

import jwt as pyjwt

_MAX_TOKEN_AGE_SECONDS = 50 * 60  # rotate before Apple's 60-minute hard cap


class ApnsJwtSigner:
    """Produces a fresh JWT for each push when the cached one ages out."""

    def __init__(self, key_path: str, key_id: str, team_id: str) -> None:
        self._key_path = key_path
        self._key_id = key_id
        self._team_id = team_id
        self._lock = threading.Lock()
        self._cached_token: str | None = None
        self._issued_at: float = 0.0

    def current_token(self) -> str:
        with self._lock:
            now = time.time()
            if (
                self._cached_token is not None
                and now - self._issued_at < _MAX_TOKEN_AGE_SECONDS
            ):
                return self._cached_token
            with open(self._key_path, "rb") as fh:
                key = fh.read()
            token = pyjwt.encode(
                {"iss": self._team_id, "iat": int(now)},
                key,
                algorithm="ES256",
                headers={"kid": self._key_id},
            )
            self._cached_token = token
            self._issued_at = now
            return token
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_apns_jwt.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add app/apns_jwt.py tests/test_apns_jwt.py requirements.txt
git commit -m "Add APNs JWT signer with 50-minute cache rotation"
```

---

### Task 4: APNs client

HTTP/2 client that sends either alert or Live Activity pushes. Returns a structured `ApnsResult`. Handles 410 Unregistered and similar token-invalid responses.

**Files:**
- Create: `app/apns_client.py`
- Create: `tests/test_apns_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_apns_client.py`:

```python
"""Tests for the APNs HTTP/2 client."""

from __future__ import annotations

import json

import httpx
import pytest

from app.apns_client import ApnsClient, ApnsPushType, ApnsResult


class StubSigner:
    def current_token(self) -> str:
        return "stub-jwt"


def _make_client(handler, env: str = "production") -> ApnsClient:
    transport = httpx.MockTransport(handler)
    return ApnsClient(
        signer=StubSigner(),
        bundle_id="org.example.app",
        environment=env,
        transport=transport,
    )


async def test_alert_push_sets_headers_and_payload():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200)

    client = _make_client(handler)
    result = await client.send_alert(
        device_token="abc123",
        title="Print paused",
        body="X1C paused on layer 42",
        event_type="print_paused",
        printer_id="P01",
    )
    assert result.ok is True
    assert result.token_invalid is False
    assert captured["url"].endswith("/3/device/abc123")
    assert "api.push.apple.com" in captured["url"]
    assert captured["headers"]["apns-push-type"] == "alert"
    assert captured["headers"]["apns-topic"] == "org.example.app"
    assert captured["headers"]["authorization"] == "bearer stub-jwt"
    assert captured["body"]["aps"]["alert"]["title"] == "Print paused"
    assert captured["body"]["printer_id"] == "P01"
    assert captured["body"]["event_type"] == "print_paused"


async def test_sandbox_environment_hits_sandbox_host():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200)

    client = _make_client(handler, env="sandbox")
    await client.send_alert(
        device_token="abc", title="t", body="b",
        event_type="print_paused", printer_id="P01",
    )
    assert "api.sandbox.push.apple.com" in captured["url"]


async def test_live_activity_update_uses_liveactivity_push_type():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200)

    client = _make_client(handler)
    await client.send_live_activity_update(
        activity_token="act123",
        content_state={"progress": 0.42, "state": "printing"},
        stale_after_seconds=3600,
    )
    assert captured["headers"]["apns-push-type"] == "liveactivity"
    assert captured["headers"]["apns-topic"] == "org.example.app.push-type.liveactivity"
    assert captured["body"]["aps"]["event"] == "update"
    assert captured["body"]["aps"]["content-state"]["progress"] == 0.42


async def test_410_unregistered_marks_token_invalid():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            410, json={"reason": "Unregistered"},
        )

    client = _make_client(handler)
    result = await client.send_alert(
        device_token="dead", title="t", body="b",
        event_type="print_paused", printer_id="P01",
    )
    assert result.ok is False
    assert result.token_invalid is True
    assert result.reason == "Unregistered"


async def test_500_error_not_token_invalid():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"reason": "InternalServerError"})

    client = _make_client(handler)
    result = await client.send_alert(
        device_token="tok", title="t", body="b",
        event_type="print_paused", printer_id="P01",
    )
    assert result.ok is False
    assert result.token_invalid is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_apns_client.py -v`
Expected: FAIL — `app.apns_client` does not exist.

- [ ] **Step 3: Implement ApnsClient**

Create `app/apns_client.py`:

```python
"""HTTP/2 client for sending APNs pushes (alerts and Live Activity updates)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

_TOKEN_INVALID_REASONS = {
    "Unregistered", "BadDeviceToken", "DeviceTokenNotForTopic",
}


class ApnsPushType(str, Enum):
    alert = "alert"
    liveactivity = "liveactivity"


class _Signer(Protocol):
    def current_token(self) -> str: ...


@dataclass
class ApnsResult:
    ok: bool
    status_code: int = 0
    reason: str = ""
    token_invalid: bool = False


class ApnsClient:
    """Send APNs pushes over HTTP/2. One instance per gateway."""

    def __init__(
        self,
        signer: _Signer,
        bundle_id: str,
        environment: str = "production",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._signer = signer
        self._bundle_id = bundle_id
        self._host = (
            "https://api.sandbox.push.apple.com"
            if environment == "sandbox"
            else "https://api.push.apple.com"
        )
        self._client = httpx.AsyncClient(
            http2=True, timeout=10.0, transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send_alert(
        self, *, device_token: str, title: str, body: str,
        event_type: str, printer_id: str,
    ) -> ApnsResult:
        payload: dict[str, Any] = {
            "aps": {
                "alert": {"title": title, "body": body},
                "sound": "default",
                "interruption-level": "time-sensitive",
            },
            "printer_id": printer_id,
            "event_type": event_type,
        }
        return await self._post(
            device_token=device_token,
            push_type=ApnsPushType.alert,
            topic=self._bundle_id,
            payload=payload,
            priority=10,
        )

    async def send_live_activity_update(
        self, *, activity_token: str, content_state: dict[str, Any],
        stale_after_seconds: int = 3600,
    ) -> ApnsResult:
        import time
        now = int(time.time())
        payload = {
            "aps": {
                "timestamp": now,
                "event": "update",
                "content-state": content_state,
                "stale-date": now + stale_after_seconds,
            }
        }
        return await self._post(
            device_token=activity_token,
            push_type=ApnsPushType.liveactivity,
            topic=f"{self._bundle_id}.push-type.liveactivity",
            payload=payload,
            priority=5,
        )

    async def send_live_activity_start(
        self, *, start_token: str, attributes_type: str,
        attributes: dict[str, Any], content_state: dict[str, Any],
        stale_after_seconds: int = 3600,
    ) -> ApnsResult:
        import time
        now = int(time.time())
        payload = {
            "aps": {
                "timestamp": now,
                "event": "start",
                "attributes-type": attributes_type,
                "attributes": attributes,
                "content-state": content_state,
                "stale-date": now + stale_after_seconds,
            }
        }
        return await self._post(
            device_token=start_token,
            push_type=ApnsPushType.liveactivity,
            topic=f"{self._bundle_id}.push-type.liveactivity",
            payload=payload,
            priority=10,
        )

    async def send_live_activity_end(
        self, *, activity_token: str, content_state: dict[str, Any],
        dismissal_seconds_from_now: int = 0,
    ) -> ApnsResult:
        import time
        now = int(time.time())
        payload = {
            "aps": {
                "timestamp": now,
                "event": "end",
                "content-state": content_state,
                "dismissal-date": now + dismissal_seconds_from_now,
            }
        }
        return await self._post(
            device_token=activity_token,
            push_type=ApnsPushType.liveactivity,
            topic=f"{self._bundle_id}.push-type.liveactivity",
            payload=payload,
            priority=10,
        )

    async def _post(
        self, *, device_token: str, push_type: ApnsPushType, topic: str,
        payload: dict[str, Any], priority: int,
    ) -> ApnsResult:
        headers = {
            "authorization": f"bearer {self._signer.current_token()}",
            "apns-push-type": push_type.value,
            "apns-topic": topic,
            "apns-priority": str(priority),
        }
        url = f"{self._host}/3/device/{device_token}"
        try:
            response = await self._client.post(
                url, headers=headers, content=json.dumps(payload),
            )
        except httpx.HTTPError as exc:
            logger.warning("APNs request failed: %s", exc)
            return ApnsResult(ok=False, reason=str(exc))

        if 200 <= response.status_code < 300:
            return ApnsResult(ok=True, status_code=response.status_code)

        reason = ""
        try:
            reason = response.json().get("reason", "")
        except Exception:
            reason = response.text
        token_invalid = reason in _TOKEN_INVALID_REASONS
        if response.status_code == 410:
            token_invalid = True
        return ApnsResult(
            ok=False,
            status_code=response.status_code,
            reason=reason,
            token_invalid=token_invalid,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_apns_client.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/apns_client.py tests/test_apns_client.py
git commit -m "Add APNs HTTP/2 client for alerts and Live Activity pushes"
```

---

### Task 5: DeviceStore

JSON-backed persistence for registered devices and active Live Activities. Thread-safe; used from both request handlers (FastAPI async) and the notification thread.

**Files:**
- Create: `app/device_store.py`
- Create: `tests/test_device_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_device_store.py`:

```python
"""Tests for device registry persistence."""

from __future__ import annotations

import json

import pytest

from app.device_store import DeviceStore, DeviceRecord, ActiveActivity


@pytest.fixture
def store(tmp_path):
    return DeviceStore(tmp_path / "devices.json")


def test_empty_store_returns_no_devices(store):
    assert store.list_devices() == []


def test_register_persists_to_disk(store, tmp_path):
    store.upsert_device(DeviceRecord(
        id="dev-1", name="iPhone",
        device_token="tok-a",
        live_activity_start_token="start-a",
        subscribed_printers=["*"],
    ))
    raw = json.loads((tmp_path / "devices.json").read_text())
    assert len(raw["devices"]) == 1
    assert raw["devices"][0]["id"] == "dev-1"


def test_upsert_updates_existing_device(store):
    store.upsert_device(DeviceRecord(
        id="dev-1", name="iPhone", device_token="old",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    store.upsert_device(DeviceRecord(
        id="dev-1", name="iPhone", device_token="new",
        live_activity_start_token="start", subscribed_printers=["P01"],
    ))
    devs = store.list_devices()
    assert len(devs) == 1
    assert devs[0].device_token == "new"
    assert devs[0].live_activity_start_token == "start"
    assert devs[0].subscribed_printers == ["P01"]


def test_remove_device_also_removes_its_activities(store):
    store.upsert_device(DeviceRecord(
        id="dev-1", name="iPhone", device_token="tok",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    store.add_activity(ActiveActivity(
        device_id="dev-1", printer_id="P01", activity_update_token="upd",
    ))
    store.remove_device("dev-1")
    assert store.list_devices() == []
    assert store.list_activities_for_printer("P01") == []


def test_invalidate_token_removes_just_that_token(store):
    store.upsert_device(DeviceRecord(
        id="dev-1", name="iPhone", device_token="bad",
        live_activity_start_token="good", subscribed_printers=["*"],
    ))
    store.invalidate_token("bad")
    dev = store.list_devices()[0]
    assert dev.device_token == ""
    assert dev.live_activity_start_token == "good"


def test_invalidate_activity_token_removes_activity(store):
    store.upsert_device(DeviceRecord(
        id="dev-1", name="iPhone", device_token="tok",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    store.add_activity(ActiveActivity(
        device_id="dev-1", printer_id="P01", activity_update_token="bad",
    ))
    store.invalidate_token("bad")
    assert store.list_activities_for_printer("P01") == []


def test_subscribers_for_printer_respects_wildcard_and_explicit(store):
    store.upsert_device(DeviceRecord(
        id="dev-a", name="A", device_token="ta",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    store.upsert_device(DeviceRecord(
        id="dev-b", name="B", device_token="tb",
        live_activity_start_token=None, subscribed_printers=["P02"],
    ))
    p01 = {d.id for d in store.subscribers_for_printer("P01")}
    p02 = {d.id for d in store.subscribers_for_printer("P02")}
    assert p01 == {"dev-a"}
    assert p02 == {"dev-a", "dev-b"}


def test_reload_from_disk(tmp_path):
    s1 = DeviceStore(tmp_path / "devices.json")
    s1.upsert_device(DeviceRecord(
        id="dev-1", name="iPhone", device_token="tok",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    s2 = DeviceStore(tmp_path / "devices.json")
    assert len(s2.list_devices()) == 1
    assert s2.list_devices()[0].id == "dev-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_device_store.py -v`
Expected: FAIL — `app.device_store` does not exist.

- [ ] **Step 3: Implement DeviceStore**

Create `app/device_store.py`:

```python
"""JSON-backed persistence for APNs devices and active Live Activities."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DeviceRecord:
    id: str
    name: str
    device_token: str
    live_activity_start_token: str | None
    subscribed_printers: list[str] = field(default_factory=list)
    registered_at: str = ""
    last_seen_at: str = ""


@dataclass
class ActiveActivity:
    device_id: str
    printer_id: str
    activity_update_token: str
    started_at: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class DeviceStore:
    """Thread-safe registry backed by a JSON file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._devices: dict[str, DeviceRecord] = {}
        self._activities: dict[tuple[str, str], ActiveActivity] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load device store %s: %s", self._path, exc)
            return
        for d in raw.get("devices", []):
            rec = DeviceRecord(
                id=d["id"], name=d.get("name", ""),
                device_token=d.get("device_token", ""),
                live_activity_start_token=d.get("live_activity_start_token") or None,
                subscribed_printers=d.get("subscribed_printers", []),
                registered_at=d.get("registered_at", ""),
                last_seen_at=d.get("last_seen_at", ""),
            )
            self._devices[rec.id] = rec
        for a in raw.get("active_activities", []):
            act = ActiveActivity(
                device_id=a["device_id"], printer_id=a["printer_id"],
                activity_update_token=a["activity_update_token"],
                started_at=a.get("started_at", ""),
            )
            self._activities[(act.device_id, act.printer_id)] = act

    def _save_locked(self) -> None:
        raw = {
            "devices": [asdict(d) for d in self._devices.values()],
            "active_activities": [asdict(a) for a in self._activities.values()],
        }
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(raw, indent=2))
        tmp.replace(self._path)

    def list_devices(self) -> list[DeviceRecord]:
        with self._lock:
            return list(self._devices.values())

    def get_device(self, device_id: str) -> DeviceRecord | None:
        with self._lock:
            return self._devices.get(device_id)

    def upsert_device(self, record: DeviceRecord) -> None:
        with self._lock:
            existing = self._devices.get(record.id)
            if existing and not record.registered_at:
                record.registered_at = existing.registered_at
            if not record.registered_at:
                record.registered_at = _now_iso()
            record.last_seen_at = _now_iso()
            self._devices[record.id] = record
            self._save_locked()

    def remove_device(self, device_id: str) -> None:
        with self._lock:
            self._devices.pop(device_id, None)
            self._activities = {
                k: v for k, v in self._activities.items() if v.device_id != device_id
            }
            self._save_locked()

    def add_activity(self, activity: ActiveActivity) -> None:
        with self._lock:
            if not activity.started_at:
                activity.started_at = _now_iso()
            self._activities[(activity.device_id, activity.printer_id)] = activity
            self._save_locked()

    def remove_activity(self, device_id: str, printer_id: str) -> None:
        with self._lock:
            self._activities.pop((device_id, printer_id), None)
            self._save_locked()

    def list_activities_for_printer(self, printer_id: str) -> list[ActiveActivity]:
        with self._lock:
            return [a for a in self._activities.values() if a.printer_id == printer_id]

    def invalidate_token(self, token: str) -> None:
        """Remove any device_token / start_token / activity_token equal to ``token``."""
        if not token:
            return
        with self._lock:
            changed = False
            for dev in self._devices.values():
                if dev.device_token == token:
                    dev.device_token = ""
                    changed = True
                if dev.live_activity_start_token == token:
                    dev.live_activity_start_token = None
                    changed = True
            for key in list(self._activities.keys()):
                if self._activities[key].activity_update_token == token:
                    del self._activities[key]
                    changed = True
            if changed:
                self._save_locked()

    def subscribers_for_printer(self, printer_id: str) -> list[DeviceRecord]:
        with self._lock:
            return [
                d for d in self._devices.values()
                if "*" in d.subscribed_printers or printer_id in d.subscribed_printers
            ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_device_store.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add app/device_store.py tests/test_device_store.py
git commit -m "Add DeviceStore JSON persistence for push tokens"
```

---

### Task 6: HMS parsing in MQTT client

Parse the `hms` array from MQTT reports. Each entry has `attr` (hex error code) and `code` (severity/type). Expose as a set on `PrinterStatus`.

**Files:**
- Modify: `app/models.py`
- Modify: `app/mqtt_client.py`
- Create: `tests/test_hms_parsing.py`

- [ ] **Step 1: Add HMS field to PrinterStatus**

Modify `app/models.py`, add before the `PrinterStatus` class:

```python
class HMSCode(BaseModel):
    """A Bambu Health Monitoring System error code."""
    attr: str  # hex, e.g. "0300_2000_0001_0001"
    code: str  # severity/category hex
```

Modify `PrinterStatus` — add after the `job` field:

```python
    hms_codes: list[HMSCode] = []
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_hms_parsing.py`:

```python
"""Tests for HMS array parsing in MQTT client."""

from __future__ import annotations

from app.config import PrinterConfig
from app.models import HMSCode
from app.mqtt_client import BambuMQTTClient


def _make_client() -> BambuMQTTClient:
    return BambuMQTTClient(PrinterConfig(
        ip="1.2.3.4", access_code="0000", serial="P01",
    ))


def test_hms_codes_parsed_from_print_info():
    client = _make_client()
    client._update_status({
        "hms": [
            {"attr": "0300200000010001", "code": "0001000A"},
            {"attr": "07008001", "code": "00020001"},
        ]
    })
    status = client._status
    assert status.hms_codes == [
        HMSCode(attr="0300200000010001", code="0001000A"),
        HMSCode(attr="07008001", code="00020001"),
    ]


def test_empty_hms_clears_existing_codes():
    client = _make_client()
    client._update_status({"hms": [{"attr": "a", "code": "b"}]})
    assert len(client._status.hms_codes) == 1
    client._update_status({"hms": []})
    assert client._status.hms_codes == []


def test_hms_missing_key_leaves_codes_unchanged():
    client = _make_client()
    client._update_status({"hms": [{"attr": "a", "code": "b"}]})
    client._update_status({"nozzle_temper": 200.0})
    assert len(client._status.hms_codes) == 1


def test_malformed_hms_entry_is_skipped():
    client = _make_client()
    client._update_status({
        "hms": [
            {"attr": "a", "code": "b"},
            {"attr": "only_attr"},  # missing code
            "not a dict",
        ]
    })
    assert client._status.hms_codes == [HMSCode(attr="a", code="b")]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_hms_parsing.py -v`
Expected: FAIL — HMS not parsed yet.

- [ ] **Step 4: Parse HMS in `_update_status`**

Modify `app/mqtt_client.py`, update the import block:

```python
from app.models import (
    AMSType,
    HMSCode,
    PrinterState,
    PrinterStatus,
    PrintJob,
    TemperatureInfo,
)
```

In `_update_status`, inside the `with self._lock:` block, **before** the state derivation section, add:

```python
            # HMS codes
            if "hms" in print_info:
                raw = print_info["hms"]
                parsed: list[HMSCode] = []
                if isinstance(raw, list):
                    for entry in raw:
                        if not isinstance(entry, dict):
                            continue
                        attr = entry.get("attr")
                        code = entry.get("code")
                        if isinstance(attr, str) and isinstance(code, str):
                            parsed.append(HMSCode(attr=attr, code=code))
                self._status.hms_codes = parsed
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_hms_parsing.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/mqtt_client.py tests/test_hms_parsing.py
git commit -m "Parse HMS codes from MQTT print reports"
```

---

### Task 7: State-change callback hook on MQTT client

Add a subscriber callback so `NotificationHub` can observe status diffs without polling. The callback fires after `_update_status` applies changes, with `(prev, new)` snapshots. Callback runs on the MQTT network thread — it must be fast and non-blocking.

**Files:**
- Modify: `app/mqtt_client.py`
- Create: `tests/test_mqtt_callback.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mqtt_callback.py`:

```python
"""Tests for MQTT state-change callback hook."""

from __future__ import annotations

from app.config import PrinterConfig
from app.mqtt_client import BambuMQTTClient


def _make_client() -> BambuMQTTClient:
    return BambuMQTTClient(PrinterConfig(
        ip="1.2.3.4", access_code="0000", serial="P01",
    ))


def test_callback_fires_with_prev_and_new_snapshots():
    client = _make_client()
    calls: list[tuple] = []
    client.set_status_change_callback(lambda prev, new: calls.append((prev.model_copy(), new.model_copy())))
    client._update_status({"nozzle_temper": 200.0})
    assert len(calls) == 1
    prev, new = calls[0]
    assert prev.temperatures.nozzle_temp == 0.0
    assert new.temperatures.nozzle_temp == 200.0


def test_callback_exception_does_not_break_update():
    client = _make_client()
    client.set_status_change_callback(lambda prev, new: (_ for _ in ()).throw(RuntimeError("boom")))
    # Should not raise
    client._update_status({"nozzle_temper": 42.0})
    assert client._status.temperatures.nozzle_temp == 42.0


def test_no_callback_works_fine():
    client = _make_client()
    client._update_status({"nozzle_temper": 1.0})
    assert client._status.temperatures.nozzle_temp == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mqtt_callback.py -v`
Expected: FAIL — `set_status_change_callback` does not exist.

- [ ] **Step 3: Implement callback hook**

Modify `app/mqtt_client.py`. Add these imports at top if not present:

```python
from collections.abc import Callable
```

In `BambuMQTTClient.__init__`, after `self._disconnect_timer: threading.Timer | None = None`, add:

```python
        self._status_change_callback: Callable[[PrinterStatus, PrinterStatus], None] | None = None
```

Add this method after `__init__`:

```python
    def set_status_change_callback(
        self, callback: Callable[[PrinterStatus, PrinterStatus], None] | None,
    ) -> None:
        """Register a callback invoked on every status update with (prev, new) snapshots."""
        self._status_change_callback = callback
```

Modify `_update_status` — replace the top of the method. Change:

```python
    def _update_status(self, print_info: dict) -> None:
        """Apply fields from an MQTT print report to the in-memory status."""
        with self._lock:
```

to:

```python
    def _update_status(self, print_info: dict) -> None:
        """Apply fields from an MQTT print report to the in-memory status."""
        with self._lock:
            prev_snapshot = self._status.model_copy(deep=True)
```

At the very end of `_update_status` (after the existing body, still inside the function but outside the `with self._lock:` block), add:

```python
        callback = self._status_change_callback
        if callback is not None:
            try:
                with self._lock:
                    new_snapshot = self._status.model_copy(deep=True)
                callback(prev_snapshot, new_snapshot)
            except Exception:
                logger.exception("Status change callback raised")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mqtt_callback.py tests/test_hms_parsing.py -v`
Expected: PASS (7 tests — existing HMS tests should still pass).

- [ ] **Step 5: Commit**

```bash
git add app/mqtt_client.py tests/test_mqtt_callback.py
git commit -m "Add status-change callback hook on BambuMQTTClient"
```

---

### Task 8: NotificationHub — event detection

Core state-diff engine. Emits `NotificationEvent` records for every rule listed in the spec. No APNs calls yet — that wiring comes in Task 9.

**Files:**
- Create: `app/notification_events.py`
- Create: `app/notification_hub.py`
- Create: `tests/test_notification_events.py`

- [ ] **Step 1: Write failing tests for event detection**

Create `tests/test_notification_events.py`:

```python
"""Tests for NotificationHub event detection (diff rules only)."""

from __future__ import annotations

import pytest

from app.models import (
    HMSCode, PrinterState, PrinterStatus, PrintJob,
)
from app.notification_events import EventType, NotificationEvent
from app.notification_hub import detect_events


def _status(
    state: PrinterState = PrinterState.idle,
    online: bool = True,
    progress: int = 0,
    layer: int = 0,
    remaining: int = 0,
    hms: list[HMSCode] | None = None,
) -> PrinterStatus:
    return PrinterStatus(
        id="P01", name="X1C", online=online, state=state,
        job=PrintJob(progress=progress, current_layer=layer, remaining_minutes=remaining) if progress or layer or remaining else None,
        hms_codes=hms or [],
    )


def _types(events: list[NotificationEvent]) -> list[EventType]:
    return [e.event_type for e in events]


def test_idle_to_printing_emits_print_started():
    prev = _status(PrinterState.idle)
    new = _status(PrinterState.printing, progress=1)
    assert _types(detect_events(prev, new)) == [EventType.print_started]


def test_printing_to_paused_emits_print_paused():
    prev = _status(PrinterState.printing, progress=30)
    new = _status(PrinterState.paused, progress=30)
    assert _types(detect_events(prev, new)) == [EventType.print_paused]


def test_paused_to_printing_emits_print_resumed_not_started():
    prev = _status(PrinterState.paused, progress=30)
    new = _status(PrinterState.printing, progress=30)
    assert _types(detect_events(prev, new)) == [EventType.print_resumed]


def test_printing_to_finished_emits_print_finished():
    prev = _status(PrinterState.printing, progress=99)
    new = _status(PrinterState.finished, progress=100)
    events = detect_events(prev, new)
    assert EventType.print_finished in _types(events)


def test_printing_to_cancelled_emits_print_cancelled():
    prev = _status(PrinterState.printing, progress=50)
    new = _status(PrinterState.cancelled, progress=50)
    assert _types(detect_events(prev, new)) == [EventType.print_cancelled]


def test_printing_to_error_emits_print_failed():
    prev = _status(PrinterState.printing, progress=50)
    new = _status(PrinterState.error, progress=50)
    assert _types(detect_events(prev, new)) == [EventType.print_failed]


def test_online_to_offline_while_printing_emits_printer_offline_active():
    prev = _status(PrinterState.printing, progress=50, online=True)
    new = _status(PrinterState.printing, progress=50, online=False)
    assert _types(detect_events(prev, new)) == [EventType.printer_offline_active]


def test_online_to_offline_while_idle_emits_nothing():
    prev = _status(PrinterState.idle, online=True)
    new = _status(PrinterState.idle, online=False)
    assert detect_events(prev, new) == []


def test_progress_tick_on_1pct_change():
    prev = _status(PrinterState.printing, progress=50, layer=100, remaining=60)
    new = _status(PrinterState.printing, progress=51, layer=100, remaining=60)
    assert _types(detect_events(prev, new)) == [EventType.progress_tick]


def test_progress_tick_on_layer_change():
    prev = _status(PrinterState.printing, progress=50, layer=100, remaining=60)
    new = _status(PrinterState.printing, progress=50, layer=101, remaining=60)
    assert _types(detect_events(prev, new)) == [EventType.progress_tick]


def test_progress_tick_on_5min_remaining_change():
    prev = _status(PrinterState.printing, progress=50, layer=100, remaining=60)
    new = _status(PrinterState.printing, progress=50, layer=100, remaining=54)
    assert _types(detect_events(prev, new)) == [EventType.progress_tick]


def test_no_progress_tick_for_small_change():
    prev = _status(PrinterState.printing, progress=50, layer=100, remaining=60)
    new = _status(PrinterState.printing, progress=50, layer=100, remaining=59)
    assert detect_events(prev, new) == []


def test_new_hms_code_emits_hms_warning():
    prev = _status(PrinterState.printing, progress=50)
    new = _status(
        PrinterState.printing, progress=50,
        hms=[HMSCode(attr="0300200000010001", code="0001")],
    )
    events = detect_events(prev, new)
    hms_events = [e for e in events if e.event_type == EventType.hms_warning]
    assert len(hms_events) == 1
    assert hms_events[0].hms_code == "0300200000010001"


def test_existing_hms_code_does_not_re_emit():
    prev = _status(
        PrinterState.printing, progress=50,
        hms=[HMSCode(attr="AAAA", code="BBBB")],
    )
    new = _status(
        PrinterState.printing, progress=50,
        hms=[HMSCode(attr="AAAA", code="BBBB")],
    )
    events = [e for e in detect_events(prev, new) if e.event_type == EventType.hms_warning]
    assert events == []


def test_cleared_hms_does_not_emit():
    prev = _status(
        PrinterState.printing, progress=50,
        hms=[HMSCode(attr="AAAA", code="BBBB")],
    )
    new = _status(PrinterState.printing, progress=50, hms=[])
    events = [e for e in detect_events(prev, new) if e.event_type == EventType.hms_warning]
    assert events == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_notification_events.py -v`
Expected: FAIL — modules don't exist.

- [ ] **Step 3: Implement event types**

Create `app/notification_events.py`:

```python
"""Event record types emitted by NotificationHub."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.models import PrinterStatus


class EventType(str, Enum):
    print_started = "print_started"
    print_paused = "print_paused"
    print_resumed = "print_resumed"
    print_finished = "print_finished"
    print_cancelled = "print_cancelled"
    print_failed = "print_failed"
    printer_offline_active = "printer_offline_active"
    hms_warning = "hms_warning"
    progress_tick = "progress_tick"


@dataclass
class NotificationEvent:
    event_type: EventType
    printer_id: str
    snapshot: PrinterStatus
    hms_code: str = ""  # populated for hms_warning
```

- [ ] **Step 4: Implement detect_events**

Create `app/notification_hub.py`:

```python
"""Detects notification-worthy state changes between printer snapshots."""

from __future__ import annotations

from app.models import PrinterState, PrinterStatus
from app.notification_events import EventType, NotificationEvent


_ACTIVE_STATES = {
    PrinterState.printing, PrinterState.paused, PrinterState.preparing,
}


def detect_events(
    prev: PrinterStatus, new: PrinterStatus,
) -> list[NotificationEvent]:
    events: list[NotificationEvent] = []

    if prev.state != new.state:
        transition_event = _state_transition_event(prev.state, new.state)
        if transition_event is not None:
            events.append(NotificationEvent(
                event_type=transition_event,
                printer_id=new.id,
                snapshot=new,
            ))

    # Offline-while-active
    if prev.online and not new.online and prev.state in _ACTIVE_STATES:
        events.append(NotificationEvent(
            event_type=EventType.printer_offline_active,
            printer_id=new.id,
            snapshot=new,
        ))

    # HMS: only emit for codes newly present
    prev_attrs = {c.attr for c in prev.hms_codes}
    for code in new.hms_codes:
        if code.attr not in prev_attrs:
            events.append(NotificationEvent(
                event_type=EventType.hms_warning,
                printer_id=new.id,
                snapshot=new,
                hms_code=code.attr,
            ))

    # Progress ticks — only while printing, no state transition in the same diff
    if (
        not events
        and prev.state == PrinterState.printing
        and new.state == PrinterState.printing
        and new.online
    ):
        if _is_progress_tick(prev, new):
            events.append(NotificationEvent(
                event_type=EventType.progress_tick,
                printer_id=new.id,
                snapshot=new,
            ))

    return events


def _state_transition_event(
    prev: PrinterState, new: PrinterState,
) -> EventType | None:
    if new == PrinterState.printing:
        if prev == PrinterState.paused:
            return EventType.print_resumed
        return EventType.print_started
    if new == PrinterState.paused and prev == PrinterState.printing:
        return EventType.print_paused
    if new == PrinterState.finished:
        return EventType.print_finished
    if new == PrinterState.cancelled:
        return EventType.print_cancelled
    if new == PrinterState.error:
        return EventType.print_failed
    return None


def _is_progress_tick(prev: PrinterStatus, new: PrinterStatus) -> bool:
    prev_job = prev.job
    new_job = new.job
    if new_job is None:
        return False
    prev_progress = prev_job.progress if prev_job else 0
    prev_layer = prev_job.current_layer if prev_job else 0
    prev_remaining = prev_job.remaining_minutes if prev_job else 0

    if abs(new_job.progress - prev_progress) >= 1:
        return True
    if new_job.current_layer != prev_layer:
        return True
    if abs(new_job.remaining_minutes - prev_remaining) >= 5:
        return True
    return False
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_notification_events.py -v`
Expected: PASS (15 tests).

- [ ] **Step 6: Commit**

```bash
git add app/notification_events.py app/notification_hub.py tests/test_notification_events.py
git commit -m "Add NotificationHub event detection for printer state diffs"
```

---

### Task 9: NotificationHub — dispatch, dedupe, throttle

Wrap the pure `detect_events` function in a class that owns:
- A dedicated daemon thread consuming events from a `queue.Queue`
- Per-event-key debounce (drop repeats within 10s)
- Per-printer progress-tick throttle (max 1 / 10s)
- HMS tracking state per printer (for "newly added" semantics across many snapshots)
- APNs dispatch via `ApnsClient`
- Invalid-token cleanup via `DeviceStore`

**Files:**
- Modify: `app/notification_hub.py`
- Create: `tests/test_notification_hub.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_notification_hub.py`:

```python
"""Tests for NotificationHub dispatch, dedupe, and throttle."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.apns_client import ApnsResult
from app.device_store import DeviceRecord, DeviceStore, ActiveActivity
from app.models import PrinterState, PrinterStatus, PrintJob
from app.notification_hub import NotificationHub


@dataclass
class FakeApns:
    alerts: list[dict] = field(default_factory=list)
    updates: list[dict] = field(default_factory=list)
    starts: list[dict] = field(default_factory=list)
    ends: list[dict] = field(default_factory=list)
    invalid_token: str | None = None

    async def send_alert(self, **kwargs) -> ApnsResult:
        self.alerts.append(kwargs)
        return self._result(kwargs.get("device_token"))

    async def send_live_activity_update(self, **kwargs) -> ApnsResult:
        self.updates.append(kwargs)
        return self._result(kwargs.get("activity_token"))

    async def send_live_activity_start(self, **kwargs) -> ApnsResult:
        self.starts.append(kwargs)
        return self._result(kwargs.get("start_token"))

    async def send_live_activity_end(self, **kwargs) -> ApnsResult:
        self.ends.append(kwargs)
        return self._result(kwargs.get("activity_token"))

    def _result(self, token: str | None) -> ApnsResult:
        if self.invalid_token and token == self.invalid_token:
            return ApnsResult(ok=False, status_code=410, reason="Unregistered", token_invalid=True)
        return ApnsResult(ok=True, status_code=200)


def _status(
    state: PrinterState = PrinterState.printing, progress: int = 50,
    online: bool = True,
) -> PrinterStatus:
    return PrinterStatus(
        id="P01", name="X1C", state=state, online=online,
        job=PrintJob(file_name="test.3mf", progress=progress,
                     current_layer=10, total_layers=100, remaining_minutes=30),
    )


def _make_hub(tmp_path, apns: FakeApns) -> tuple[NotificationHub, DeviceStore]:
    store = DeviceStore(tmp_path / "devices.json")
    hub = NotificationHub(apns=apns, device_store=store)
    hub.start()
    return hub, store


def _wait_for(predicate, timeout: float = 1.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timeout waiting for predicate")


def test_pause_transition_sends_alert_to_subscribed_devices(tmp_path):
    apns = FakeApns()
    hub, store = _make_hub(tmp_path, apns)
    store.upsert_device(DeviceRecord(
        id="dev", name="iPhone", device_token="tok",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    try:
        hub.on_status_change(_status(PrinterState.printing), _status(PrinterState.paused))
        _wait_for(lambda: len(apns.alerts) == 1)
        assert apns.alerts[0]["device_token"] == "tok"
        assert apns.alerts[0]["event_type"] == "print_paused"
    finally:
        hub.stop()


def test_duplicate_pause_within_10s_is_deduped(tmp_path):
    apns = FakeApns()
    hub, store = _make_hub(tmp_path, apns)
    store.upsert_device(DeviceRecord(
        id="dev", name="iPhone", device_token="tok",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    try:
        prev, new = _status(PrinterState.printing), _status(PrinterState.paused)
        hub.on_status_change(prev, new)
        hub.on_status_change(prev, new)
        _wait_for(lambda: len(apns.alerts) >= 1)
        time.sleep(0.2)
        assert len(apns.alerts) == 1
    finally:
        hub.stop()


def test_progress_tick_throttled_to_once_per_10s_per_printer(tmp_path):
    apns = FakeApns()
    hub, store = _make_hub(tmp_path, apns)
    store.upsert_device(DeviceRecord(
        id="dev", name="iPhone", device_token="tok",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    store.add_activity(ActiveActivity(
        device_id="dev", printer_id="P01", activity_update_token="act-tok",
    ))
    try:
        prev = _status(PrinterState.printing, progress=50)
        for pct in (51, 52, 53, 54, 55):
            hub.on_status_change(prev, _status(PrinterState.printing, progress=pct))
            prev = _status(PrinterState.printing, progress=pct)
        _wait_for(lambda: len(apns.updates) >= 1)
        time.sleep(0.2)
        assert len(apns.updates) == 1
    finally:
        hub.stop()


def test_invalid_token_response_is_removed_from_store(tmp_path):
    apns = FakeApns(invalid_token="tok")
    hub, store = _make_hub(tmp_path, apns)
    store.upsert_device(DeviceRecord(
        id="dev", name="iPhone", device_token="tok",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    try:
        hub.on_status_change(_status(PrinterState.printing), _status(PrinterState.paused))
        _wait_for(lambda: store.get_device("dev").device_token == "")
    finally:
        hub.stop()


def test_print_started_sends_push_to_start_when_no_activity(tmp_path):
    apns = FakeApns()
    hub, store = _make_hub(tmp_path, apns)
    store.upsert_device(DeviceRecord(
        id="dev", name="iPhone", device_token="tok",
        live_activity_start_token="start-tok", subscribed_printers=["*"],
    ))
    try:
        hub.on_status_change(
            _status(PrinterState.idle, progress=0),
            _status(PrinterState.printing, progress=1),
        )
        _wait_for(lambda: len(apns.starts) == 1)
        assert apns.starts[0]["start_token"] == "start-tok"
    finally:
        hub.stop()


def test_print_started_skips_push_to_start_when_activity_exists(tmp_path):
    apns = FakeApns()
    hub, store = _make_hub(tmp_path, apns)
    store.upsert_device(DeviceRecord(
        id="dev", name="iPhone", device_token="tok",
        live_activity_start_token="start-tok", subscribed_printers=["*"],
    ))
    store.add_activity(ActiveActivity(
        device_id="dev", printer_id="P01", activity_update_token="act",
    ))
    try:
        hub.on_status_change(
            _status(PrinterState.idle, progress=0),
            _status(PrinterState.printing, progress=1),
        )
        time.sleep(0.2)
        assert apns.starts == []
    finally:
        hub.stop()


def test_terminal_state_ends_live_activity(tmp_path):
    apns = FakeApns()
    hub, store = _make_hub(tmp_path, apns)
    store.upsert_device(DeviceRecord(
        id="dev", name="iPhone", device_token="tok",
        live_activity_start_token=None, subscribed_printers=["*"],
    ))
    store.add_activity(ActiveActivity(
        device_id="dev", printer_id="P01", activity_update_token="act",
    ))
    try:
        hub.on_status_change(
            _status(PrinterState.printing, progress=99),
            _status(PrinterState.finished, progress=100),
        )
        _wait_for(lambda: len(apns.ends) == 1)
        assert apns.ends[0]["activity_token"] == "act"
        # And removed from store
        assert store.list_activities_for_printer("P01") == []
    finally:
        hub.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_notification_hub.py -v`
Expected: FAIL — `NotificationHub` class does not exist (only the free function `detect_events`).

- [ ] **Step 3: Implement NotificationHub**

Modify `app/notification_hub.py`. Keep the existing `detect_events` function and `_state_transition_event` / `_is_progress_tick` helpers. Append at the bottom:

```python
import asyncio
import logging
import queue
import threading
import time
from typing import Protocol

from app.apns_client import ApnsResult
from app.device_store import DeviceStore
from app.models import PrinterState
from app.notification_events import EventType, NotificationEvent

logger = logging.getLogger(__name__)

_DEDUPE_SECONDS = 30.0  # also covers the "error oscillation" case from the spec
_PROGRESS_THROTTLE_SECONDS = 10.0
_TERMINAL_STATES = {
    PrinterState.finished, PrinterState.cancelled, PrinterState.error,
}


class _ApnsProtocol(Protocol):
    async def send_alert(self, **kwargs) -> ApnsResult: ...
    async def send_live_activity_update(self, **kwargs) -> ApnsResult: ...
    async def send_live_activity_start(self, **kwargs) -> ApnsResult: ...
    async def send_live_activity_end(self, **kwargs) -> ApnsResult: ...


_ALERT_COPY: dict[EventType, tuple[str, str]] = {
    EventType.print_paused: ("Print paused", "{printer} paused on layer {layer}"),
    EventType.print_failed: ("Print failed", "{printer} stopped with an error"),
    EventType.print_cancelled: ("Print cancelled", "{printer} cancelled"),
    EventType.print_finished: ("Print complete", "{printer} finished {file}"),
    EventType.printer_offline_active: ("Printer offline", "{printer} lost connection during a print"),
    EventType.hms_warning: ("Printer warning", "{printer} reported code {code}"),
}


class NotificationHub:
    """Serialises event detection + APNs dispatch on a background thread."""

    def __init__(self, apns: _ApnsProtocol, device_store: DeviceStore) -> None:
        self._apns = apns
        self._store = device_store
        self._queue: queue.Queue[NotificationEvent | None] = queue.Queue()
        self._dedupe: dict[tuple[str, str], float] = {}
        self._last_progress: dict[str, float] = {}
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, name="notification-hub", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    def on_status_change(self, prev, new) -> None:
        """Invoked from the MQTT thread. Enqueues detected events."""
        try:
            for event in detect_events(prev, new):
                self._queue.put(event)
        except Exception:
            logger.exception("detect_events raised")

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            while self._running:
                event = self._queue.get()
                if event is None:
                    break
                try:
                    self._loop.run_until_complete(self._handle(event))
                except Exception:
                    logger.exception("NotificationHub handler failed")
        finally:
            self._loop.close()
            self._loop = None

    async def _handle(self, event: NotificationEvent) -> None:
        if self._is_deduped(event):
            return
        if event.event_type == EventType.progress_tick and self._is_throttled(event):
            return

        if event.event_type == EventType.progress_tick:
            await self._dispatch_live_activity_update(event)
            return

        # State-transition events: alert + live activity handling combined
        await self._dispatch_event(event)

    def _is_deduped(self, event: NotificationEvent) -> bool:
        now = time.monotonic()
        # Prune old entries opportunistically
        expired = [k for k, ts in self._dedupe.items() if now - ts > _DEDUPE_SECONDS]
        for k in expired:
            del self._dedupe[k]
        if event.event_type == EventType.progress_tick:
            return False  # throttled separately
        key = (event.printer_id, event.event_type.value + ":" + event.hms_code)
        if key in self._dedupe:
            return True
        self._dedupe[key] = now
        return False

    def _is_throttled(self, event: NotificationEvent) -> bool:
        now = time.monotonic()
        last = self._last_progress.get(event.printer_id, 0.0)
        if now - last < _PROGRESS_THROTTLE_SECONDS:
            return True
        self._last_progress[event.printer_id] = now
        return False

    async def _dispatch_event(self, event: NotificationEvent) -> None:
        # Alerts
        if event.event_type in _ALERT_COPY:
            await self._send_alerts(event)

        # Live Activity lifecycle
        if event.event_type == EventType.print_started:
            await self._send_push_to_start(event)
        elif event.event_type in _TERMINAL_STATES_EVENT_TYPES:
            await self._send_live_activity_end(event)
        elif event.event_type in {
            EventType.print_paused, EventType.print_resumed,
            EventType.printer_offline_active,
        }:
            await self._dispatch_live_activity_update(event)

    async def _send_alerts(self, event: NotificationEvent) -> None:
        title_tpl, body_tpl = _ALERT_COPY[event.event_type]
        subscribers = self._store.subscribers_for_printer(event.printer_id)
        layer = event.snapshot.job.current_layer if event.snapshot.job else 0
        file_name = event.snapshot.job.file_name if event.snapshot.job else ""
        title = title_tpl.format(printer=event.snapshot.name)
        body = body_tpl.format(
            printer=event.snapshot.name,
            layer=layer, file=file_name, code=event.hms_code,
        )
        for dev in subscribers:
            if not dev.device_token:
                continue
            result = await self._apns.send_alert(
                device_token=dev.device_token,
                title=title, body=body,
                event_type=event.event_type.value,
                printer_id=event.printer_id,
            )
            self._handle_result(result, dev.device_token)

    async def _send_push_to_start(self, event: NotificationEvent) -> None:
        snapshot = event.snapshot
        subscribers = self._store.subscribers_for_printer(event.printer_id)
        existing_device_ids = {
            a.device_id for a in self._store.list_activities_for_printer(event.printer_id)
        }
        attributes = {
            "printerId": snapshot.id,
            "printerName": snapshot.name,
            "fileName": snapshot.job.file_name if snapshot.job else "",
            "thumbnailData": None,  # set by iOS when started locally; gateway has no thumbnail cached here
        }
        content = _content_state_from(snapshot)
        for dev in subscribers:
            if dev.id in existing_device_ids:
                continue  # iOS app already created a local activity
            if not dev.live_activity_start_token:
                continue
            result = await self._apns.send_live_activity_start(
                start_token=dev.live_activity_start_token,
                attributes_type="PrintActivityAttributes",
                attributes=attributes,
                content_state=content,
            )
            self._handle_result(result, dev.live_activity_start_token)

    async def _dispatch_live_activity_update(self, event: NotificationEvent) -> None:
        activities = self._store.list_activities_for_printer(event.printer_id)
        content = _content_state_from(event.snapshot)
        for act in activities:
            result = await self._apns.send_live_activity_update(
                activity_token=act.activity_update_token, content_state=content,
            )
            self._handle_result(result, act.activity_update_token)

    async def _send_live_activity_end(self, event: NotificationEvent) -> None:
        activities = self._store.list_activities_for_printer(event.printer_id)
        content = _content_state_from(event.snapshot)
        dismissal = 4 * 3600 if event.event_type == EventType.print_finished else 0
        for act in activities:
            result = await self._apns.send_live_activity_end(
                activity_token=act.activity_update_token,
                content_state=content,
                dismissal_seconds_from_now=dismissal,
            )
            self._handle_result(result, act.activity_update_token)
            self._store.remove_activity(act.device_id, act.printer_id)

    def _handle_result(self, result: ApnsResult, token: str) -> None:
        if result.token_invalid and token:
            self._store.invalidate_token(token)


_TERMINAL_STATES_EVENT_TYPES = {
    EventType.print_finished, EventType.print_cancelled, EventType.print_failed,
}


def _content_state_from(status) -> dict:
    job = status.job
    return {
        "state": status.state.value,
        "stageName": status.stage_name or "",
        "progress": (job.progress / 100.0) if job else 0.0,
        "remainingMinutes": job.remaining_minutes if job else 0,
        "currentLayer": job.current_layer if job else 0,
        "totalLayers": job.total_layers if job else 0,
        "updatedAt": int(time.time()),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_notification_hub.py tests/test_notification_events.py -v`
Expected: PASS (22 tests).

- [ ] **Step 5: Commit**

```bash
git add app/notification_hub.py tests/test_notification_hub.py
git commit -m "Dispatch APNs pushes with dedupe and throttle"
```

---

### Task 10: Wire NotificationHub into PrinterService + app lifespan

Install the hub so every `BambuMQTTClient` routes state changes into it. Tears down cleanly on shutdown. Handles `sync_printers()` hot-reloads.

**Files:**
- Modify: `app/printer_service.py`
- Modify: `app/main.py`

- [ ] **Step 1: Add hub wiring to PrinterService**

Modify `app/printer_service.py`. Update `__init__`:

```python
    def __init__(
        self,
        printer_configs: list[PrinterConfig],
        status_change_callback=None,
    ) -> None:
        self._configs: dict[str, PrinterConfig] = {}
        self._clients: dict[str, BambuMQTTClient] = {}
        self._status_change_callback = status_change_callback
        for cfg in printer_configs:
            self._configs[cfg.serial] = cfg
            client = BambuMQTTClient(cfg)
            if status_change_callback is not None:
                client.set_status_change_callback(status_change_callback)
            self._clients[cfg.serial] = client
```

In `sync_printers`, replace the `to_add` loop and the reset branch inside `to_check` to install the callback on every newly constructed client. Find the existing lines:

```python
                self._clients[serial].stop()
                self._configs[serial] = new
                self._clients[serial] = BambuMQTTClient(new)
```

Replace with:

```python
                self._clients[serial].stop()
                self._configs[serial] = new
                new_client = BambuMQTTClient(new)
                if self._status_change_callback is not None:
                    new_client.set_status_change_callback(self._status_change_callback)
                self._clients[serial] = new_client
```

And find:

```python
        for serial in to_add:
            cfg = new_by_serial[serial]
            logger.info("Adding printer %s", serial)
            self._configs[serial] = cfg
            self._clients[serial] = BambuMQTTClient(cfg)
```

Replace with:

```python
        for serial in to_add:
            cfg = new_by_serial[serial]
            logger.info("Adding printer %s", serial)
            self._configs[serial] = cfg
            client = BambuMQTTClient(cfg)
            if self._status_change_callback is not None:
                client.set_status_change_callback(self._status_change_callback)
            self._clients[serial] = client
```

- [ ] **Step 2: Wire lifespan in main.py**

Open `app/main.py`. Locate the lifespan context manager (search for `@asynccontextmanager` or `lifespan`). Near where `PrinterService` is constructed, add imports and wiring.

At the top of the file, add:

```python
from app.apns_client import ApnsClient
from app.apns_jwt import ApnsJwtSigner
from app.config import settings
from app.device_store import DeviceStore
from app.notification_hub import NotificationHub
```

In the lifespan function, after loading printer configs and before constructing `PrinterService`, add:

```python
    # Device registry + APNs
    device_store_path = Path(args.config).parent / "devices.json" if args.config else Path("devices.json")
    device_store = DeviceStore(device_store_path)

    apns_client = None
    notification_hub = None
    status_change_callback = None
    if settings.push_enabled:
        signer = ApnsJwtSigner(
            key_path=settings.apns_key_path,
            key_id=settings.apns_key_id,
            team_id=settings.apns_team_id,
        )
        apns_client = ApnsClient(
            signer=signer,
            bundle_id=settings.apns_bundle_id,
            environment=settings.apns_environment,
        )
        notification_hub = NotificationHub(apns=apns_client, device_store=device_store)
        notification_hub.start()
        status_change_callback = notification_hub.on_status_change
        logger.info("APNs push enabled")
    else:
        logger.info("APNs push disabled — set APNS_KEY_PATH and related vars to enable")
```

Change the `PrinterService` construction to:

```python
    printer_service = PrinterService(
        printer_configs, status_change_callback=status_change_callback,
    )
```

Store the new objects on the `app.state` so route handlers can access them:

```python
    app.state.device_store = device_store
    app.state.notification_hub = notification_hub
    app.state.apns_client = apns_client
```

In the shutdown portion of lifespan (after `yield`), add:

```python
    if notification_hub is not None:
        notification_hub.stop()
    if apns_client is not None:
        await apns_client.aclose()
```

- [ ] **Step 3: Verify app still starts**

Run: `python -m app &` then `curl http://localhost:4844/api/printers` then kill the server.
Expected: request succeeds (200 OK with current printer list), no exceptions on startup.
Also verify the log contains either `"APNs push enabled"` or `"APNs push disabled"`.

- [ ] **Step 4: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass; no regressions.

- [ ] **Step 5: Commit**

```bash
git add app/printer_service.py app/main.py
git commit -m "Wire NotificationHub into printer service lifespan"
```

---

### Task 11: New gateway endpoints

Add `/api/capabilities`, `/api/devices/register`, `DELETE /api/devices/{id}`, `POST /api/devices/{id}/activities`, `DELETE /api/devices/{id}/activities/{printer_id}`.

**Files:**
- Modify: `app/models.py`
- Modify: `app/main.py`
- Create: `tests/test_device_endpoints.py`

- [ ] **Step 1: Add API models**

Modify `app/models.py`. Append:

```python
# --- Push / Live Activity models ---


class CapabilitiesResponse(BaseModel):
    push: bool
    live_activities: bool


class DeviceRegisterRequest(BaseModel):
    id: str
    name: str = ""
    device_token: str
    live_activity_start_token: str | None = None
    subscribed_printers: list[str] = ["*"]


class DeviceRegisterResponse(BaseModel):
    status: str = "ok"


class ActivityRegisterRequest(BaseModel):
    printer_id: str
    activity_update_token: str


class ActivityRegisterResponse(BaseModel):
    status: str = "ok"
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_device_endpoints.py`:

```python
"""Integration tests for the new device registry endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point the gateway at a clean tmp dir for printers.json + devices.json
    monkeypatch.setenv("APNS_KEY_PATH", "")
    monkeypatch.chdir(tmp_path)
    # Import inside the fixture to pick up the env
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_capabilities_reports_push_disabled(client):
    res = client.get("/api/capabilities")
    assert res.status_code == 200
    body = res.json()
    assert body == {"push": False, "live_activities": False}


def test_device_register_upsert_then_delete(client):
    body = {
        "id": "dev-1", "name": "iPhone",
        "device_token": "tok-a", "subscribed_printers": ["*"],
    }
    res = client.post("/api/devices/register", json=body)
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # Upsert with new token
    body["device_token"] = "tok-b"
    res = client.post("/api/devices/register", json=body)
    assert res.status_code == 200

    res = client.delete("/api/devices/dev-1")
    assert res.status_code == 200


def test_activity_register_requires_known_device(client):
    res = client.post(
        "/api/devices/dev-unknown/activities",
        json={"printer_id": "P01", "activity_update_token": "tok"},
    )
    assert res.status_code == 404


def test_activity_register_and_delete(client):
    client.post("/api/devices/register", json={
        "id": "dev-1", "name": "iPhone", "device_token": "tok",
    })
    res = client.post(
        "/api/devices/dev-1/activities",
        json={"printer_id": "P01", "activity_update_token": "upd"},
    )
    assert res.status_code == 200
    res = client.delete("/api/devices/dev-1/activities/P01")
    assert res.status_code == 200
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_device_endpoints.py -v`
Expected: FAIL — endpoints don't exist (likely 404s).

- [ ] **Step 4: Add the endpoints**

Modify `app/main.py`. Add imports at the top near other model imports:

```python
from app.device_store import DeviceRecord, ActiveActivity
from app.models import (
    ActivityRegisterRequest, ActivityRegisterResponse,
    CapabilitiesResponse, DeviceRegisterRequest, DeviceRegisterResponse,
)
```

Add the endpoints (place them alongside other `/api/...` routes):

```python
@app.get("/api/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities():
    return CapabilitiesResponse(
        push=settings.push_enabled,
        live_activities=settings.push_enabled,
    )


@app.post("/api/devices/register", response_model=DeviceRegisterResponse)
async def register_device(body: DeviceRegisterRequest):
    store: DeviceStore = app.state.device_store
    store.upsert_device(DeviceRecord(
        id=body.id,
        name=body.name,
        device_token=body.device_token,
        live_activity_start_token=body.live_activity_start_token,
        subscribed_printers=body.subscribed_printers or ["*"],
    ))
    return DeviceRegisterResponse()


@app.delete("/api/devices/{device_id}")
async def unregister_device(device_id: str):
    store: DeviceStore = app.state.device_store
    store.remove_device(device_id)
    return {"status": "ok"}


@app.post(
    "/api/devices/{device_id}/activities",
    response_model=ActivityRegisterResponse,
)
async def register_activity(device_id: str, body: ActivityRegisterRequest):
    store: DeviceStore = app.state.device_store
    if store.get_device(device_id) is None:
        raise HTTPException(status_code=404, detail="device not found")
    store.add_activity(ActiveActivity(
        device_id=device_id,
        printer_id=body.printer_id,
        activity_update_token=body.activity_update_token,
    ))
    return ActivityRegisterResponse()


@app.delete("/api/devices/{device_id}/activities/{printer_id}")
async def unregister_activity(device_id: str, printer_id: str):
    store: DeviceStore = app.state.device_store
    store.remove_activity(device_id, printer_id)
    return {"status": "ok"}
```

If `HTTPException` is not already imported in `main.py`, add:

```python
from fastapi import HTTPException
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_device_endpoints.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Run full test suite**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/models.py app/main.py tests/test_device_endpoints.py
git commit -m "Add device registry and capabilities endpoints"
```

---

**End of Phase 1 (Gateway).** At this point the gateway has:
- Optional APNs configuration with graceful disable
- HMS parsing
- State-change callback hook
- Event detection + dispatch with dedupe / throttle
- Device registry endpoints

Running without APNs configured → all existing behavior preserved; `/api/capabilities` returns `{"push": false, "live_activities": false}`.

---

## Phase 2: iOS

All Phase 2 work lives in `bambu-gateway-ios` on the `live-activities-push` branch.

### Task 12: Shared ActivityAttributes

Define the `PrintActivityAttributes` type consumed by both the app and the Widget Extension target.

**Files:**
- Create: `BambuGateway/Models/PrintActivityAttributes.swift`

- [ ] **Step 1: Create the shared attributes type**

Create `BambuGateway/Models/PrintActivityAttributes.swift`:

```swift
import ActivityKit
import Foundation

enum PrinterStateBadge: String, Codable, Hashable {
    case idle
    case preparing
    case printing
    case paused
    case finished
    case cancelled
    case error
    case offline
}

struct PrintActivityAttributes: ActivityAttributes {
    // Static, set once at Activity creation
    let printerId: String
    let printerName: String
    let fileName: String
    let thumbnailData: Data?

    struct ContentState: Codable, Hashable {
        var state: PrinterStateBadge
        var stageName: String?
        var progress: Double          // 0.0-1.0
        var remainingMinutes: Int
        var currentLayer: Int
        var totalLayers: Int
        var updatedAt: Date
    }
}
```

- [ ] **Step 2: Commit**

```bash
git add BambuGateway/Models/PrintActivityAttributes.swift
git commit -m "Add shared PrintActivityAttributes for Live Activity"
```

---

### Task 13: LiveActivityExtension target

Create the Widget Extension target that renders the Live Activity. XcodeGen is the source of truth; edit `project.yml`, regenerate, then fill in the widget.

**Files:**
- Modify: `project.yml`
- Create: `LiveActivityExtension/Info.plist`
- Create: `LiveActivityExtension/PrintLiveActivity.swift`
- Create: `LiveActivityExtension/LiveActivityBundle.swift`
- Modify: `Configuration/Base.xcconfig`

- [ ] **Step 1: Add bundle ID variable**

Modify `Configuration/Base.xcconfig` — add (near `SHARE_EXTENSION_BUNDLE_ID` if it exists, or at the bottom):

```
LIVE_ACTIVITY_BUNDLE_ID = $(APP_BUNDLE_ID).LiveActivity
```

- [ ] **Step 2: Create the extension Info.plist**

Create `LiveActivityExtension/Info.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>NSExtension</key>
    <dict>
        <key>NSExtensionPointIdentifier</key>
        <string>com.apple.widgetkit-extension</string>
    </dict>
</dict>
</plist>
```

- [ ] **Step 3: Add the target to project.yml**

Modify `project.yml`. After the `ShareExtension` target definition (inside the `targets:` mapping), append:

```yaml
  LiveActivityExtension:
    type: app-extension
    platform: iOS
    deploymentTarget: '18.0'
    configFiles:
      Debug: Configuration/Base.xcconfig
      Release: Configuration/Base.xcconfig
    sources:
      - path: LiveActivityExtension
      - path: BambuGateway/Models/PrintActivityAttributes.swift
    settings:
      base:
        PRODUCT_NAME: LiveActivityExtension
        PRODUCT_BUNDLE_IDENTIFIER: $(LIVE_ACTIVITY_BUNDLE_ID)
        DEVELOPMENT_TEAM: $(DEVELOPMENT_TEAM)
        INFOPLIST_FILE: LiveActivityExtension/Info.plist
        GENERATE_INFOPLIST_FILE: NO
        CODE_SIGN_STYLE: Automatic
        SWIFT_VERSION: 5.0
```

In the `BambuGateway` target's `dependencies` list, add:

```yaml
      - target: LiveActivityExtension
```

In the `BambuGateway` scheme's `build.targets` mapping, add:

```yaml
        LiveActivityExtension: [build]
```

- [ ] **Step 4: Create the widget bundle entry**

Create `LiveActivityExtension/LiveActivityBundle.swift`:

```swift
import SwiftUI
import WidgetKit

@main
struct LiveActivityBundle: WidgetBundle {
    var body: some Widget {
        PrintLiveActivity()
    }
}
```

- [ ] **Step 5: Create the Live Activity widget**

Create `LiveActivityExtension/PrintLiveActivity.swift`:

```swift
import ActivityKit
import SwiftUI
import WidgetKit

struct PrintLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: PrintActivityAttributes.self) { context in
            lockScreenView(context: context)
                .padding()
                .activityBackgroundTint(Color.black.opacity(0.85))
                .activitySystemActionForegroundColor(.white)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    thumbnail(data: context.attributes.thumbnailData, size: 36)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text("\(Int(context.state.progress * 100))%")
                        .font(.headline.monospacedDigit())
                }
                DynamicIslandExpandedRegion(.center) {
                    Text(context.attributes.printerName)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(alignment: .leading, spacing: 4) {
                        ProgressView(value: context.state.progress)
                        Text(statusLine(context: context))
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            } compactLeading: {
                Image(systemName: iconName(for: context.state.state))
            } compactTrailing: {
                Text("\(Int(context.state.progress * 100))%")
                    .monospacedDigit()
            } minimal: {
                Image(systemName: iconName(for: context.state.state))
            }
        }
    }

    private func lockScreenView(context: ActivityViewContext<PrintActivityAttributes>) -> some View {
        HStack(alignment: .top, spacing: 12) {
            thumbnail(data: context.attributes.thumbnailData, size: 56)
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text(context.attributes.printerName)
                        .font(.headline)
                    Spacer()
                    Text("\(Int(context.state.progress * 100))%")
                        .font(.headline.monospacedDigit())
                }
                if !context.attributes.fileName.isEmpty {
                    Text(context.attributes.fileName)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                ProgressView(value: context.state.progress)
                    .tint(.white)
                Text(statusLine(context: context))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private func thumbnail(data: Data?, size: CGFloat) -> some View {
        if let data, let image = UIImage(data: data) {
            Image(uiImage: image)
                .resizable()
                .aspectRatio(contentMode: .fill)
                .frame(width: size, height: size)
                .clipShape(RoundedRectangle(cornerRadius: 8))
        } else {
            RoundedRectangle(cornerRadius: 8)
                .fill(Color.white.opacity(0.1))
                .frame(width: size, height: size)
                .overlay(Image(systemName: "printer.fill").foregroundStyle(.white.opacity(0.6)))
        }
    }

    private func statusLine(context: ActivityViewContext<PrintActivityAttributes>) -> String {
        let state = context.state
        if let stage = state.stageName, !stage.isEmpty, state.state == .preparing {
            return stage
        }
        switch state.state {
        case .paused: return "Paused"
        case .offline: return "Printer offline"
        case .error: return "Error"
        case .finished: return "Complete"
        case .cancelled: return "Cancelled"
        default:
            var parts: [String] = []
            if state.totalLayers > 0 {
                parts.append("Layer \(state.currentLayer)/\(state.totalLayers)")
            }
            if state.remainingMinutes > 0 {
                parts.append("\(state.remainingMinutes) min left")
            }
            return parts.joined(separator: " · ")
        }
    }

    private func iconName(for state: PrinterStateBadge) -> String {
        switch state {
        case .printing, .preparing: return "printer.fill"
        case .paused: return "pause.circle.fill"
        case .finished: return "checkmark.circle.fill"
        case .cancelled: return "xmark.circle.fill"
        case .error: return "exclamationmark.triangle.fill"
        case .offline: return "wifi.slash"
        case .idle: return "printer"
        }
    }
}
```

- [ ] **Step 6: Regenerate the Xcode project**

Run: `xcodegen generate`
Expected: project regenerates with the new target; no errors.

- [ ] **Step 7: Build the extension**

Run:
```
xcodebuild -project BambuGateway.xcodeproj -scheme BambuGateway -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build
```
Expected: build succeeds.

- [ ] **Step 8: Commit**

```bash
git add project.yml Configuration/Base.xcconfig LiveActivityExtension/
git commit -m "Add LiveActivityExtension widget target"
```

---

### Task 14: Entitlements for push + background remote notifications

The main app needs `aps-environment` and the `remote-notification` background mode.

**Files:**
- Modify: `project.yml`
- Create: `BambuGateway/Support/BambuGateway.entitlements`
- Modify: `BambuGateway/Support/Info.plist`

- [ ] **Step 1: Create entitlements file**

Create `BambuGateway/Support/BambuGateway.entitlements`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>aps-environment</key>
    <string>development</string>
</dict>
</plist>
```

(Production signing flips this to `production` via the xcconfig automatically when archiving with a production provisioning profile.)

- [ ] **Step 2: Reference the entitlements from project.yml**

Modify `project.yml`. In the `BambuGateway` target `settings.base` block, add:

```yaml
        CODE_SIGN_ENTITLEMENTS: BambuGateway/Support/BambuGateway.entitlements
```

- [ ] **Step 3: Add background mode to Info.plist**

Read the existing `BambuGateway/Support/Info.plist`, then add a `UIBackgroundModes` array containing `remote-notification`. If the key already exists, add `remote-notification` to the array.

Snippet to add (nest inside the top-level `<dict>`):

```xml
    <key>UIBackgroundModes</key>
    <array>
        <string>remote-notification</string>
    </array>
```

- [ ] **Step 4: Regenerate**

Run: `xcodegen generate`
Expected: no errors.

- [ ] **Step 5: Build**

Run:
```
xcodebuild -project BambuGateway.xcodeproj -scheme BambuGateway -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build
```
Expected: build succeeds.

- [ ] **Step 6: Commit**

```bash
git add project.yml BambuGateway/Support/BambuGateway.entitlements BambuGateway/Support/Info.plist
git commit -m "Add push + remote-notification entitlements for main app"
```

---

### Task 15: GatewayClient — new endpoints

Add bindings for `/api/capabilities`, `/api/devices/register`, `DELETE /api/devices/{id}`, and the activity endpoints.

**Files:**
- Modify: `BambuGateway/Networking/GatewayClient.swift`
- Modify: `BambuGateway/Models/GatewayModels.swift`

- [ ] **Step 1: Add request/response models**

Modify `BambuGateway/Models/GatewayModels.swift`. Append:

```swift
struct GatewayCapabilities: Codable {
    let push: Bool
    let liveActivities: Bool

    enum CodingKeys: String, CodingKey {
        case push
        case liveActivities = "live_activities"
    }
}

struct DeviceRegisterPayload: Codable {
    let id: String
    let name: String
    let deviceToken: String
    let liveActivityStartToken: String?
    let subscribedPrinters: [String]

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case deviceToken = "device_token"
        case liveActivityStartToken = "live_activity_start_token"
        case subscribedPrinters = "subscribed_printers"
    }
}

struct ActivityRegisterPayload: Codable {
    let printerId: String
    let activityUpdateToken: String

    enum CodingKeys: String, CodingKey {
        case printerId = "printer_id"
        case activityUpdateToken = "activity_update_token"
    }
}
```

- [ ] **Step 2: Add client methods**

Modify `BambuGateway/Networking/GatewayClient.swift`. Add new methods on `GatewayClient`:

```swift
    func fetchCapabilities() async throws -> GatewayCapabilities {
        try await get(path: "/api/capabilities")
    }

    func registerDevice(_ payload: DeviceRegisterPayload) async throws {
        let _: [String: String] = try await post(
            path: "/api/devices/register",
            body: payload,
        )
    }

    func unregisterDevice(id: String) async throws {
        try await delete(path: "/api/devices/\(id)")
    }

    func registerActivity(
        deviceId: String, payload: ActivityRegisterPayload,
    ) async throws {
        let _: [String: String] = try await post(
            path: "/api/devices/\(deviceId)/activities",
            body: payload,
        )
    }

    func unregisterActivity(
        deviceId: String, printerId: String,
    ) async throws {
        try await delete(path: "/api/devices/\(deviceId)/activities/\(printerId)")
    }
```

If `GatewayClient` does not already have generic `post` / `delete` helpers, add them alongside the existing `get`:

```swift
    private func post<B: Encodable, R: Decodable>(
        path: String, body: B,
    ) async throws -> R {
        guard let url = URL(string: baseURLString + path) else {
            throw GatewayClientError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        request.httpBody = try encoder.encode(body)
        let (data, response) = try await session.data(for: request)
        try Self.validate(response: response)
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        do {
            return try decoder.decode(R.self, from: data)
        } catch {
            throw GatewayClientError.decodeError
        }
    }

    private func delete(path: String) async throws {
        guard let url = URL(string: baseURLString + path) else {
            throw GatewayClientError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "DELETE"
        let (_, response) = try await session.data(for: request)
        try Self.validate(response: response)
    }

    private static func validate(response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else {
            throw GatewayClientError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            throw GatewayClientError.serverError("HTTP \(http.statusCode)")
        }
    }
```

If the existing code already has a different `post` shape, use it instead — the key is new methods wrapping the new endpoints.

- [ ] **Step 3: Build**

Run:
```
xcodebuild -project BambuGateway.xcodeproj -scheme BambuGateway -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build
```
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add BambuGateway/Networking/GatewayClient.swift BambuGateway/Models/GatewayModels.swift
git commit -m "Extend GatewayClient with capabilities and device endpoints"
```

---

### Task 16: PushService

Owns device token + Live Activity push-to-start token, syncs both to the gateway.

**Files:**
- Create: `BambuGateway/Services/PushService.swift`
- Modify: `BambuGateway/App/BambuGatewayApp.swift`

- [ ] **Step 1: Create PushService**

Create `BambuGateway/Services/PushService.swift`:

```swift
import ActivityKit
import Foundation
import UIKit
import UserNotifications

@MainActor
final class PushService {
    private let client: GatewayClient
    private let deviceIdDefaultsKey = "PushService.deviceId"

    private(set) var deviceToken: String?
    private(set) var liveActivityStartToken: String?
    private(set) var capabilitiesEnabled = false

    init(client: GatewayClient) {
        self.client = client
    }

    var deviceId: String {
        if let existing = UserDefaults.standard.string(forKey: deviceIdDefaultsKey) {
            return existing
        }
        let id = "ios-\(UUID().uuidString)"
        UserDefaults.standard.set(id, forKey: deviceIdDefaultsKey)
        return id
    }

    func bootstrap() async {
        do {
            let caps = try await client.fetchCapabilities()
            capabilitiesEnabled = caps.push
            guard capabilitiesEnabled else { return }
        } catch {
            capabilitiesEnabled = false
            return
        }

        // Request notification permission and register for remote notifications
        let center = UNUserNotificationCenter.current()
        let granted = (try? await center.requestAuthorization(options: [.alert, .sound, .badge])) ?? false
        guard granted else { return }

        await UIApplication.shared.registerForRemoteNotifications()

        // Observe push-to-start token updates
        Task {
            for await tokenData in Activity<PrintActivityAttributes>.pushToStartTokenUpdates {
                await handlePushToStartToken(tokenData)
            }
        }
    }

    func handleAPNsDeviceToken(_ tokenData: Data) async {
        let token = tokenData.map { String(format: "%02x", $0) }.joined()
        deviceToken = token
        await registerIfReady()
    }

    private func handlePushToStartToken(_ tokenData: Data) async {
        let token = tokenData.map { String(format: "%02x", $0) }.joined()
        liveActivityStartToken = token
        await registerIfReady()
    }

    private func registerIfReady() async {
        guard capabilitiesEnabled, let deviceToken else { return }
        let payload = DeviceRegisterPayload(
            id: deviceId,
            name: UIDevice.current.name,
            deviceToken: deviceToken,
            liveActivityStartToken: liveActivityStartToken,
            subscribedPrinters: ["*"],
        )
        do {
            try await client.registerDevice(payload)
        } catch {
            // Fail silently; retry on next launch
        }
    }
}
```

- [ ] **Step 2: Create an AppDelegate to capture the APNs device token**

Create `BambuGateway/App/AppDelegate.swift`:

```swift
import UIKit

final class AppDelegate: NSObject, UIApplicationDelegate {
    static var pushService: PushService?

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data,
    ) {
        Task { @MainActor in
            await Self.pushService?.handleAPNsDeviceToken(deviceToken)
        }
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error,
    ) {
        // Ignore — push just won't work this session
    }
}
```

- [ ] **Step 3: Wire AppDelegate + PushService into the app**

Modify `BambuGateway/App/BambuGatewayApp.swift`. Replace the struct:

```swift
import Combine
import SwiftUI

@main
struct BambuGatewayApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var viewModel = AppViewModel()
    @Environment(\.scenePhase) private var scenePhase

    private let refreshTimer = Timer.publish(every: 30, on: .main, in: .common).autoconnect()

    var body: some Scene {
        WindowGroup {
            ContentView(viewModel: viewModel)
                .task {
                    await viewModel.refreshAll()
                    await viewModel.bootstrapPushServices()
                }
                .onOpenURL { url in
                    if url.scheme == "bambugateway",
                       let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
                       let urlString = components.queryItems?.first(where: { $0.name == "url" })?.value,
                       let webURL = URL(string: urlString) {
                        viewModel.openMakerWorldBrowser(url: webURL)
                    } else {
                        Task {
                            await viewModel.import3MF(from: url)
                        }
                    }
                }
                .onChange(of: scenePhase) { _, newPhase in
                    if newPhase == .active {
                        Task {
                            await viewModel.refreshAll()
                        }
                    }
                }
                .onReceive(refreshTimer) { _ in
                    guard scenePhase == .active else { return }
                    Task {
                        await viewModel.refreshPrinters()
                    }
                }
        }
    }
}
```

Note the new `.task { await viewModel.bootstrapPushServices() }` call — the next task adds that method.

- [ ] **Step 4: Build**

Build will fail until Task 17 wires `bootstrapPushServices` on `AppViewModel`. That's expected; we'll verify after Task 17.

- [ ] **Step 5: Commit**

```bash
git add BambuGateway/Services/PushService.swift BambuGateway/App/AppDelegate.swift BambuGateway/App/BambuGatewayApp.swift
git commit -m "Add PushService and AppDelegate for remote notifications"
```

---

### Task 17: LiveActivityService + wire into AppViewModel

Starts / ends `Activity<PrintActivityAttributes>` locally when the user prints from the app, registers update tokens with the gateway, and ends activities on polled terminal states.

**Files:**
- Create: `BambuGateway/Services/LiveActivityService.swift`
- Create: `BambuGateway/Services/NotificationService.swift`
- Modify: `BambuGateway/App/AppViewModel.swift`

- [ ] **Step 0: Read the existing AppViewModel**

Run: `cat BambuGateway/App/AppViewModel.swift`
Read the file end-to-end. Note:
- the exact name of the method that submits a print to the gateway
- the shape of `Imported3MFFile` (check `BambuGateway/Models/` — look for `thumbnailData` or equivalent)
- the exact property names on `PrinterStatus` (`name`, `state`, `job.progress`, etc. — they must match what the code patches reference)
- how the view model resolves a printer's display name from its serial

The patches below use representative names. If any identifier differs, adapt to the real one without changing semantics.

- [ ] **Step 1: Create LiveActivityService**

Create `BambuGateway/Services/LiveActivityService.swift`:

```swift
import ActivityKit
import Foundation

@MainActor
final class LiveActivityService {
    private let client: GatewayClient
    private weak var pushService: PushService?
    private var activities: [String: Activity<PrintActivityAttributes>] = [:]
    private var tokenObservers: [String: Task<Void, Never>] = [:]

    init(client: GatewayClient, pushService: PushService?) {
        self.client = client
        self.pushService = pushService
    }

    /// Starts or reuses a Live Activity for the given printer/job.
    func startActivity(
        printerId: String,
        printerName: String,
        fileName: String,
        thumbnail: Data?,
        initialState: PrintActivityAttributes.ContentState,
    ) async {
        guard ActivityAuthorizationInfo().areActivitiesEnabled else { return }
        if activities[printerId] != nil { return }

        let attrs = PrintActivityAttributes(
            printerId: printerId,
            printerName: printerName,
            fileName: fileName,
            thumbnailData: thumbnail,
        )
        do {
            let activity = try Activity.request(
                attributes: attrs,
                content: .init(state: initialState, staleDate: nil),
                pushType: .token,
            )
            activities[printerId] = activity
            tokenObservers[printerId] = Task { [weak self] in
                for await tokenData in activity.pushTokenUpdates {
                    let hex = tokenData.map { String(format: "%02x", $0) }.joined()
                    await self?.registerUpdateToken(
                        printerId: printerId, token: hex,
                    )
                }
            }
        } catch {
            // ActivityKit refused; silently skip
        }
    }

    /// Updates an existing Live Activity's content state.
    func updateActivity(
        printerId: String, state: PrintActivityAttributes.ContentState,
    ) async {
        guard let activity = activities[printerId] else { return }
        await activity.update(.init(state: state, staleDate: nil))
    }

    /// Ends an existing Live Activity.
    func endActivity(
        printerId: String,
        finalState: PrintActivityAttributes.ContentState,
        dismissalPolicy: ActivityUIDismissalPolicy,
    ) async {
        guard let activity = activities[printerId] else { return }
        await activity.end(
            .init(state: finalState, staleDate: nil),
            dismissalPolicy: dismissalPolicy,
        )
        activities.removeValue(forKey: printerId)
        tokenObservers[printerId]?.cancel()
        tokenObservers.removeValue(forKey: printerId)

        if let deviceId = pushService?.deviceId {
            try? await client.unregisterActivity(
                deviceId: deviceId, printerId: printerId,
            )
        }
    }

    private func registerUpdateToken(printerId: String, token: String) async {
        guard let pushService, pushService.capabilitiesEnabled else { return }
        do {
            try await client.registerActivity(
                deviceId: pushService.deviceId,
                payload: ActivityRegisterPayload(
                    printerId: printerId, activityUpdateToken: token,
                ),
            )
        } catch {
            // Retry is handled implicitly — if ActivityKit rotates the token, the next event re-registers.
        }
    }
}
```

- [ ] **Step 2: Create NotificationService**

Create `BambuGateway/Services/NotificationService.swift`:

```swift
import Foundation
import UserNotifications

@MainActor
final class NotificationService {
    func requestAuthorizationIfNeeded() async {
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        guard settings.authorizationStatus == .notDetermined else { return }
        _ = try? await center.requestAuthorization(options: [.alert, .sound, .badge])
    }

    func fireLocal(title: String, body: String, identifier: String) async {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        let request = UNNotificationRequest(
            identifier: identifier, content: content, trigger: nil,
        )
        try? await UNUserNotificationCenter.current().add(request)
    }
}
```

- [ ] **Step 3: Extend AppViewModel**

Modify `BambuGateway/App/AppViewModel.swift`. Add properties, a bootstrap method, and helpers for translating polled statuses into Live Activity / local-notification events.

Read the existing `AppViewModel.swift` and locate the top of the `@MainActor class AppViewModel`. Add:

```swift
    let pushService: PushService
    let liveActivityService: LiveActivityService
    let notificationService: NotificationService

    private var previousStates: [String: PrinterState] = [:]
```

In the initializer (or default `init`), right after the existing `gatewayClient` is built, initialize the services:

```swift
        let push = PushService(client: gatewayClient)
        self.pushService = push
        self.liveActivityService = LiveActivityService(client: gatewayClient, pushService: push)
        self.notificationService = NotificationService()
        AppDelegate.pushService = push
```

Add a bootstrap method:

```swift
    func bootstrapPushServices() async {
        await notificationService.requestAuthorizationIfNeeded()
        await pushService.bootstrap()
    }
```

Find the place where `refreshPrinters` (or equivalent) assigns the latest `[PrinterStatus]` into the published property. Just before the assignment, call a new method that diffs old vs new and surfaces events locally:

```swift
        await handlePolledTransitions(newStatuses: fetchedStatuses)
```

Then add this method on `AppViewModel`:

```swift
    private func handlePolledTransitions(newStatuses: [PrinterStatus]) async {
        for status in newStatuses {
            let prev = previousStates[status.id]
            previousStates[status.id] = status.state

            guard let prev else { continue }
            if prev == status.state { continue }

            let content = makeContentState(from: status)

            switch status.state {
            case .printing:
                if prev == .paused {
                    await liveActivityService.updateActivity(printerId: status.id, state: content)
                } else {
                    // fresh start triggered elsewhere; nothing to do locally
                }
            case .paused:
                await liveActivityService.updateActivity(printerId: status.id, state: content)
                if !pushService.capabilitiesEnabled {
                    await notificationService.fireLocal(
                        title: "Print paused",
                        body: "\(status.name) paused",
                        identifier: "\(status.id)-paused",
                    )
                }
            case .error:
                await liveActivityService.endActivity(
                    printerId: status.id, finalState: content, dismissalPolicy: .immediate,
                )
                if !pushService.capabilitiesEnabled {
                    await notificationService.fireLocal(
                        title: "Print failed",
                        body: "\(status.name) stopped with an error",
                        identifier: "\(status.id)-error",
                    )
                }
            case .finished:
                await liveActivityService.endActivity(
                    printerId: status.id, finalState: content,
                    dismissalPolicy: .after(Date().addingTimeInterval(4 * 3600)),
                )
            case .cancelled:
                await liveActivityService.endActivity(
                    printerId: status.id, finalState: content, dismissalPolicy: .immediate,
                )
            default:
                break
            }
        }
    }

    private func makeContentState(from status: PrinterStatus) -> PrintActivityAttributes.ContentState {
        let progress = Double(status.job?.progress ?? 0) / 100.0
        return PrintActivityAttributes.ContentState(
            state: badge(for: status.state),
            stageName: status.stageName,
            progress: progress,
            remainingMinutes: status.job?.remainingMinutes ?? 0,
            currentLayer: status.job?.currentLayer ?? 0,
            totalLayers: status.job?.totalLayers ?? 0,
            updatedAt: Date(),
        )
    }

    private func badge(for state: PrinterState) -> PrinterStateBadge {
        switch state {
        case .idle: return .idle
        case .preparing: return .preparing
        case .printing: return .printing
        case .paused: return .paused
        case .finished: return .finished
        case .cancelled: return .cancelled
        case .error: return .error
        case .offline: return .offline
        }
    }
```

Find the method that actually submits a print (e.g. `submitPrint(...)`). Immediately after a successful submission, start a local Live Activity:

```swift
        await liveActivityService.startActivity(
            printerId: submission.printerId,
            printerName: printerName(for: submission.printerId),
            fileName: submission.file.originalFilename,
            thumbnail: submission.file.thumbnailData,
            initialState: PrintActivityAttributes.ContentState(
                state: .preparing,
                stageName: "Starting print",
                progress: 0.0,
                remainingMinutes: 0,
                currentLayer: 0,
                totalLayers: 0,
                updatedAt: Date(),
            ),
        )
```

If `submission.file.thumbnailData` is not yet on `Imported3MFFile`, pass `nil` — not every path has a thumbnail. The Live Activity widget handles `nil` via a placeholder.

Ensure `printerName(for:)` and the relevant property names match your existing code; adapt names without changing semantics.

- [ ] **Step 4: Build**

Run:
```
xcodebuild -project BambuGateway.xcodeproj -scheme BambuGateway -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build
```
Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add BambuGateway/Services/LiveActivityService.swift BambuGateway/Services/NotificationService.swift BambuGateway/App/AppViewModel.swift
git commit -m "Add Live Activity and local notification services"
```

---

### Task 18: Settings UI — push toggle

Shows the current capability status and a subscribe/unsubscribe toggle on the Settings screen. When the gateway does not support push, renders a disabled control with an explanation.

**Files:**
- Modify: `BambuGateway/Views/SettingsView.swift`

- [ ] **Step 1: Read the existing SettingsView**

Run: `cat BambuGateway/Views/SettingsView.swift | head -80`
Note the existing sections and how settings are structured.

- [ ] **Step 2: Add a push-notifications section**

Open `BambuGateway/Views/SettingsView.swift`. Add a new `Section` to the existing `Form`:

```swift
            Section("Notifications") {
                if viewModel.pushService.capabilitiesEnabled {
                    HStack {
                        Text("Push notifications")
                        Spacer()
                        Text("Enabled")
                            .foregroundStyle(.secondary)
                    }
                    Text("Your device receives alerts when prints pause, fail, complete, or go offline. Live Activities appear on the Lock Screen during prints.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    HStack {
                        Text("Push notifications")
                        Spacer()
                        Text("Unavailable")
                            .foregroundStyle(.secondary)
                    }
                    Text("Push requires APNs credentials on the gateway. See the README to configure your Apple Developer key.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
```

- [ ] **Step 3: Build**

Run:
```
xcodebuild -project BambuGateway.xcodeproj -scheme BambuGateway -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build
```
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add BambuGateway/Views/SettingsView.swift
git commit -m "Surface push-notifications status in Settings"
```

---

### Task 19: Manual verification matrix

ActivityKit + APNs is not meaningfully unit-testable. Verify on a real device using the matrix below. This task is a checklist — no code changes.

**Environment setup:**

- Gateway running with APNs key configured. For dev builds set `APNS_ENVIRONMENT=sandbox` and use an APNs Sandbox key.
- iPhone connected to the same LAN as the gateway. Install a dev build of `BambuGateway` signed against the same bundle ID as in `APNS_BUNDLE_ID`.

**Run each row, record pass/fail:**

- [ ] **Print start from iOS app** → Live Activity appears within 2s; Dynamic Island shows progress %.
- [ ] **Print start from web UI / OrcaSlicer** → Live Activity appears via push-to-start within a few seconds of the print beginning.
- [ ] **Print progresses** → Live Activity progress bar advances; no more than ~1 update per 10s.
- [ ] **Pause print** → Alert notification fires; Live Activity shows "Paused".
- [ ] **Resume print** → Live Activity returns to progressing state; no alert.
- [ ] **Print completes** → Alert notification fires; Live Activity stays visible for up to 4 hours with "Complete" status.
- [ ] **Cancel print** → Alert notification fires; Live Activity dismisses immediately.
- [ ] **Force an error** (e.g., unplug filament and wait for HMS) → Alert with HMS code; Live Activity shows "Error" briefly then dismisses.
- [ ] **Kill the app**, pause a print on the printer's touchscreen → Alert still fires (push path).
- [ ] **Gateway configured without APNs** → `/api/capabilities` returns `push: false`; Settings screen shows "Unavailable"; starting a print from iOS still shows a Live Activity while the app is running.
- [ ] **Airplane-mode the phone**, pause a print, re-enable network → alert arrives via APNs retry; Live Activity state reconciles on next poll.

- [ ] Record any failing rows as separate follow-up issues.

---

### Task 20: README updates (both repos)

Document how to enable APNs and what the feature does for end users.

**Files:**
- Modify: `bambu-gateway/README.md`
- Modify: `bambu-gateway-ios/README.md`

- [ ] **Step 1: Gateway README**

In `bambu-gateway/README.md`, add a new section before "Configuration":

```markdown
### iOS push notifications (optional)

The gateway can send push notifications and Live Activity updates to the
companion iOS app when prints change state. This requires a paid Apple
Developer account.

1. Create an APNs Auth Key (`.p8`) in the Apple Developer portal.
2. Note the **Key ID** and **Team ID**.
3. Set these env vars in `.env`:

```
APNS_KEY_PATH=/path/to/AuthKey_KEYID.p8
APNS_KEY_ID=KEYID
APNS_TEAM_ID=TEAMID
APNS_BUNDLE_ID=org.yourname.BambuGateway
APNS_ENVIRONMENT=production   # or "sandbox" for debug builds
```

Leaving `APNS_KEY_PATH` empty disables push. The iOS app degrades gracefully
in that case — Live Activities still run while the app is in the foreground,
but no remote updates or notifications are delivered.
```

- [ ] **Step 2: iOS README**

In `bambu-gateway-ios/README.md`, add near the feature list:

```markdown
### Live Activities and notifications

- Live Activity on the Lock Screen / Dynamic Island during prints, with
  progress %, remaining time, and current layer.
- Push notifications when prints pause, fail, complete, or go offline.
- Requires the gateway to have APNs credentials configured. Without them,
  the Live Activity still runs while the app is foregrounded, but remote
  updates are disabled.
```

- [ ] **Step 3: Commit each**

```bash
# In bambu-gateway
git add README.md
git commit -m "Document optional APNs configuration for push"

# In bambu-gateway-ios
git add README.md
git commit -m "Document Live Activities and push in README"
```

---

## Completion criteria

- [ ] Every task above checked off.
- [ ] `pytest -v` green in `bambu-gateway`.
- [ ] iOS build succeeds with `CODE_SIGNING_ALLOWED=NO`.
- [ ] Task 19 manual matrix run on a real device, each row pass/fail recorded.
- [ ] Both repo READMEs updated.
- [ ] Both branches (`live-activities-push`) ready for PR.
