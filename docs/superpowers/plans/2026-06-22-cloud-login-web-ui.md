# Cloud Login Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Settings-page UI to sign in to / out of Bambu Cloud, replacing the current curl-only login flow.

**Architecture:** The backend already exposes `/api/cloud/auth/{url,status,paste,logout}`. We enrich `/status` to return the cached account profile + region, persist that profile to disk so it survives restarts, and add a `cloud` capability flag. The React SPA gets a new `CloudSection` at the top of `/settings` that drives the open-in-tab → paste-back login flow and shows the signed-in account.

**Tech Stack:** Python 3.12 / FastAPI / pytest (backend); React 18 + TypeScript + TanStack Query + shadcn/ui + Vitest + React Testing Library + Sonner (frontend).

## Global Constraints

- Backend tests run with `.venv/bin/pytest` (system Python lacks FastAPI/httpx/jwt). Config in `pytest.ini`.
- Frontend tests run with `npm test` (Vitest) from `web/`.
- TypeScript API types in `web/src/lib/api/types.ts` mirror `app/models.py` field names verbatim — snake_case, no camelCase conversion.
- Cloud auth endpoints already exist; do NOT change their paths. Only enrich `/status` response and add side effects to `/paste` and `/logout`.
- Profile cache path: `${BAMBU_CLOUD_PLUGIN_DIR}/state/gateway_profile.json` (i.e. `settings.bambu_cloud_plugin_dir / "state" / "gateway_profile.json"`).
- Profile fields stored/exposed: `name`, `account`, `avatar`, `uid` (all strings; missing → `""`).
- `cloud` capability = `getattr(app.state, "cloud_host", None) is not None`.
- Follow existing component patterns: `web/src/components/settings/push-section.tsx` (capability-gated Card) and `printers-section.tsx`.

---

### Task 1: Profile store (persist account to disk)

**Files:**
- Create: `app/cloud/profile_store.py`
- Test: `tests/test_cloud_profile_store.py`

**Interfaces:**
- Produces:
  - `CloudProfile` dataclass: `name: str`, `account: str`, `avatar: str`, `uid: str` (all default `""`).
  - `CloudProfile.from_api(profile: dict) -> CloudProfile` — builds from Bambu's profile dict (keys `name`, `account`, `avatar`, and uid under `uidStr`/`uid`/`id`).
  - `CloudProfile.to_dict() -> dict` — `{"name","account","avatar","uid"}`.
  - `save_profile(path: Path, profile: CloudProfile) -> None` — writes JSON, creating parent dirs.
  - `load_profile(path: Path) -> CloudProfile | None` — returns `None` if file missing or corrupt.
  - `clear_profile(path: Path) -> None` — deletes file if present (no error if absent).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cloud_profile_store.py
from __future__ import annotations

from app.cloud.profile_store import (
    CloudProfile,
    clear_profile,
    load_profile,
    save_profile,
)


def test_from_api_extracts_uid_and_fields():
    p = CloudProfile.from_api(
        {"uidStr": "42", "name": "Alice", "account": "a@b", "avatar": "http://x/y.png"}
    )
    assert p == CloudProfile(name="Alice", account="a@b", avatar="http://x/y.png", uid="42")


def test_from_api_falls_back_for_missing_fields():
    p = CloudProfile.from_api({"uid": "7"})
    assert p == CloudProfile(name="", account="", avatar="", uid="7")


def test_save_load_round_trip(tmp_path):
    path = tmp_path / "state" / "gateway_profile.json"
    profile = CloudProfile(name="Alice", account="a@b", avatar="", uid="42")
    save_profile(path, profile)
    assert load_profile(path) == profile


def test_load_missing_returns_none(tmp_path):
    assert load_profile(tmp_path / "nope.json") is None


def test_load_corrupt_returns_none(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    assert load_profile(path) is None


def test_clear_removes_file(tmp_path):
    path = tmp_path / "state" / "gateway_profile.json"
    save_profile(path, CloudProfile(uid="1"))
    clear_profile(path)
    assert load_profile(path) is None
    clear_profile(path)  # idempotent — no error on missing
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cloud_profile_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.cloud.profile_store'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/cloud/profile_store.py
"""Persist the signed-in Bambu account profile to disk.

The profile is only obtainable during login (it needs the access token, which
the plugin does not re-expose). Caching it here lets the UI show which account
is linked after a gateway restart.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger("bambu.cloud.profile")


@dataclass(frozen=True)
class CloudProfile:
    name: str = ""
    account: str = ""
    avatar: str = ""
    uid: str = ""

    @classmethod
    def from_api(cls, profile: dict) -> "CloudProfile":
        uid = profile.get("uidStr") or profile.get("uid") or profile.get("id") or ""
        return cls(
            name=str(profile.get("name", "") or ""),
            account=str(profile.get("account", "") or ""),
            avatar=str(profile.get("avatar", "") or ""),
            uid=str(uid),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def save_profile(path: Path, profile: CloudProfile) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile.to_dict()))


def load_profile(path: Path) -> CloudProfile | None:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None
    except OSError as exc:
        logger.warning("could not read cloud profile cache: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    return CloudProfile(
        name=str(data.get("name", "")),
        account=str(data.get("account", "")),
        avatar=str(data.get("avatar", "")),
        uid=str(data.get("uid", "")),
    )


def clear_profile(path: Path) -> None:
    path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cloud_profile_store.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app/cloud/profile_store.py tests/test_cloud_profile_store.py
git commit -m "feat: persist Bambu cloud account profile to disk"
```

---

### Task 2: Enrich `/status`, persist on paste, clear on logout

**Files:**
- Modify: `app/cloud/auth_routes.py`
- Test: `tests/test_cloud_auth_routes.py` (extend)

**Interfaces:**
- Consumes: `CloudProfile`, `save_profile`, `load_profile`, `clear_profile` from Task 1; `settings.bambu_cloud_plugin_dir`, `settings.bambu_cloud_region`.
- Produces:
  - `_profile_cache_path() -> Path` helper in `auth_routes.py` returning `settings.bambu_cloud_plugin_dir / "state" / "gateway_profile.json"`.
  - `GET /api/cloud/auth/status` now returns `{"signed_in": bool, "profile": dict | None, "region": str}` where `profile` is `CloudProfile.to_dict()` or `None`.
  - `POST /api/cloud/auth/paste` writes the cache and sets `request.app.state.cloud_profile`.
  - `POST /api/cloud/auth/logout` clears the cache and sets `request.app.state.cloud_profile = None`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cloud_auth_routes.py`:

```python
def test_status_returns_profile_and_region_after_paste(cloud_app):
    pasted = "http://localhost:13618/?ticket=tk_abc"

    def profile_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"uidStr": "42", "name": "Alice", "account": "a@b"}
        )

    with patch(
        "app.cloud.auth_routes._make_http_client",
        return_value=httpx.AsyncClient(
            transport=httpx.MockTransport(profile_handler)
        ),
    ):
        cloud_app.post("/api/cloud/auth/paste", json={"pasted_url": pasted})

    resp = cloud_app.get("/api/cloud/auth/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["signed_in"] is True
    assert body["region"] == "US"
    assert body["profile"]["name"] == "Alice"
    assert body["profile"]["account"] == "a@b"


def test_logout_clears_profile(cloud_app):
    pasted = "http://localhost:13618/?ticket=tk_abc"

    def profile_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"uidStr": "42", "name": "Alice", "account": "a@b"})

    with patch(
        "app.cloud.auth_routes._make_http_client",
        return_value=httpx.AsyncClient(transport=httpx.MockTransport(profile_handler)),
    ):
        cloud_app.post("/api/cloud/auth/paste", json={"pasted_url": pasted})

    cloud_app.post("/api/cloud/auth/logout")
    resp = cloud_app.get("/api/cloud/auth/status")
    assert resp.json()["profile"] is None
```

Note: the fake host reports `is_login` true (`FAKE_HOST_USER_LOGGED_IN=1`), so `signed_in` stays true after logout in this fixture — the assertion only checks `profile` is cleared.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cloud_auth_routes.py -v -k "profile or region"`
Expected: FAIL — `KeyError: 'region'` / `profile` missing from status response.

- [ ] **Step 3: Write minimal implementation**

In `app/cloud/auth_routes.py`, add imports near the top:

```python
from pathlib import Path

from app.cloud.profile_store import (
    CloudProfile,
    clear_profile,
    load_profile,
    save_profile,
)
```

Add a path helper after `_require_host`:

```python
def _profile_cache_path() -> Path:
    return settings.bambu_cloud_plugin_dir / "state" / "gateway_profile.json"
```

Replace the `get_status` handler:

```python
@router.get("/status")
async def get_status(request: Request) -> dict:
    host = _require_host(request)
    try:
        signed_in = await auth.is_signed_in(host=host)
    except PluginHostError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    profile = getattr(request.app.state, "cloud_profile", None)
    if profile is None and signed_in:
        profile = load_profile(_profile_cache_path())
        request.app.state.cloud_profile = profile
    return {
        "signed_in": signed_in,
        "profile": profile.to_dict() if profile else None,
        "region": settings.bambu_cloud_region,
    }
```

In `post_paste`, after a successful `complete_login` (right before computing `connected`), cache the profile:

```python
    cached = CloudProfile.from_api(profile)
    save_profile(_profile_cache_path(), cached)
    request.app.state.cloud_profile = cached
```

In `post_logout`, after `auth.logout(...)` succeeds, clear it:

```python
    clear_profile(_profile_cache_path())
    request.app.state.cloud_profile = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cloud_auth_routes.py -v`
Expected: PASS (all, including the two new tests)

- [ ] **Step 5: Commit**

```bash
git add app/cloud/auth_routes.py tests/test_cloud_auth_routes.py
git commit -m "feat: return cached profile + region from cloud status"
```

---

### Task 3: Load cached profile on startup + `cloud` capability flag

**Files:**
- Modify: `app/main.py` (in `_start_cloud`, the lifespan `app.state` init, and `get_capabilities`)
- Modify: `app/models.py` (`CapabilitiesResponse`)
- Test: `tests/test_cloud_config.py` (add a capabilities test) and `tests/test_capabilities.py` if present (see Step 1)

**Interfaces:**
- Consumes: `load_profile` from Task 1; `app.state.cloud_host`.
- Produces:
  - `CapabilitiesResponse.cloud: bool`.
  - `GET /api/capabilities` returns `cloud=True` only when `app.state.cloud_host is not None`.
  - `app.state.cloud_profile` initialized to `None` in lifespan and populated on startup when signed in.

- [ ] **Step 1: Write the failing test**

Find where capabilities is tested: `grep -rn "capabilities" tests/`. Add a test to `tests/test_cloud_config.py` (it already builds cloud + non-cloud apps via `cloud_app_factory`):

```python
def test_capabilities_reports_cloud_true_in_cloud_mode(cloud_app_factory):
    with cloud_app_factory(fake_env={"FAKE_HOST_USER_LOGGED_IN": "1"}) as client:
        body = client.get("/api/capabilities").json()
        assert body["cloud"] is True


def test_capabilities_reports_cloud_false_without_cloud(client):
    # `client` is the default non-cloud app fixture from conftest.
    body = client.get("/api/capabilities").json()
    assert body["cloud"] is False
```

If the default non-cloud `client` fixture name differs, use the project's existing non-cloud TestClient fixture (check `grep -n "def client" tests/conftest.py`).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cloud_config.py -v -k capabilities`
Expected: FAIL with `KeyError: 'cloud'`

- [ ] **Step 3: Write minimal implementation**

In `app/models.py`, add the field to `CapabilitiesResponse`:

```python
class CapabilitiesResponse(BaseModel):
    push: bool
    live_activities: bool
    cloud: bool = False
    version: str = ""
```

In `app/main.py` `get_capabilities`:

```python
@app.get("/api/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities():
    return CapabilitiesResponse(
        push=settings.push_enabled,
        live_activities=settings.push_enabled,
        cloud=getattr(app.state, "cloud_host", None) is not None,
        version=app.version,
    )
```

In `app/main.py` lifespan, alongside the other `app.state.cloud_* = None` init lines (around line 350-353), add:

```python
        app.state.cloud_profile = None
```

In `_start_cloud`, inside the `if await cloud_auth.is_signed_in(host=host):` branch (around line 318), load the cached profile so a restored session shows the account. Add at the top of that branch:

```python
        from app.cloud.profile_store import load_profile
        app.state.cloud_profile = load_profile(
            settings.bambu_cloud_plugin_dir / "state" / "gateway_profile.json"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cloud_config.py -v -k capabilities`
Expected: PASS

Then full backend sweep: `.venv/bin/pytest -q`
Expected: PASS (no regressions)

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/models.py tests/test_cloud_config.py
git commit -m "feat: expose cloud capability + restore profile on startup"
```

---

### Task 4: Frontend API module + types

**Files:**
- Create: `web/src/lib/api/cloud.ts`
- Modify: `web/src/lib/api/types.ts`

**Interfaces:**
- Consumes: `fetchJson` from `web/src/lib/api/client.ts`.
- Produces (types in `types.ts`):
  - `CloudProfile` = `{ name: string; account: string; avatar: string; uid: string }`
  - `CloudStatus` = `{ signed_in: boolean; profile: CloudProfile | null; region: string }`
  - `Capabilities` gains `cloud: boolean`
- Produces (functions in `cloud.ts`):
  - `getCloudAuthUrl(): Promise<{ url: string }>`
  - `getCloudStatus(): Promise<CloudStatus>`
  - `pasteCloudLogin(pastedUrl: string): Promise<{ profile: CloudProfile; connected: boolean }>`
  - `cloudLogout(): Promise<{ ok: boolean }>`

- [ ] **Step 1: Add the types**

In `web/src/lib/api/types.ts`, update the `Capabilities` interface and add cloud types right after it:

```typescript
export interface Capabilities {
  push: boolean;
  live_activities: boolean;
  cloud: boolean;
  version: string;
}

// --- Settings: Bambu Cloud account (mirror app/cloud/profile_store.py) ---

export interface CloudProfile {
  name: string;
  account: string;
  avatar: string;
  uid: string;
}

export interface CloudStatus {
  signed_in: boolean;
  profile: CloudProfile | null;
  region: string;
}
```

- [ ] **Step 2: Write the API module**

```typescript
// web/src/lib/api/cloud.ts
import { fetchJson } from './client';
import type { CloudProfile, CloudStatus } from './types';

export async function getCloudAuthUrl(): Promise<{ url: string }> {
  return fetchJson<{ url: string }>('/api/cloud/auth/url');
}

export async function getCloudStatus(): Promise<CloudStatus> {
  return fetchJson<CloudStatus>('/api/cloud/auth/status');
}

export async function pasteCloudLogin(
  pastedUrl: string,
): Promise<{ profile: CloudProfile; connected: boolean }> {
  return fetchJson('/api/cloud/auth/paste', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pasted_url: pastedUrl }),
  });
}

export async function cloudLogout(): Promise<{ ok: boolean }> {
  return fetchJson('/api/cloud/auth/logout', { method: 'POST' });
}
```

- [ ] **Step 3: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/api/cloud.ts web/src/lib/api/types.ts
git commit -m "feat: add cloud auth API client + types"
```

---

### Task 5: `CloudSection` component + Settings wiring

**Files:**
- Create: `web/src/components/settings/cloud-section.tsx`
- Create: `web/src/components/settings/cloud-section.test.tsx`
- Modify: `web/src/routes/settings.tsx`

**Interfaces:**
- Consumes: `getCapabilities` (`@/lib/api/capabilities`), `getCloudStatus`, `getCloudAuthUrl`, `pasteCloudLogin`, `cloudLogout` (`@/lib/api/cloud`); `Card`, `Button`, `Input`, `Skeleton` UI primitives; `toast` from `sonner`; `useQueryClient`/`useQuery`/`useMutation` from `@tanstack/react-query`.
- Produces: `CloudSection` (default-exported-style named export `export function CloudSection()`), rendered first in `SettingsRoute`.

- [ ] **Step 1: Write the failing test**

```typescript
// web/src/components/settings/cloud-section.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { CloudSection } from './cloud-section';

vi.mock('@/lib/api/capabilities', () => ({ getCapabilities: vi.fn() }));
vi.mock('@/lib/api/cloud', () => ({
  getCloudStatus: vi.fn(),
  getCloudAuthUrl: vi.fn(),
  pasteCloudLogin: vi.fn(),
  cloudLogout: vi.fn(),
}));

import { getCapabilities } from '@/lib/api/capabilities';
import {
  cloudLogout,
  getCloudAuthUrl,
  getCloudStatus,
  pasteCloudLogin,
} from '@/lib/api/cloud';

function renderSection() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <CloudSection />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('CloudSection', () => {
  test('shows disabled note when cloud capability is false', async () => {
    vi.mocked(getCapabilities).mockResolvedValue({
      push: false, live_activities: false, cloud: false, version: '1',
    });
    renderSection();
    expect(await screen.findByText(/Cloud mode is off/i)).toBeInTheDocument();
    expect(getCloudStatus).not.toHaveBeenCalled();
  });

  test('shows the signed-in account and signs out', async () => {
    vi.mocked(getCapabilities).mockResolvedValue({
      push: false, live_activities: false, cloud: true, version: '1',
    });
    vi.mocked(getCloudStatus).mockResolvedValue({
      signed_in: true,
      region: 'US',
      profile: { name: 'Alice', account: 'a@b', avatar: '', uid: '42' },
    });
    vi.mocked(cloudLogout).mockResolvedValue({ ok: true });
    renderSection();

    expect(await screen.findByText('Alice')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /sign out/i }));
    await waitFor(() => expect(cloudLogout).toHaveBeenCalled());
  });

  test('completes login via paste', async () => {
    vi.mocked(getCapabilities).mockResolvedValue({
      push: false, live_activities: false, cloud: true, version: '1',
    });
    vi.mocked(getCloudStatus).mockResolvedValue({
      signed_in: false, region: 'US', profile: null,
    });
    vi.mocked(pasteCloudLogin).mockResolvedValue({
      profile: { name: 'Alice', account: 'a@b', avatar: '', uid: '42' },
      connected: true,
    });
    renderSection();

    const input = await screen.findByPlaceholderText(/localhost:13618/i);
    await userEvent.type(input, 'http://localhost:13618/?ticket=tk_abc');
    await userEvent.click(screen.getByRole('button', { name: /complete sign-in/i }));
    await waitFor(() =>
      expect(pasteCloudLogin).toHaveBeenCalledWith('http://localhost:13618/?ticket=tk_abc'),
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/components/settings/cloud-section.test.tsx`
Expected: FAIL — cannot find module `./cloud-section`.

- [ ] **Step 3: Write the component**

```tsx
// web/src/components/settings/cloud-section.tsx
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Cloud, ExternalLink, LogOut } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { getCapabilities } from '@/lib/api/capabilities';
import {
  cloudLogout,
  getCloudAuthUrl,
  getCloudStatus,
  pasteCloudLogin,
} from '@/lib/api/cloud';
import { ApiError } from '@/lib/api/client';

export function CloudSection() {
  const queryClient = useQueryClient();
  const [pasteValue, setPasteValue] = useState('');

  const capsQuery = useQuery({
    queryKey: ['capabilities'],
    queryFn: getCapabilities,
    staleTime: 60_000,
  });
  const cloudEnabled = capsQuery.data?.cloud === true;

  const statusQuery = useQuery({
    queryKey: ['cloud-status'],
    queryFn: getCloudStatus,
    enabled: cloudEnabled,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['cloud-status'] });
    queryClient.invalidateQueries({ queryKey: ['printers'] });
  };

  const openSignIn = useMutation({
    mutationFn: getCloudAuthUrl,
    onSuccess: ({ url }) => window.open(url, '_blank', 'noopener,noreferrer'),
    onError: (e) => toast.error(`Couldn't open sign-in: ${describe(e)}`),
  });

  const paste = useMutation({
    mutationFn: () => pasteCloudLogin(pasteValue.trim()),
    onSuccess: ({ profile }) => {
      toast.success(`Signed in as ${profile.name || profile.account || 'your account'}`);
      setPasteValue('');
      invalidate();
    },
    onError: (e) => toast.error(`Sign-in failed: ${describe(e)}`),
  });

  const signOut = useMutation({
    mutationFn: cloudLogout,
    onSuccess: () => {
      toast.success('Signed out of Bambu Cloud');
      invalidate();
    },
    onError: (e) => toast.error(`Sign-out failed: ${describe(e)}`),
  });

  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-base font-semibold text-white px-1">Cloud Account</h2>
      <Card className="bg-card border-border p-4 flex flex-col gap-3">
        {capsQuery.isLoading ? (
          <Skeleton className="h-5 w-2/3" />
        ) : !cloudEnabled ? (
          <p className="text-sm text-text-1">
            Cloud mode is off. Set{' '}
            <code className="text-text-0">BAMBU_CLOUD_ENABLED=true</code> in the
            gateway environment to connect through your Bambu account — see{' '}
            <a
              href="https://github.com/leolobato/bambu-gateway#bambu-cloud-mode-optional"
              target="_blank"
              rel="noreferrer"
              className="text-accent hover:underline"
            >
              the README
            </a>
            .
          </p>
        ) : statusQuery.isLoading ? (
          <Skeleton className="h-14 rounded-2xl" />
        ) : statusQuery.data?.signed_in ? (
          <SignedIn
            name={statusQuery.data.profile?.name || statusQuery.data.profile?.account || 'Signed in to Bambu Cloud'}
            account={statusQuery.data.profile?.account ?? ''}
            avatar={statusQuery.data.profile?.avatar ?? ''}
            region={statusQuery.data.region}
            onSignOut={() => signOut.mutate()}
            signingOut={signOut.isPending}
          />
        ) : (
          <SignIn
            value={pasteValue}
            onChange={setPasteValue}
            onOpen={() => openSignIn.mutate()}
            opening={openSignIn.isPending}
            onPaste={() => paste.mutate()}
            pasting={paste.isPending}
          />
        )}
      </Card>
    </section>
  );
}

function describe(e: unknown): string {
  return e instanceof ApiError ? e.detail : 'unexpected error';
}

function SignedIn(props: {
  name: string;
  account: string;
  avatar: string;
  region: string;
  onSignOut: () => void;
  signingOut: boolean;
}) {
  return (
    <div className="flex items-center gap-3">
      {props.avatar ? (
        <img src={props.avatar} alt="" className="h-10 w-10 rounded-full object-cover" />
      ) : (
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-surface-1">
          <Cloud className="h-5 w-5 text-accent" aria-hidden />
        </div>
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold text-text-0">{props.name}</p>
        <p className="truncate text-xs text-text-2">
          {props.account ? `${props.account} · ` : ''}{props.region} region
        </p>
      </div>
      <Button
        type="button"
        onClick={props.onSignOut}
        disabled={props.signingOut}
        className="rounded-full h-8 px-3 bg-surface-1 hover:bg-surface-2 text-text-1 border-0 text-[13px] font-semibold"
      >
        <LogOut className="w-3.5 h-3.5 mr-1" aria-hidden /> Sign out
      </Button>
    </div>
  );
}

function SignIn(props: {
  value: string;
  onChange: (v: string) => void;
  onOpen: () => void;
  opening: boolean;
  onPaste: () => void;
  pasting: boolean;
}) {
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-text-1">
        Sign in with your Bambu account to control cloud-connected printers.
      </p>
      <ol className="flex flex-col gap-2 text-sm text-text-1">
        <li className="flex items-center gap-2">
          <span className="text-text-2">1.</span>
          <Button
            type="button"
            onClick={props.onOpen}
            disabled={props.opening}
            className="rounded-full h-8 px-3 bg-surface-1 hover:bg-surface-2 text-accent border-0 text-[13px] font-semibold"
          >
            <ExternalLink className="w-3.5 h-3.5 mr-1" aria-hidden /> Open Bambu sign-in
          </Button>
        </li>
        <li className="text-text-2 pl-5 text-xs">
          After signing in, your browser will show a “this site can't be reached”
          page at <code className="text-text-1">localhost:13618</code> — that's
          expected. Copy the full URL from the address bar and paste it below.
        </li>
        <li className="flex flex-col gap-2 sm:flex-row">
          <Input
            value={props.value}
            onChange={(e) => props.onChange(e.target.value)}
            placeholder="http://localhost:13618/?ticket=…"
            className="flex-1"
          />
          <Button
            type="button"
            onClick={props.onPaste}
            disabled={props.pasting || props.value.trim() === ''}
            className="rounded-full h-9 px-4 bg-accent hover:bg-accent/90 text-black border-0 text-[13px] font-semibold"
          >
            Complete sign-in
          </Button>
        </li>
      </ol>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npx vitest run src/components/settings/cloud-section.test.tsx`
Expected: PASS (3 tests)

- [ ] **Step 5: Wire into Settings page**

Modify `web/src/routes/settings.tsx`:

```tsx
import { CloudSection } from '@/components/settings/cloud-section';
import { PrintersSection } from '@/components/settings/printers-section';
import { PushSection } from '@/components/settings/push-section';
import { AboutSection } from '@/components/settings/about-section';

export default function SettingsRoute() {
  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-[28px] font-extrabold tracking-tight text-white">Settings</h1>
      </header>
      <CloudSection />
      <PrintersSection />
      <PushSection />
      <AboutSection />
    </div>
  );
}
```

- [ ] **Step 6: Typecheck + full frontend test run**

Run: `cd web && npx tsc --noEmit && npx vitest run`
Expected: no type errors; all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add web/src/components/settings/cloud-section.tsx web/src/components/settings/cloud-section.test.tsx web/src/routes/settings.tsx
git commit -m "feat: add Bambu Cloud account section to Settings"
```

---

### Task 6: Build the SPA + final verification

**Files:**
- Modify: `app/static/dist/**` (generated by the build)

- [ ] **Step 1: Build the web bundle**

Run: `cd web && npm run build`
Expected: build succeeds, emits to `app/static/dist/`.

- [ ] **Step 2: Full backend test sweep**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 3: Commit the built assets**

```bash
git add app/static/dist web/
git commit -m "build: bundle cloud account settings UI"
```

---

## Self-Review Notes

- **Spec coverage:** profile persistence (Task 1), `/status` enrichment + paste/logout side effects (Task 2), startup restore + `cloud` capability (Task 3), API module/types (Task 4), `CloudSection` three states + placement above Printers (Task 5), build (Task 6). All spec sections mapped.
- **Type consistency:** `CloudProfile` fields `name/account/avatar/uid` identical across `profile_store.py`, `types.ts`, and component props. `CloudStatus` shape matches the backend `/status` dict. `getCloudStatus`/`pasteCloudLogin`/`cloudLogout`/`getCloudAuthUrl` names identical between `cloud.ts` and the test mocks/component.
- **Fixture caveat:** the cloud test fixture pins `is_login` true, so logout-clears-profile is asserted on the `profile` field only (documented inline in Task 2). Task 3's capability tests depend on the conftest non-cloud `client` fixture name — Step 1 instructs verifying the actual fixture name before writing.
