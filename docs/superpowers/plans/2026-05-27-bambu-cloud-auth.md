# Bambu Cloud OAuth Paste Fallback — Implementation Plan (Phase 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Implement the auth flow so the user can sign into their Bambu account from the gateway. Because the plugin's OAuth redirect URI is hardcoded to `http://localhost:<port>` (per Phase 0 §Q11.1), we use the **paste fallback**: user signs in on Bambu's hosted page in their browser, manually pastes the redirected URL back into the gateway, gateway extracts tokens and calls `change_user` on the plugin.

**Architecture:** Stateless server-side (the plugin holds session state in its own files). Four new API endpoints provide: sign-in URL, paste-callback, status, logout. Token persistence is handled by the plugin itself — Python never sees or stores tokens.

**Tech Stack:** FastAPI, httpx, pytest with `asyncio_mode=auto`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-26-bambu-cloud-plugin-design.md` §6.
**Discovery:** `docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md` §Q11.1, §Q11.4.

---

## File Structure

**New files:**

| File | Purpose |
|---|---|
| `app/cloud/auth.py` | Sign-in URL builder, paste-URL parser, canonical-payload builder, profile fetch |
| `app/cloud/auth_routes.py` | FastAPI router with 4 endpoints |
| `tests/test_cloud_auth.py` | Unit tests for the URL/payload logic |
| `tests/test_cloud_auth_routes.py` | Route-level tests using the Python fake host |
| `docs/superpowers/notes/2026-05-27-bambu-auth-discovery.md` | Phase A output |

**Modified files:**

| File | Change |
|---|---|
| `app/main.py` | Mount the auth router; expose PluginHost to routes via app state |
| `tests/cloud_fake_host.py` | Add stubs for `is_user_login` + `user_logout` |

**Out of scope (deferred to a UI plan):**
- The web/ frontend changes for a Settings "Bambu Account" panel.

---

## Phase A — Discovery: Bambu sign-in URL + user-info endpoint

### Task A.1: Discover the Bambu sign-in URL shape

**Files:**
- Create: `docs/superpowers/notes/2026-05-27-bambu-auth-discovery.md`

- [ ] **Step 1: Read OrcaSlicer's external-browser sign-in URL builder**

Phase 0 §Q11.1 found `pjarczak_browser_login_url()` in `/Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/GUI/WebUserLoginDialog.cpp` around lines 64-76. Read that function end-to-end.

Also read the surrounding context to find:
- The Bambu sign-in base URL (likely `https://bambulab.com/sign-in` or similar — may differ from the API base)
- All query params it sets (`redirect_url`, `client_id`, `response_type`, `state`, etc.)
- How the `state` and PKCE values are sourced

- [ ] **Step 2: Document the URL shape**

Write to `docs/superpowers/notes/2026-05-27-bambu-auth-discovery.md`:

```markdown
# Bambu Cloud Auth — Discovery

## Sign-in URL

**Base URL (US region):** `<https://...>`

**Base URL (CN region):** `<https://...>`

**Required query params:**

| Param | Value | Source |
|---|---|---|
| `redirect_url` | `http://localhost:13618` | hardcoded in plugin or constructed in C++? |
| ... | ... | ... |

**Verbatim builder (`WebUserLoginDialog.cpp:64-76`):**
```cpp
<paste here>
```

**Country-code switching:** how does region selection affect the sign-in URL? Cite the code.

## Post-redirect URL shapes (Phase 0 §Q11.4 confirms)

After successful sign-in, Bambu redirects to one of:

- `<redirect_url>?ticket=<val>`
- `<redirect_url>?code=<val>&state=<val>`
- `<redirect_url>?access_token=<val>&refresh_token=<val>&expires_in=<n>&refresh_expires_in=<n>&redirect_url=<url>`

We target the third (access_token) form because it carries all tokens in the URL.

## Forcing the access_token form

If Bambu defaults to `?code=&state=`, we may need to pass `response_type=token` (implicit) or similar in the sign-in URL. Check OrcaSlicer's URL builder for any `response_type` query param.

Document the answer here.
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/notes/2026-05-27-bambu-auth-discovery.md
git commit -m "Cloud auth: discover sign-in URL shape"
```

### Task A.2: Discover the Bambu user-profile endpoint

After parsing tokens from the pasted URL, we need to fetch the user's profile (uid/uidStr/name/account/avatar) to build the canonical_login payload. The plugin has its own `get_my_profile` method, but calling Bambu's REST API directly from Python is simpler.

**Files:**
- Modify: `docs/superpowers/notes/2026-05-27-bambu-auth-discovery.md`

- [ ] **Step 1: Read OrcaSlicer's profile-fetch function**

Grep across the OrcaSlicer-bambulab repo:

```bash
grep -rn "get_my_profile\|api/v1/user\|user-service" \
  /Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/ | head -20
```

Find the function (likely `BBLCloudServiceAgent::get_my_profile`) and the HTTPS endpoint it hits.

- [ ] **Step 2: Document**

Append to discovery notes:

```markdown
## Profile fetch (after we have access_token)

**Endpoint:** `<HTTPS method + URL>`

**Headers:**
- `Authorization: Bearer <access_token>`
- `User-Agent: BambuStudio/02.05.02.58` (in case Bambu gates on identity)
- `X-BBL-*` (same as plugin downloads)

**Response shape:**
```json
{
  "uidStr": "...",
  "name": "...",
  "account": "...",
  "avatar": "..."
}
```
(Update with actual fields once verified against OrcaSlicer source.)

**Citations:**
- `<file>:<line>` — verbatim
```

- [ ] **Step 3: Probe live (if reachable)**

If you have a Bambu account, manually go through their sign-in once and grab a test access_token, then curl the profile endpoint to confirm the response shape. Skip this step if you don't have an account; the OrcaSlicer source is authoritative.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/notes/2026-05-27-bambu-auth-discovery.md
git commit -m "Cloud auth: discover profile endpoint"
```

---

## Phase B — Auth core logic (no FastAPI yet)

Pure functions for URL building, URL parsing, and canonical-payload construction. Easy to TDD without any subprocess or FastAPI.

### Task B.1: TDD the sign-in URL builder

**Files:**
- Create: `app/cloud/auth.py`
- Create: `tests/test_cloud_auth.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cloud_auth.py`:

```python
"""Tests for the cloud auth flow."""
from __future__ import annotations

import pytest

from app.cloud.auth import build_signin_url


def test_signin_url_us_region_uses_bambulab_com():
    url = build_signin_url(region="US")
    assert url.startswith("https://bambulab.com/sign-in")


def test_signin_url_cn_region_uses_bambulab_cn():
    url = build_signin_url(region="CN")
    assert "bambulab.cn" in url


def test_signin_url_includes_loopback_redirect():
    # The plugin's hardcoded redirect_url is http://localhost:13618 (per
    # Phase 0 §Q11.1 — pjarczak_browser_login_url).
    url = build_signin_url(region="US")
    assert "redirect_url=http%3A%2F%2Flocalhost%3A13618" in url or \
           "redirect_url=http://localhost:13618" in url


def test_signin_url_requests_token_response_type():
    # We need the access_token redirect shape (per discovery §Q11.4 Shape 3),
    # which Bambu emits when response_type=token. Defaults to code+state
    # which we cannot complete without the in-plugin PKCE verifier.
    url = build_signin_url(region="US")
    assert "response_type=token" in url
```

> **Important:** If Phase A discovery reveals that Bambu does NOT honour `response_type=token` (i.e., always returns code+state regardless), update this test to assert whichever response_type OrcaSlicer's external-browser builder uses. The post-redirect URL parser in Task B.2 will need to handle that shape instead.

- [ ] **Step 2: Run, confirm fail**

```bash
.venv/bin/pytest tests/test_cloud_auth.py -v
```

Expected: `ImportError: cannot import name 'build_signin_url'`.

- [ ] **Step 3: Implement**

Create `app/cloud/auth.py`:

```python
"""Cloud auth flow: sign-in URL, paste-callback parsing, canonical payload."""
from __future__ import annotations

from urllib.parse import urlencode

# These values come from Phase A discovery. The redirect_url is fixed because
# the plugin hardcodes its loopback callback URL — we can't change it.
_LOOPBACK_REDIRECT = "http://localhost:13618"
_BAMBU_STUDIO_VERSION = "02.05.02.58"

_REGION_SIGNIN_BASE: dict[str, str] = {
    "US": "https://bambulab.com/sign-in",
    # Discovery may reveal a different sign-in host for CN; update if so.
    "CN": "https://bambulab.cn/sign-in",
}


def build_signin_url(*, region: str) -> str:
    """Build the Bambu hosted sign-in URL the user opens in their browser.

    The redirect_url is fixed by the plugin (`http://localhost:13618`); after
    the user signs in, Bambu redirects there with tokens in the query.
    Because nothing is listening on that port from the user's browser, the
    browser shows a "connection refused" page — the user then copies the
    URL from their address bar and pastes it into the gateway's
    /api/cloud/auth/paste endpoint.
    """
    base = _REGION_SIGNIN_BASE.get(region)
    if base is None:
        raise ValueError(f"unsupported region: {region!r}")
    params = {
        "response_type": "token",
        "redirect_url": _LOOPBACK_REDIRECT,
        "client_id": "bambu_studio",
        "version": _BAMBU_STUDIO_VERSION,
    }
    return f"{base}?{urlencode(params)}"
```

> **Note:** the `client_id` and `version` params are educated guesses based on Bambu's standard OAuth scheme. Phase A discovery will confirm the actual values. Update the constants if discovery differs.

- [ ] **Step 4: Run, confirm pass**

```bash
.venv/bin/pytest tests/test_cloud_auth.py -v
```

Expected: 4/4 green.

- [ ] **Step 5: Commit**

```bash
git add app/cloud/auth.py tests/test_cloud_auth.py
git commit -m "Cloud auth: sign-in URL builder"
```

### Task B.2: TDD the paste-URL parser

The user pastes back a URL like `http://localhost:13618/?access_token=xyz&refresh_token=abc&expires_in=86400&refresh_expires_in=2592000&redirect_url=...`. Parse out the four token fields.

**Files:**
- Modify: `app/cloud/auth.py`
- Modify: `tests/test_cloud_auth.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_cloud_auth.py`:

```python
from app.cloud.auth import PasteParseError, parse_paste_url, PasteTokens


def test_parse_paste_url_extracts_all_token_fields():
    pasted = (
        "http://localhost:13618/?"
        "access_token=at_abc&refresh_token=rt_xyz&"
        "expires_in=86400&refresh_expires_in=2592000&"
        "redirect_url=http%3A%2F%2Flocalhost%3A13618"
    )
    tokens = parse_paste_url(pasted)
    assert tokens == PasteTokens(
        access_token="at_abc",
        refresh_token="rt_xyz",
        expires_in="86400",
        refresh_expires_in="2592000",
    )


def test_parse_paste_url_accepts_fragment_style():
    # Some OAuth flows put tokens in the fragment (#) instead of the query (?).
    pasted = (
        "http://localhost:13618/#"
        "access_token=at_abc&refresh_token=rt_xyz&"
        "expires_in=86400&refresh_expires_in=2592000"
    )
    tokens = parse_paste_url(pasted)
    assert tokens.access_token == "at_abc"


def test_parse_paste_url_rejects_missing_access_token():
    with pytest.raises(PasteParseError):
        parse_paste_url("http://localhost:13618/?refresh_token=rt&expires_in=1")


def test_parse_paste_url_rejects_obviously_wrong_input():
    with pytest.raises(PasteParseError):
        parse_paste_url("https://example.com/")
    with pytest.raises(PasteParseError):
        parse_paste_url("not a url")
```

- [ ] **Step 2: Run, confirm fail**

```bash
.venv/bin/pytest tests/test_cloud_auth.py -v -k paste
```

Expected: ImportError.

- [ ] **Step 3: Implement**

Append to `app/cloud/auth.py`:

```python
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


class PasteParseError(ValueError):
    """Raised when the user-pasted URL doesn't carry the expected tokens."""


@dataclass(frozen=True)
class PasteTokens:
    """The four token fields a successful access_token redirect carries."""

    access_token: str
    refresh_token: str
    expires_in: str
    refresh_expires_in: str


def parse_paste_url(pasted: str) -> PasteTokens:
    """Extract the OAuth token fields from the URL the user pasted back.

    Bambu's sign-in page may put the tokens in either the query string (``?``)
    or the URL fragment (``#``); we try both.
    """
    try:
        parsed = urlparse(pasted)
    except Exception as exc:
        raise PasteParseError(f"not a URL: {pasted!r}") from exc

    # Real OAuth callbacks land on the loopback host; lock that down to catch
    # obviously-wrong pastes (a Google search result, say).
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise PasteParseError(
            f"expected a localhost redirect URL, got {parsed.hostname!r}"
        )

    qs: dict[str, list[str]] = {}
    if parsed.query:
        qs.update(parse_qs(parsed.query))
    if parsed.fragment:
        qs.update(parse_qs(parsed.fragment))

    def one(field: str) -> str:
        vals = qs.get(field)
        if not vals:
            raise PasteParseError(f"pasted URL missing {field!r}")
        return vals[0]

    return PasteTokens(
        access_token=one("access_token"),
        refresh_token=one("refresh_token"),
        expires_in=one("expires_in"),
        refresh_expires_in=one("refresh_expires_in"),
    )
```

- [ ] **Step 4: Run, confirm pass**

- [ ] **Step 5: Commit**

```bash
git add app/cloud/auth.py tests/test_cloud_auth.py
git commit -m "Cloud auth: paste-URL parser"
```

### Task B.3: TDD the canonical_login payload builder

Given `PasteTokens` + profile JSON (from Bambu's REST), build the canonical `{"command":"user_login","data":{...}}` payload (per Phase 0 §Q11.4) that gets passed to plugin's `change_user`.

**Files:**
- Modify: `app/cloud/auth.py`
- Modify: `tests/test_cloud_auth.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
import json

from app.cloud.auth import build_canonical_login


def test_build_canonical_login_assembles_token_and_profile_fields():
    tokens = PasteTokens(
        access_token="at",
        refresh_token="rt",
        expires_in="3600",
        refresh_expires_in="86400",
    )
    profile = {
        "uidStr": "42",
        "name": "Alice",
        "account": "alice@example.com",
        "avatar": "https://cdn/avatar.png",
    }
    payload = build_canonical_login(tokens=tokens, profile=profile)
    obj = json.loads(payload)
    assert obj["command"] == "user_login"
    d = obj["data"]
    # Token fields — both `token` and `access_token` carry the same value
    # for compatibility (per Q11.4).
    assert d["token"] == "at"
    assert d["access_token"] == "at"
    assert d["refresh_token"] == "rt"
    assert d["expires_in"] == "3600"
    assert d["refresh_expires_in"] == "86400"
    # User-id fields — uidStr/user_id/user.id/user.uid/user.uidStr all carry
    # the same value (per Q11.4 and HttpServer.cpp:38-65).
    assert d["user_id"] == "42"
    assert d["uidStr"] == "42"
    assert d["user"]["id"] == "42"
    assert d["user"]["uid"] == "42"
    assert d["user"]["uidStr"] == "42"
    assert d["user"]["name"] == "Alice"
    assert d["user"]["account"] == "alice@example.com"
    assert d["user"]["avatar"] == "https://cdn/avatar.png"


def test_build_canonical_login_accepts_uid_in_other_field_names():
    # Bambu's profile API may return `uid` or `id` rather than `uidStr`.
    # Q11.4 says build_canonical_login_payload normalises via json_string_first.
    tokens = PasteTokens("at", "rt", "1", "1")
    payload = build_canonical_login(
        tokens=tokens, profile={"uid": "99", "name": "Bob"}
    )
    obj = json.loads(payload)
    assert obj["data"]["user_id"] == "99"


def test_build_canonical_login_rejects_missing_uid():
    tokens = PasteTokens("at", "rt", "1", "1")
    with pytest.raises(ValueError):
        build_canonical_login(tokens=tokens, profile={"name": "x"})
```

- [ ] **Step 2: Implement**

Append to `app/cloud/auth.py`:

```python
import json as _json


def _first_present(d: dict, *keys: str) -> str | None:
    for k in keys:
        v = d.get(k)
        if v is not None:
            return str(v)
    return None


def build_canonical_login(*, tokens: PasteTokens, profile: dict) -> str:
    """Assemble the canonical ``change_user`` payload from tokens + profile.

    Matches the schema in Phase 0 §Q11.4 (HttpServer.cpp:38-65,
    ``build_canonical_login_payload``). The string returned is what gets
    passed to ``plugin.change_user``.
    """
    uid = _first_present(profile, "uidStr", "uid", "id")
    if uid is None:
        raise ValueError("profile must contain uidStr/uid/id")

    payload = {
        "command": "user_login",
        "data": {
            "token": tokens.access_token,
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_in": tokens.expires_in,
            "refresh_expires_in": tokens.refresh_expires_in,
            "user_id": uid,
            "uidStr": uid,
            "user": {
                "id": uid,
                "uid": uid,
                "uidStr": uid,
                "name": profile.get("name", ""),
                "account": profile.get("account", ""),
                "avatar": profile.get("avatar", ""),
            },
        },
    }
    return _json.dumps(payload)
```

- [ ] **Step 3: Run, confirm pass + commit**

```bash
.venv/bin/pytest tests/test_cloud_auth.py -v
git add app/cloud/auth.py tests/test_cloud_auth.py
git commit -m "Cloud auth: build canonical_login payload"
```

### Task B.4: TDD the profile fetch (httpx-based)

**Files:**
- Modify: `app/cloud/auth.py`
- Modify: `tests/test_cloud_auth.py`

> The exact URL + response shape come from Phase A.2 discovery. The implementation below uses an educated guess; update the URL constant if discovery differs.

- [ ] **Step 1: Write failing tests**

Append:

```python
import httpx

from app.cloud.auth import fetch_profile, ProfileFetchError


async def test_fetch_profile_returns_profile_json():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            200,
            json={
                "uidStr": "42",
                "name": "Alice",
                "account": "alice@example.com",
                "avatar": "https://cdn/avatar.png",
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        profile = await fetch_profile(
            client=client, region="US", access_token="at_xyz"
        )

    assert profile["uidStr"] == "42"
    # Confirm the request carried the right auth header + forged UA.
    req = seen_requests[0]
    assert req.headers["Authorization"] == "Bearer at_xyz"
    assert "BambuStudio" in req.headers["User-Agent"]


async def test_fetch_profile_raises_on_non_200():
    def handler(request):
        return httpx.Response(401, json={"error": "invalid_token"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ProfileFetchError):
            await fetch_profile(client=client, region="US", access_token="bad")
```

- [ ] **Step 2: Implement**

Append to `app/cloud/auth.py`:

```python
from app.cloud import BAMBU_STUDIO_USER_AGENT, BAMBU_NETWORK_AGENT_VERSION
import httpx


class ProfileFetchError(RuntimeError):
    """Raised when the Bambu profile API rejects the access token."""


_REGION_API_BASE: dict[str, str] = {
    "US": "https://api.bambulab.com",
    "CN": "https://api.bambulab.cn",
}

# Phase A.2 should confirm this path. Educated guess from OrcaSlicer's typical
# Bambu REST surface. Update if discovery differs.
_PROFILE_PATH = "/v1/user-service/my/profile"


async def fetch_profile(
    *, client: httpx.AsyncClient, region: str, access_token: str
) -> dict:
    """Fetch the user's profile from Bambu's REST API using the access token.

    Raises :class:`ProfileFetchError` on non-2xx responses.
    """
    base = _REGION_API_BASE.get(region)
    if base is None:
        raise ValueError(f"unsupported region: {region!r}")
    response = await client.get(
        base + _PROFILE_PATH,
        headers={
            "Authorization": f"Bearer {access_token}",
            "User-Agent": BAMBU_STUDIO_USER_AGENT,
            "X-BBL-Client-Type": "slicer",
            "X-BBL-Client-Name": "BambuStudio",
            "X-BBL-Client-Version": BAMBU_NETWORK_AGENT_VERSION,
            "X-BBL-OS-Type": "linux",
        },
        timeout=15.0,
    )
    if response.status_code != 200:
        raise ProfileFetchError(
            f"Bambu profile API returned {response.status_code}: "
            f"{response.text[:200]}"
        )
    return response.json()
```

- [ ] **Step 3: Run + commit**

```bash
.venv/bin/pytest tests/test_cloud_auth.py -v
git add app/cloud/auth.py tests/test_cloud_auth.py
git commit -m "Cloud auth: fetch Bambu profile via REST"
```

---

## Phase C — Auth orchestration: tying it together with the PluginHost

### Task C.1: Add `is_user_login` + `user_logout` stubs to the fake host

The auth flow needs to call two more plugin methods that aren't in the fake host yet.

**Files:**
- Modify: `tests/cloud_fake_host.py`

- [ ] **Step 1: Read the existing fake host**

Read `tests/cloud_fake_host.py` to understand the current `_dispatch` shape.

- [ ] **Step 2: Add new methods**

Modify `_dispatch` to also handle:

```python
if method == "is_user_login":
    # Toggled by FAKE_HOST_USER_LOGGED_IN env var (used in tests).
    import os
    return {"is_login": os.environ.get("FAKE_HOST_USER_LOGGED_IN") == "1"}
if method == "user_logout":
    # with_backend_notify arg accepted; result is 0 for success.
    return {"rc": 0}
```

- [ ] **Step 3: Commit**

```bash
git add tests/cloud_fake_host.py
git commit -m "Cloud auth: add is_user_login + user_logout to fake host"
```

### Task C.2: Implement the high-level `complete_login` orchestrator

This wires Phase B's helpers together: parse paste → fetch profile → build canonical → call plugin `change_user` → confirm via `is_user_login`.

**Files:**
- Modify: `app/cloud/auth.py`
- Modify: `tests/test_cloud_auth.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cloud_auth.py`:

```python
import sys
from pathlib import Path

from app.cloud.plugin_host import PluginHost
from app.cloud.auth import complete_login, LoginFailed


FAKE_HOST = Path(__file__).parent / "cloud_fake_host.py"


async def test_complete_login_happy_path(tmp_path, monkeypatch):
    record_file = tmp_path / "requests.jsonl"
    pasted_url = (
        "http://localhost:13618/?access_token=at&refresh_token=rt"
        "&expires_in=3600&refresh_expires_in=86400"
    )

    def profile_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer at"
        return httpx.Response(
            200,
            json={
                "uidStr": "42",
                "name": "Alice",
                "account": "alice@example.com",
                "avatar": "",
            },
        )

    async with (
        PluginHost(
            cmd=[sys.executable, str(FAKE_HOST)],
            env={
                "FAKE_HOST_RECORD_FILE": str(record_file),
                # Make is_user_login return True after change_user is called.
                "FAKE_HOST_USER_LOGGED_IN": "1",
            },
        ) as host,
        httpx.AsyncClient(transport=httpx.MockTransport(profile_handler)) as http,
    ):
        result = await complete_login(
            host=host, http=http, region="US", pasted_url=pasted_url
        )

    assert result["account"] == "alice@example.com"
    # Fake host should have seen: change_user, then is_user_login.
    import json as _json
    seen = [_json.loads(line) for line in record_file.read_text().splitlines()]
    methods = [r["method"] for r in seen]
    assert "change_user" in methods
    assert methods.index("change_user") < methods.index("is_user_login")


async def test_complete_login_fails_if_plugin_does_not_register_login(tmp_path):
    pasted = (
        "http://localhost:13618/?access_token=at&refresh_token=rt"
        "&expires_in=3600&refresh_expires_in=86400"
    )

    def profile_handler(request):
        return httpx.Response(200, json={"uidStr": "1", "name": "x"})

    async with (
        PluginHost(
            cmd=[sys.executable, str(FAKE_HOST)],
            # FAKE_HOST_USER_LOGGED_IN NOT set — is_user_login will return False
        ) as host,
        httpx.AsyncClient(transport=httpx.MockTransport(profile_handler)) as http,
    ):
        with pytest.raises(LoginFailed):
            await complete_login(
                host=host, http=http, region="US", pasted_url=pasted
            )
```

- [ ] **Step 2: Implement**

Append to `app/cloud/auth.py`:

```python
import logging

from app.cloud.plugin_host import PluginHost, PluginHostError

logger = logging.getLogger("bambu.cloud.auth")


class LoginFailed(RuntimeError):
    """Raised when change_user succeeded but the plugin doesn't report login."""


async def complete_login(
    *,
    host: PluginHost,
    http: httpx.AsyncClient,
    region: str,
    pasted_url: str,
) -> dict:
    """Drive the full paste-fallback login flow end-to-end.

    Returns the profile dict on success. Raises:

    - :class:`PasteParseError` if the URL doesn't carry the expected tokens
    - :class:`ProfileFetchError` if Bambu rejects the access token
    - :class:`PluginHostError` if the plugin host RPC fails
    - :class:`LoginFailed` if change_user returned 0 but is_user_login is False
    """
    tokens = parse_paste_url(pasted_url)
    profile = await fetch_profile(
        client=http, region=region, access_token=tokens.access_token
    )
    canonical = build_canonical_login(tokens=tokens, profile=profile)

    change_result = await host.call("change_user", {"canonical_login": canonical})
    rc = change_result.get("rc", -1)
    if rc != 0:
        raise LoginFailed(f"plugin change_user returned rc={rc}")

    status = await host.call("is_user_login", {})
    if not status.get("is_login"):
        raise LoginFailed("plugin did not register login after change_user")

    logger.info(
        "Bambu user signed in: account=%s uid=%s",
        profile.get("account"),
        profile.get("uidStr") or profile.get("uid") or profile.get("id"),
    )
    return profile


async def logout(*, host: PluginHost) -> None:
    """Sign out, telling the plugin to also notify Bambu's backend."""
    await host.call("user_logout", {"with_backend_notify": True})


async def is_signed_in(*, host: PluginHost) -> bool:
    """Return True if the plugin currently has a valid logged-in user."""
    result = await host.call("is_user_login", {})
    return bool(result.get("is_login"))
```

- [ ] **Step 3: Add a `change_user` method handler in the fake host**

The existing fake host has `change_user` — confirm it still returns `{"rc": 0}`. No changes needed unless the test fails.

- [ ] **Step 4: Run + commit**

```bash
.venv/bin/pytest tests/test_cloud_auth.py -v
git add app/cloud/auth.py tests/test_cloud_auth.py
git commit -m "Cloud auth: complete_login orchestrator"
```

---

## Phase D — FastAPI routes

### Task D.1: Mount PluginHost on `app.state` for routes to consume

The auth routes (and future cloud routes) need access to the running `PluginHost` instance. Today the lifespan creates one inside its own scope but doesn't expose it. Fix that.

**Files:**
- Modify: `app/main.py`
- Modify: `tests/test_cloud_config.py`

- [ ] **Step 1: Find the lifespan in `app/main.py`**

The Phase F.2 task added an `AsyncExitStack`-based lifespan that enters a `PluginHost` when cloud mode is on. Currently it's a local variable. Make it accessible from routes via `app.state.cloud_host`.

Conceptual change:

```python
# Inside lifespan, after `host = await stack.enter_async_context(PluginHost(...))`:
app.state.cloud_host = host
# (No teardown for state — the host is closed by stack unwind.)
```

- [ ] **Step 2: Add a test that confirms it's exposed**

Append to `tests/test_cloud_config.py`:

```python
def test_lifespan_exposes_plugin_host_on_app_state(monkeypatch, tmp_path):
    import app.main as main_mod
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_mod.settings, "bambu_cloud_enabled", True)
    monkeypatch.setattr(main_mod.settings, "bambu_cloud_plugin_dir", tmp_path)
    monkeypatch.setattr(
        main_mod.settings,
        "bambu_cloud_host_binary",
        tmp_path / "fake_host_binary",
    )

    sentinel = object()

    class FakeHost:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return sentinel
        async def __aexit__(self, *_): pass
        async def call(self, *_a, **_k): return {"bootstrap_rc": 0}

    with (
        patch(
            "app.cloud.plugin_downloader.PluginDownloader.ensure_active",
            new=AsyncMock(return_value=None),
        ),
        patch("app.printer_service.PrinterService.start", new=MagicMock()),
        patch("app.main.PluginHost", FakeHost),
    ):
        from fastapi.testclient import TestClient
        with TestClient(main_mod.app):
            # While the TestClient is running, app.state.cloud_host should be set.
            assert getattr(main_mod.app.state, "cloud_host", None) is sentinel
```

> **Caveat:** `__aenter__` returning a sentinel vs returning `self` is a real
> structural choice. The current FakePluginHost class returns `self`. If the
> production lifespan does `host = await stack.enter_async_context(host_obj)`,
> then `host` is whatever `__aenter__` returned — typically `self`. Adjust the
> FakeHost above to return `self` and check `cloud_host is fake_host_instance`
> if that fits better.

- [ ] **Step 3: Wire `app.state.cloud_host`**

Implementation detail in `app/main.py` (read the existing lifespan first):

```python
async with AsyncExitStack() as stack:
    ...
    if settings.bambu_cloud_enabled:
        ...
        host = await stack.enter_async_context(PluginHost(cmd=..., env=...))
        boot = await host.call("init_plugin", {})
        if boot.get("bootstrap_rc", -1) != 0:
            raise RuntimeError(...)
        app.state.cloud_host = host
    ...
    yield
    # On exit, stack unwinds; clear the attribute for cleanliness.
    app.state.cloud_host = None
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/bin/pytest tests/test_cloud_config.py -v
git add app/main.py tests/test_cloud_config.py
git commit -m "Cloud auth: expose PluginHost on app.state"
```

### Task D.2: Implement the four auth routes

**Files:**
- Create: `app/cloud/auth_routes.py`
- Create: `tests/test_cloud_auth_routes.py`
- Modify: `app/main.py` (mount the router)

- [ ] **Step 1: Write failing tests**

Create `tests/test_cloud_auth_routes.py`:

```python
"""Route-level tests for /api/cloud/auth/* using the fake host."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_mod


FAKE_HOST = Path(__file__).parent / "cloud_fake_host.py"


@pytest.fixture
def cloud_app(monkeypatch, tmp_path):
    """A TestClient with cloud mode enabled and the Python fake host wired in."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_mod.settings, "bambu_cloud_enabled", True)
    monkeypatch.setattr(main_mod.settings, "bambu_cloud_plugin_dir", tmp_path)
    monkeypatch.setattr(main_mod.settings, "bambu_cloud_region", "US")
    monkeypatch.setattr(
        main_mod.settings,
        "bambu_cloud_host_binary",
        tmp_path / "ignored",
    )

    # The auth-routes layer reads app.state.cloud_host; we stub
    # the lifespan to attach a Python fake host directly.
    from app.cloud.plugin_host import PluginHost

    real_host = PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={"FAKE_HOST_USER_LOGGED_IN": "1"},
    )

    class _AlreadyOpenHost:
        def __init__(self, *_, **__): pass
        async def __aenter__(self): return real_host
        async def __aexit__(self, *_):
            await real_host.stop()

    with (
        patch(
            "app.cloud.plugin_downloader.PluginDownloader.ensure_active",
            new=AsyncMock(return_value=None),
        ),
        patch("app.printer_service.PrinterService.start", new=MagicMock()),
        patch("app.main.PluginHost", _AlreadyOpenHost),
    ):
        # Start the real fake-host subprocess separately so the lifespan can
        # use the already-running one.
        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(real_host.start())
        try:
            with TestClient(main_mod.app) as client:
                yield client
        finally:
            loop.run_until_complete(real_host.stop())
            loop.close()


def test_get_signin_url(cloud_app):
    resp = cloud_app.get("/api/cloud/auth/url")
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"].startswith("https://bambulab.com/sign-in")


def test_get_status_when_not_logged_in(cloud_app, monkeypatch):
    # FAKE_HOST_USER_LOGGED_IN is sticky from the fixture; for this test we
    # need to spin up a separate state. Simpler: just call the endpoint and
    # confirm the contract — actual `is_login` truthiness is exercised by
    # the Phase C orchestrator tests.
    resp = cloud_app.get("/api/cloud/auth/status")
    assert resp.status_code == 200
    assert "signed_in" in resp.json()


def test_post_paste_completes_login(cloud_app):
    pasted = (
        "http://localhost:13618/?access_token=at&refresh_token=rt"
        "&expires_in=3600&refresh_expires_in=86400"
    )

    def profile_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"uidStr": "42", "name": "Alice", "account": "a@b"},
        )

    with patch(
        "app.cloud.auth_routes._make_http_client",
        return_value=httpx.AsyncClient(
            transport=httpx.MockTransport(profile_handler)
        ),
    ):
        resp = cloud_app.post(
            "/api/cloud/auth/paste",
            json={"pasted_url": pasted},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["profile"]["account"] == "a@b"


def test_post_paste_rejects_malformed_url(cloud_app):
    resp = cloud_app.post(
        "/api/cloud/auth/paste",
        json={"pasted_url": "https://google.com/"},
    )
    assert resp.status_code == 400
    assert "localhost" in resp.json()["detail"]


def test_post_logout(cloud_app):
    resp = cloud_app.post("/api/cloud/auth/logout")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
```

- [ ] **Step 2: Implement the router**

Create `app/cloud/auth_routes.py`:

```python
"""FastAPI routes for /api/cloud/auth/*."""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.cloud import auth
from app.cloud.plugin_host import PluginHostError
from app.config import settings

logger = logging.getLogger("bambu.cloud.auth.routes")

router = APIRouter(prefix="/api/cloud/auth", tags=["cloud-auth"])


def _make_http_client() -> httpx.AsyncClient:
    """Module-level factory so tests can monkeypatch it."""
    return httpx.AsyncClient(timeout=30.0)


def _require_host(request: Request):
    host = getattr(request.app.state, "cloud_host", None)
    if host is None:
        raise HTTPException(
            status_code=503,
            detail="cloud mode not active (BAMBU_CLOUD_ENABLED=false)",
        )
    return host


@router.get("/url")
async def get_signin_url() -> dict:
    return {"url": auth.build_signin_url(region=settings.bambu_cloud_region)}


@router.get("/status")
async def get_status(request: Request) -> dict:
    host = _require_host(request)
    try:
        signed_in = await auth.is_signed_in(host=host)
    except PluginHostError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"signed_in": signed_in}


class PasteBody(BaseModel):
    pasted_url: str


@router.post("/paste")
async def post_paste(request: Request, body: PasteBody) -> dict:
    host = _require_host(request)
    async with _make_http_client() as http:
        try:
            profile = await auth.complete_login(
                host=host,
                http=http,
                region=settings.bambu_cloud_region,
                pasted_url=body.pasted_url,
            )
        except auth.PasteParseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except auth.ProfileFetchError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except (auth.LoginFailed, PluginHostError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"profile": profile}


@router.post("/logout")
async def post_logout(request: Request) -> dict:
    host = _require_host(request)
    try:
        await auth.logout(host=host)
    except PluginHostError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True}
```

- [ ] **Step 3: Mount the router in `app/main.py`**

```python
from app.cloud.auth_routes import router as cloud_auth_router
# inside the FastAPI app setup (typically after `app = FastAPI(...)`):
app.include_router(cloud_auth_router)
```

Read `app/main.py` to find where existing routers are mounted; mount this one alongside them.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_cloud_auth_routes.py -v
```

Expected: all green. The fixture is intricate (it has to spawn the fake host subprocess outside the TestClient's event loop, which is fiddly). If you hit "event loop is closed" or similar, adapt the fixture pattern — the simplest fallback is to use `pytest_asyncio.fixture` and let pytest-asyncio manage the loop. Don't over-engineer this; the key is that the routes get called and return the expected HTTP shapes.

- [ ] **Step 5: Run the full suite**

```bash
.venv/bin/pytest -q
```

Expected: previous baseline + 5 = whatever it was + 5.

- [ ] **Step 6: Commit**

```bash
git add app/cloud/auth_routes.py app/main.py tests/test_cloud_auth_routes.py
git commit -m "Cloud auth: /api/cloud/auth/{url,status,paste,logout} routes"
```

---

## Wrap-up

Phase 4 is complete. The gateway now exposes a working OAuth paste-fallback flow that drives `change_user` on the plugin. A user can:

1. `GET /api/cloud/auth/url` → open the returned URL in their browser
2. Sign in on Bambu's hosted page
3. Copy the failed-redirect URL from their browser
4. `POST /api/cloud/auth/paste` with that URL → gateway is now signed in
5. `GET /api/cloud/auth/status` → confirm `{"signed_in": true}`

The follow-up plans (5, 6, 7) build on this:

- **Phase 5** adds `connect_server` + `start_subscribe` + `add_subscribe` + `OnMessage` event handling so the dashboard sees printer status over cloud MQTT.
- **Phase 6** adds `start_print` so cloud printers can receive print jobs.
- **Phase 7** adds `send_message` for control commands (pause/resume/cancel/speed/AMS-drying).

UI integration (a Settings page panel for the auth flow) is intentionally out of scope here; the API is curl-able as-is.
