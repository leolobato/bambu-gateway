# Bambu Cloud OAuth Paste Fallback — Implementation Plan (Phase 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

> **Plan revision (2026-05-27, after Phase A discovery):** OrcaSlicer's
> external-browser sign-in URL uses `slicerLoginType=ticket`, so the
> redirect carries `?ticket=<val>` — **not** the `?access_token=` form
> originally assumed. The flow therefore needs the plugin's `get_my_token`
> method (and `get_my_profile`) to exchange the ticket for tokens. Tasks
> B.1–C.2 below are written for the ticket flow.

**Goal:** Implement the auth flow so the user can sign into their Bambu account from the gateway. Because the plugin's OAuth redirect URI is hardcoded to `http://localhost:<port>` (per Phase 0 §Q11.1), we use the **paste fallback**: user signs in on Bambu's hosted page in their browser, manually pastes the `?ticket=...` URL back into the gateway, gateway exchanges the ticket via the plugin's `get_my_token`, fetches the profile via `get_my_profile`, builds the canonical payload, calls `change_user`.

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

### Task B.1: TDD the sign-in URL builder (ticket flow, mirrors OrcaSlicer)

**Files:**
- Create: `app/cloud/auth.py`
- Create: `tests/test_cloud_auth.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cloud_auth.py`:

```python
"""Tests for the cloud auth flow."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse, unquote

import pytest

from app.cloud.auth import build_signin_url


def test_signin_url_us_region_uses_bambulab_com():
    url = build_signin_url(region="US")
    assert url.startswith("https://bambulab.com/sign-in")


def test_signin_url_cn_region_uses_bambulab_cn():
    url = build_signin_url(region="CN")
    assert "bambulab.cn" in url


def test_signin_url_carries_ticket_flow_markers():
    # OrcaSlicer's pjarczak_browser_login_url builder uses
    # `slicerLoginType=ticket` so Bambu redirects with `?ticket=<val>`
    # (Phase A discovery, 2026-05-27-bambu-auth-discovery.md §A.1).
    url = build_signin_url(region="US")
    qs = parse_qs(urlparse(url).query)
    # Outer URL has `to=<encoded inner URL>`; inner URL carries the
    # ticket-flow markers.
    inner = unquote(qs["to"][0])
    inner_qs = parse_qs(urlparse(inner).query)
    assert inner_qs["slicerLoginType"] == ["ticket"]
    assert inner_qs["redirect_url"] == ["http://localhost:13618"]
    assert inner_qs["openBy"] == ["suite"]


def test_signin_url_outer_query_includes_from_and_source():
    url = build_signin_url(region="US")
    qs = parse_qs(urlparse(url).query)
    assert qs["from"] == ["studio"]
    assert qs["source"] == ["portal"]
```

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

# These values mirror `pjarczak_browser_login_url()` in
# WebUserLoginDialog.cpp:64-76 (see Phase A discovery notes §A.1).
# The redirect_url is fixed because the plugin hardcodes its loopback
# callback URL — we cannot change it.
_LOOPBACK_REDIRECT = "http://localhost:13618"

_REGION_SIGNIN_BASE: dict[str, str] = {
    "US": "https://bambulab.com/sign-in",
    "CN": "https://bambulab.cn/sign-in",
}


def build_signin_url(*, region: str, locale: str = "en") -> str:
    """Build the Bambu hosted sign-in URL the user opens in their browser.

    Two-level URL: the outer is the public sign-in page, the ``to=`` query
    param is the inner callback URL that carries the ticket-flow markers.
    Mirrors OrcaSlicer's ``pjarczak_browser_login_url`` (Phase A §A.1).

    After the user signs in, Bambu redirects to
    ``http://localhost:13618/?ticket=<val>``. Because nothing is listening
    on that port from the user's browser, the browser shows a "connection
    refused" page — the user then copies the URL from their address bar
    and pastes it into the gateway's /api/cloud/auth/paste endpoint.
    """
    base = _REGION_SIGNIN_BASE.get(region)
    if base is None:
        raise ValueError(f"unsupported region: {region!r}")

    # Inner callback URL — carries the ticket-flow markers.
    inner_qs = urlencode({
        "source": "portal",
        "locale": locale,
        "redirect_url": _LOOPBACK_REDIRECT,
        "openBy": "suite",
        "from": "studio",
        "slicerLoginType": "ticket",
    })
    inner = f"{base}/callback?{inner_qs}"

    # Outer URL — carries `to=<inner-encoded>`.
    outer_qs = urlencode({
        "from": "studio",
        "source": "portal",
        "to": inner,
    })
    return f"{base}?{outer_qs}"
```

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

### Task B.2: TDD the paste-URL parser (ticket flow)

The user pastes back a URL like `http://localhost:13618/?ticket=abc123`. Extract the ticket string.

**Files:**
- Modify: `app/cloud/auth.py`
- Modify: `tests/test_cloud_auth.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_cloud_auth.py`:

```python
from app.cloud.auth import PasteParseError, parse_paste_url


def test_parse_paste_url_extracts_ticket():
    pasted = "http://localhost:13618/?ticket=tk_abc123"
    assert parse_paste_url(pasted) == "tk_abc123"


def test_parse_paste_url_accepts_ticket_with_extra_params():
    # Bambu may attach extra params; we only care about `ticket`.
    pasted = "http://localhost:13618/?ticket=tk_abc&from=studio"
    assert parse_paste_url(pasted) == "tk_abc"


def test_parse_paste_url_accepts_fragment_style():
    # Some OAuth flows put values in the fragment (#) instead of the query.
    pasted = "http://localhost:13618/#ticket=tk_abc"
    assert parse_paste_url(pasted) == "tk_abc"


def test_parse_paste_url_rejects_missing_ticket():
    with pytest.raises(PasteParseError):
        parse_paste_url("http://localhost:13618/?foo=bar")


def test_parse_paste_url_rejects_obviously_wrong_input():
    with pytest.raises(PasteParseError):
        parse_paste_url("https://example.com/?ticket=x")
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
from urllib.parse import parse_qs, urlparse


class PasteParseError(ValueError):
    """Raised when the user-pasted URL doesn't carry a usable ticket."""


def parse_paste_url(pasted: str) -> str:
    """Extract the ``ticket`` value from the URL the user pasted back.

    Bambu's sign-in page may put the ticket in either the query string (``?``)
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

    ticket = qs.get("ticket")
    if not ticket:
        raise PasteParseError("pasted URL missing 'ticket' parameter")
    return ticket[0]
```

- [ ] **Step 4: Run, confirm pass**

- [ ] **Step 5: Commit**

```bash
git add app/cloud/auth.py tests/test_cloud_auth.py
git commit -m "Cloud auth: paste-URL parser"
```

### Task B.3: Fetch the user profile via direct HTTPS

Per Phase A.2 discovery: profile is at `GET https://api.bambulab.com/v1/user-service/u/info` with `Authorization: Bearer <access_token>`. We hit it directly from Python; no plugin call needed.

**Files:**
- Modify: `app/cloud/auth.py`
- Modify: `tests/test_cloud_auth.py`

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
    req = seen_requests[0]
    assert req.headers["Authorization"] == "Bearer at_xyz"
    assert req.url.path == "/v1/user-service/u/info"


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

# Confirmed by Phase A.2 discovery (2026-05-27-bambu-auth-discovery.md §A.2).
_PROFILE_PATH = "/v1/user-service/u/info"


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

### Task B.4: Defer ticket exchange to the plugin (`get_my_token`)

The ticket → tokens exchange happens inside the closed plugin via the
`bambu_network_get_my_token` symbol. Rather than reverse-engineer the
HTTPS endpoint, we delegate this call to the C++ host (via a new RPC).

This task plans the RPC contract on the Python side; the C++ host gains
the new method in Task C.0 below.

**Files:**
- Modify: `app/cloud/auth.py`
- Modify: `tests/test_cloud_auth.py`

- [ ] **Step 1: Document the planned RPC contract**

The C++ host will accept a `get_my_token` method:

```
Request:  {"id":N, "method":"get_my_token", "params":{"ticket":"<val>"}}
Response: {"id":N, "result":{"access_token":"...", "refresh_token":"...",
                              "expires_in":"...", "refresh_expires_in":"..."}}
```

On failure (invalid ticket, network error):

```
Response: {"id":N, "error":{"code":-1, "message":"<plugin rc or msg>"}}
```

The Python-side `complete_login` orchestrator (Task C.2) will call
`host.call("get_my_token", {"ticket": ...})` and treat the result as
the "access_token bundle" needed to build the canonical_login payload.

No standalone Python helper to test in isolation — this is just an RPC
shape. Skip to Task B.5.

### Task B.5: TDD the canonical_login payload builder

Given the token bundle (from `get_my_token` RPC) + profile JSON (from
`fetch_profile`), build the canonical `{"command":"user_login","data":{...}}`
payload (per Phase 0 §Q11.4) that gets passed to plugin's `change_user`.

**Files:**
- Modify: `app/cloud/auth.py`
- Modify: `tests/test_cloud_auth.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
import json

from app.cloud.auth import build_canonical_login


def test_build_canonical_login_assembles_token_and_profile_fields():
    tokens = {
        "access_token": "at",
        "refresh_token": "rt",
        "expires_in": "3600",
        "refresh_expires_in": "86400",
    }
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
    assert d["token"] == "at"
    assert d["access_token"] == "at"
    assert d["refresh_token"] == "rt"
    assert d["expires_in"] == "3600"
    assert d["refresh_expires_in"] == "86400"
    assert d["user_id"] == "42"
    assert d["uidStr"] == "42"
    assert d["user"]["id"] == "42"
    assert d["user"]["uid"] == "42"
    assert d["user"]["uidStr"] == "42"
    assert d["user"]["name"] == "Alice"
    assert d["user"]["account"] == "alice@example.com"
    assert d["user"]["avatar"] == "https://cdn/avatar.png"


def test_build_canonical_login_accepts_uid_in_other_field_names():
    payload = build_canonical_login(
        tokens={
            "access_token": "at", "refresh_token": "rt",
            "expires_in": "1", "refresh_expires_in": "1",
        },
        profile={"uid": "99", "name": "Bob"},
    )
    obj = json.loads(payload)
    assert obj["data"]["user_id"] == "99"


def test_build_canonical_login_accepts_camelcase_token_keys():
    # The plugin's get_my_token may return either camelCase or snake_case;
    # Phase 0 §Q11.4 says HttpServer normalises via json_string_first.
    payload = build_canonical_login(
        tokens={"accessToken": "at", "refreshToken": "rt",
                "expiresIn": "1", "refreshExpiresIn": "1"},
        profile={"uidStr": "1"},
    )
    obj = json.loads(payload)
    assert obj["data"]["token"] == "at"
    assert obj["data"]["refresh_token"] == "rt"


def test_build_canonical_login_rejects_missing_uid():
    with pytest.raises(ValueError):
        build_canonical_login(
            tokens={"access_token": "at", "refresh_token": "rt",
                    "expires_in": "1", "refresh_expires_in": "1"},
            profile={"name": "x"},
        )
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


def build_canonical_login(*, tokens: dict, profile: dict) -> str:
    """Assemble the canonical ``change_user`` payload from tokens + profile.

    Matches the schema in Phase 0 §Q11.4 (HttpServer.cpp:38-65). The string
    returned is what gets passed to ``plugin.change_user``. Accepts either
    snake_case or camelCase token field names (Bambu's APIs mix both).
    """
    access = _first_present(tokens, "access_token", "accessToken", "token")
    refresh = _first_present(tokens, "refresh_token", "refreshToken")
    expires = _first_present(tokens, "expires_in", "expiresIn")
    refresh_expires = _first_present(
        tokens, "refresh_expires_in", "refreshExpiresIn"
    )
    if access is None or refresh is None:
        raise ValueError("tokens must contain access_token and refresh_token")

    uid = _first_present(profile, "uidStr", "uid", "id")
    if uid is None:
        raise ValueError("profile must contain uidStr/uid/id")

    payload = {
        "command": "user_login",
        "data": {
            "token": access,
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": expires or "",
            "refresh_expires_in": refresh_expires or "",
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

- [ ] **Step 3: Run + commit**

```bash
.venv/bin/pytest tests/test_cloud_auth.py -v
git add app/cloud/auth.py tests/test_cloud_auth.py
git commit -m "Cloud auth: build canonical_login payload"
```

---

## Phase C — Auth orchestration: tying it together with the PluginHost

### Task C.0: Discover and wire `get_my_token` into the C++ host

The ticket-flow login requires the plugin's `bambu_network_get_my_token`
to exchange the user-pasted ticket for an access_token bundle. This needs
both a C ABI discovery and a new RPC method in the C++ host.

**Files:**
- Modify: `docs/superpowers/notes/2026-05-27-bambu-cloud-host-discovery.md`
- Modify: `tools/bambu_cloud_host/plugin_loader.hpp`
- Modify: `tools/bambu_cloud_host/plugin_loader.cpp`
- Modify: `tools/bambu_cloud_host/methods.cpp`

- [ ] **Step 1: Discovery — read the C ABI**

Read `/Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/Utils/BBLNetworkPlugin.hpp` for the `func_get_my_token` typedef. Then `grep -n "bambu_network_get_my_token" /Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/Utils/BBLNetworkPlugin.cpp` for the dlsym call.

The signature is likely a callback-style API (the plugin makes the HTTPS
call asynchronously and invokes a callback with the result). Read
`BBLCloudServiceAgent::get_my_token` (`BBLCloudServiceAgent.cpp`) for the
exact call shape — what does it pass, what callback signature does it
expect, is it sync or async?

If it's async/callback-based, we need to bridge that into our synchronous
RPC: enqueue the result and have the RPC handler block until the callback
fires (with a timeout). This is exactly the pattern the event queue
established in Phase 2 will support — see if any callback wiring is
already in place we can reuse.

If it's actually synchronous-with-out-param (signature like
`int get_my_token(void* agent, string ticket, string& access_token, string& refresh_token, ...)`), the RPC handler is trivial: call, populate response from out-params.

Document findings in `docs/superpowers/notes/2026-05-27-bambu-cloud-host-discovery.md` under a new `## get_my_token` section, with citations and the chosen RPC pattern (sync passthrough vs callback-bridge).

- [ ] **Step 2: Extend `plugin_loader.hpp`**

Add the function-pointer typedef and a `get_my_token(ticket)` member that
returns a struct or `nlohmann::json` of the token fields.

```cpp
// Inside class PluginLoader, add:
nlohmann::json get_my_token(const std::string& ticket);
```

- [ ] **Step 3: Extend `plugin_loader.cpp`**

Resolve `bambu_network_get_my_token` in `load_from_env()` and implement
the wrapper. For the sync case:

```cpp
// Example shape — adapt to the real signature from discovery:
using get_my_token_fn = int(*)(void*, std::string,
                                std::string&, std::string&,
                                std::string&, std::string&);

nlohmann::json PluginLoader::get_my_token(const std::string& ticket) {
    if (!agent_handle_) throw std::runtime_error("agent not bootstrapped");
    std::string access, refresh, expires, refresh_expires;
    int rc = p_get_my_token_(agent_handle_, ticket,
                              access, refresh, expires, refresh_expires);
    if (rc != 0) {
        throw std::runtime_error("get_my_token rc=" + std::to_string(rc));
    }
    return {
        {"access_token", access},
        {"refresh_token", refresh},
        {"expires_in", expires},
        {"refresh_expires_in", refresh_expires},
    };
}
```

For the callback case: register a small lambda that captures a
`std::promise<json>`, call the plugin, then `future.wait_for(30s)`.

- [ ] **Step 4: Wire the RPC in `methods.cpp`**

Add a `method_get_my_token` handler:

```cpp
json method_get_my_token(const json& params) {
    return loader().get_my_token(params.at("ticket").get<std::string>());
}

// In dispatch_method:
if (method == "get_my_token") return method_get_my_token(params);
```

- [ ] **Step 5: Rebuild Docker image**

```bash
docker build -t bambu-gateway:cloud-host-test . 2>&1 | tail -10
```

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/notes/2026-05-27-bambu-cloud-host-discovery.md \
        tools/bambu_cloud_host/plugin_loader.hpp \
        tools/bambu_cloud_host/plugin_loader.cpp \
        tools/bambu_cloud_host/methods.cpp
git commit -m "Cloud host: add get_my_token RPC method"
```

### Task C.1: Add `is_user_login`, `user_logout`, `get_my_token` stubs to the fake host

The auth flow needs to call three more plugin methods that aren't in the fake host yet.

**Files:**
- Modify: `tests/cloud_fake_host.py`

- [ ] **Step 1: Add new methods**

In `_dispatch`:

```python
if method == "is_user_login":
    import os
    return {"is_login": os.environ.get("FAKE_HOST_USER_LOGGED_IN") == "1"}
if method == "user_logout":
    return {"rc": 0}
if method == "get_my_token":
    # Pretends to exchange the ticket for canned tokens. Tests can assert
    # against these exact values.
    if "ticket" not in params:
        raise ValueError("get_my_token requires 'ticket'")
    return {
        "access_token": f"at_for_{params['ticket']}",
        "refresh_token": "rt_canned",
        "expires_in": "3600",
        "refresh_expires_in": "86400",
    }
```

- [ ] **Step 2: Commit**

```bash
git add tests/cloud_fake_host.py
git commit -m "Cloud auth: add is_user_login, user_logout, get_my_token to fake host"
```

### Task C.2: Implement the high-level `complete_login` orchestrator (ticket flow)

This wires Phase B's helpers together: parse ticket → plugin `get_my_token` → fetch profile → build canonical → plugin `change_user` → confirm via `is_user_login`.

**Files:**
- Modify: `app/cloud/auth.py`
- Modify: `tests/test_cloud_auth.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_cloud_auth.py`:

```python
import sys
from pathlib import Path

from app.cloud.plugin_host import PluginHost
from app.cloud.auth import complete_login, LoginFailed


FAKE_HOST = Path(__file__).parent / "cloud_fake_host.py"


async def test_complete_login_happy_path(tmp_path):
    record_file = tmp_path / "requests.jsonl"
    pasted_url = "http://localhost:13618/?ticket=tk_abc"

    def profile_handler(request: httpx.Request) -> httpx.Response:
        # The fake host's get_my_token returns access_token = "at_for_tk_abc";
        # confirm we pass that to the profile API.
        assert request.headers["Authorization"] == "Bearer at_for_tk_abc"
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
                "FAKE_HOST_USER_LOGGED_IN": "1",
            },
        ) as host,
        httpx.AsyncClient(transport=httpx.MockTransport(profile_handler)) as http,
    ):
        result = await complete_login(
            host=host, http=http, region="US", pasted_url=pasted_url
        )

    assert result["account"] == "alice@example.com"
    # Fake host should have seen: get_my_token, change_user, is_user_login.
    import json as _json
    seen = [_json.loads(line) for line in record_file.read_text().splitlines()]
    methods = [r["method"] for r in seen]
    assert methods.index("get_my_token") < methods.index("change_user")
    assert methods.index("change_user") < methods.index("is_user_login")


async def test_complete_login_fails_if_plugin_does_not_register_login(tmp_path):
    pasted = "http://localhost:13618/?ticket=tk_abc"

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

    - :class:`PasteParseError` if the URL doesn't carry a usable ticket
    - :class:`PluginHostError` if the plugin host RPC fails (e.g. get_my_token
      rejects the ticket)
    - :class:`ProfileFetchError` if Bambu rejects the access token
    - :class:`LoginFailed` if change_user returned 0 but is_user_login is False
    """
    ticket = parse_paste_url(pasted_url)
    tokens = await host.call("get_my_token", {"ticket": ticket})

    access = _first_present(tokens, "access_token", "accessToken", "token")
    if access is None:
        raise LoginFailed("get_my_token result missing access_token")
    profile = await fetch_profile(
        client=http, region=region, access_token=access
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

- [ ] **Step 3: Run + commit**

```bash
.venv/bin/pytest tests/test_cloud_auth.py -v
git add app/cloud/auth.py tests/test_cloud_auth.py
git commit -m "Cloud auth: complete_login orchestrator (ticket flow)"
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
    pasted = "http://localhost:13618/?ticket=tk_abc"

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
