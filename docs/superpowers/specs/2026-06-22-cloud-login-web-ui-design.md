# Cloud Login Web UI — Design

**Date:** 2026-06-22
**Status:** Approved

## Problem

Bambu Cloud mode (`BAMBU_CLOUD_ENABLED=true`) lets the gateway reach printers
through a Bambu account instead of LAN/Developer Mode. The login flow already
exists as `/api/cloud/auth/*` endpoints, but there is **no UI** for it — the
first sign-in currently requires hand-running `curl`. This adds a friendly web
UI on the Settings page.

## Constraint that shapes the design

After signing in on `bambulab.com`, the browser is redirected to
`http://localhost:13618/?ticket=…`. Nothing listens there from the user's
browser, so it shows a "connection refused" page. The SPA **cannot intercept**
this redirect (it leaves the app entirely), so a manual copy-paste of that URL
back into the gateway is unavoidable. The UI's job is to make that paste step
clear and painless, not to eliminate it.

The user profile (name/account/avatar) is only obtainable during login, when the
access token is in hand (`fetch_profile` needs a `Bearer` token). The plugin
exposes no "get current token" RPC, so the profile **cannot be re-fetched** after
a restart. To show the account persistently, the profile is **cached to disk on
login** and reloaded on startup.

## Backend changes (`app/`)

### 1. Persist profile on login
On successful `complete_login`, write the profile to
`${BAMBU_CLOUD_PLUGIN_DIR}/state/gateway_profile.json` (inside the existing
`/data` volume, so it survives restarts) and hold it in
`app.state.cloud_profile`. Stored fields: `name`, `account`, `avatar`, `uid`.
Delete the file and clear `app.state.cloud_profile` on logout.

A small helper module (e.g. `app/cloud/profile_store.py`) owns read/write/clear
of this file so the logic is testable in isolation.

### 2. Load profile on startup
During cloud startup, when the restored session reports signed-in
(`is_signed_in` true), load the cached profile from disk into
`app.state.cloud_profile`. If the file is missing/corrupt, leave it `None` (the
UI then shows a generic "Signed in" state — see frontend fallback).

### 3. Enrich `GET /api/cloud/auth/status`
Current response: `{ "signed_in": bool }`.
New response: `{ "signed_in": bool, "profile": CloudProfile | null, "region": "US"|"CN" }`
where `profile` is `null` when not signed in or when the cache is absent.
`region` comes from `settings.bambu_cloud_region`.

The `/paste` and `/logout` routes also update `app.state.cloud_profile` and the
on-disk cache as a side effect (paste writes it from the returned profile; logout
clears it).

### 4. Add `cloud` capability flag
Extend `CapabilitiesResponse` (`app/models.py`) and `GET /api/capabilities`
(`app/main.py`) with `cloud: bool`, set to
`getattr(app.state, "cloud_host", None) is not None` — i.e. true only when the
cloud host process is actually running. This mirrors how `push` gates the Push
section and correctly reads false when `BAMBU_CLOUD_ENABLED=true` but the host
failed to start (graceful-degradation path).

## Frontend changes (`web/src/`)

### 5. API module + types
New `lib/api/cloud.ts`:
- `getCloudAuthUrl(): Promise<{ url: string }>` → `GET /api/cloud/auth/url`
- `getCloudStatus(): Promise<CloudStatus>` → `GET /api/cloud/auth/status`
- `pasteCloudLogin(pastedUrl: string): Promise<{ profile: CloudProfile; connected: boolean }>` → `POST /api/cloud/auth/paste`
- `cloudLogout(): Promise<{ ok: boolean }>` → `POST /api/cloud/auth/logout`

`lib/api/types.ts`: add `CloudProfile` (`{ name: string; account: string; avatar: string; uid?: string }`),
`CloudStatus` (`{ signed_in: boolean; profile: CloudProfile | null; region: string }`),
and `cloud: boolean` on `Capabilities`.

### 6. `CloudSection` component
New `components/settings/cloud-section.tsx`, following the `PushSection`
pattern (Card, capability-gated query, Skeleton while loading). Rendered at the
**top of the Settings page, above `PrintersSection`**.

States:
- **Cloud disabled** (`capabilities.cloud === false`): a note — "Cloud mode is
  off. Set `BAMBU_CLOUD_ENABLED=true` in the gateway environment to connect
  through your Bambu account." Links to the README section.
- **Signed in** (`status.signed_in === true`): avatar (fallback to an icon when
  `avatar` empty), account name (`profile.name` → fallback `profile.account` →
  fallback "Signed in to Bambu Cloud"), region badge, and a **Sign out** button.
  Sign out calls `cloudLogout`, invalidates the status + printers queries, toasts
  on success/failure.
- **Not signed in** (`status.signed_in === false`): a 2-step flow.
  1. **"Open Bambu sign-in"** button → `getCloudAuthUrl()` then
     `window.open(url, '_blank')`.
  2. A paste `Input` + **"Complete sign-in"** button → `pasteCloudLogin(url)`.
     On success: invalidate status + printers queries, toast, clear field.
  Inline help text explains: "After signing in, your browser will show a
  'connection refused' page at `localhost:13618` — that's expected. Copy the full
  URL from the address bar and paste it here."
  Errors (400 bad URL, 502 login failed) surface via toast + inline message using
  `ApiError.detail`.

### 7. Settings page wiring
`routes/settings.tsx`: render `<CloudSection />` first, above `<PrintersSection />`.

## Testing

- **Frontend** `cloud-section.test.tsx` (RTL + mocked `lib/api/cloud`):
  disabled-note state, signed-in profile render + sign-out, not-signed-in paste
  happy path, paste error path.
- **Backend** (extend `tests/test_cloud_auth_routes.py` / add as needed):
  - `/status` returns `profile` + `region` when signed in, `null` profile when not.
  - `/paste` writes the profile cache; `/logout` clears it.
  - `profile_store` read/write/clear round-trip, including missing/corrupt file.
  - `/api/capabilities` includes `cloud`, true only when `cloud_host` is set.

## Out of scope (YAGNI)

- Automatic redirect capture (impossible — redirect leaves the SPA).
- Token-refresh UI, multi-account switching, dedicated nav route.
- Showing per-printer cloud link status (handled by the existing dashboard).
