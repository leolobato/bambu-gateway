# Bambu Cloud Plugin — Discovery Notes

Resolutions for the four open questions in `docs/superpowers/specs/2026-05-26-bambu-cloud-plugin-design.md` §11.

Source tree probed: `/Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/` (read-only).

## Q11.1 — OAuth redirect URI

> **Note (re-resolved 2026-05-26):** The original finding cited `OrcaCloudServiceAgent` and `OAuthJob.cpp` — both of
> which belong to OrcaSlicer's own auth cloud, not Bambu's. This section has been rewritten to cite the correct BBL
> (Bambu) path: `WebUserLoginDialog.cpp`, `BBLCloudServiceAgent.cpp`, and `HttpServer.cpp`.

**Finding:** In the BBL path the `redirect_uri` value is **generated inside the closed-source network plugin binary
(`.so`/`.dylib`)** and is not visible in any open-source C++ file. `BBLCloudServiceAgent::build_login_cmd()` is a
pure pass-through to a function pointer resolved via `dlsym` under the name `bambu_network_build_login_cmd`; this
symbol lives inside the proprietary BBL network plugin and returns a JSON blob that includes a `pkce` object
containing `loopback_port` and/or `redirect_uri`. The open-source code only reads those fields back out — it does
not construct them.

**The actual interception mechanism** is a local TCP server (port `LOCALHOST_PORT = 13618` by default, or the port
extracted from the plugin-supplied `pkce.loopback_port`). After calling `build_login_cmd()`, `WebUserLoginDialog`
starts this server (`wxGetApp().start_http_server(port)`) and passes the `base_url` (`http://localhost:<port>`) to
the hosted Bambu login page via `window.postMessage`. The Bambu-hosted sign-in page then redirects the actual OAuth
callback to that loopback URL. The loopback server handler (`HttpServer::bbl_auth_handle_request`) receives the
request and handles three redirect shapes:
- `?ticket=<val>` — exchanges the ticket for an access token via `get_my_token()`, then `get_my_profile()`
- `?code=<val>&state=<val>` — standard PKCE auth-code: calls `agent->change_user()` with the code/state JSON
- `?access_token=<val>&...` — direct token delivery (implicit-style): calls `get_my_profile()` then `change_user()`

Crucially, `OnNavigationRequest` / `OnNavigationComplete` in `WebUserLoginDialog.cpp` are **no-ops** (they only call
`UpdateState()` which sets the window title). The interception does NOT happen by watching WebView navigation events
— it happens because the browser genuinely navigates to / POSTs to the loopback HTTP server, which the C++ side
intercepts via the TCP listener.

There is also a second open-source code path (`pjarczak_browser_login_url`, used on the Linux bridge and on all
platforms when `PJarczakLinuxBridge::enabled()` is true) where the `redirect_url` **is** constructed explicitly in
C++: it builds `http://localhost:13618` from the `LOCALHOST_URL` and `LOCALHOST_PORT` constants and appends it to
the Bambu sign-in URL as the `redirect_url` query parameter. This confirms Bambu's hosted login page accepts a
`redirect_url` query param and will redirect the callback there.

**Citations (BBL path):**
- `src/slic3r/Utils/BBLCloudServiceAgent.cpp:156-165` — `build_login_cmd()` delegates entirely to
  `plugin.get_build_login_cmd()(agent)` (a `dlsym`-resolved function pointer into the closed binary)
- `src/slic3r/GUI/HttpServer.hpp:19-20` — `#define LOCALHOST_PORT 13618` / `#define LOCALHOST_URL "http://localhost:"`
- `src/slic3r/GUI/WebUserLoginDialog.cpp:358-399` — `OnScriptMessage` handles `get_login_cmd`: calls
  `build_login_cmd()`, extracts `pkce.loopback_port` or parses the port from `pkce.redirect_uri`, starts the
  loopback HTTP server, then posts the full `login_cmd` JSON back to the WebView via `window.postMessage`
- `src/slic3r/GUI/WebUserLoginDialog.cpp:64-76` — `pjarczak_browser_login_url()`: open-source construction of the
  Bambu sign-in URL with `redirect_url=http://localhost:13618` (the Linux/external-browser code path)
- `src/slic3r/GUI/WebUserLoginDialog.cpp:283-299` — `OnNavigationRequest` / `OnNavigationComplete`: both are
  effectively no-ops (call `UpdateState()`); they do **not** intercept the redirect URL
- `src/slic3r/GUI/HttpServer.cpp:282-416` — `bbl_auth_handle_request`: the three redirect shapes handled server-side
  (`?ticket=`, `?code=&state=`, `?access_token=`)

**Override possibility:** There is no env var or config key visible in the open-source wrapper that controls the
port/URI returned by the plugin. The Linux bridge path (`PJarczakBambuNetworkForwarderExports.cpp:402`) forwards the
call to the daemon via RPC (`net.build_login_cmd`) and the daemon ultimately talks to the closed `.so` — so the
value is still opaque. The only overridable value is in the external-browser code path where `LOCALHOST_PORT` (a
compile-time `#define`) sets the port — this is not runtime-configurable.

**Does the BBL finding agree with the previous Orca-based finding?**
Structurally yes: both use a loopback (`http://localhost:<port>`) redirect URI and neither supports arbitrary
non-localhost origins. However the mechanism differs: Orca builds the URI in open-source C++ (overridable), while
BBL generates the URI inside the closed binary (not overridable from outside). The conclusion is the same, but now
grounded in the correct files.

**Implication for bambu-gateway:**
- The primary OAuth callback path (registering our gateway's `/api/cloud/auth/callback`) is **not viable** without
  Bambu's explicit backend cooperation to whitelist a non-localhost redirect URI. The BBL plugin binary generates the
  loopback URI internally and there is no runtime hook to substitute a different value.
- Fallback A (loopback intercept): workable only when the gateway runs on the user's own machine. For a remote
  server deployment (e.g. `10.0.1.9`), `http://localhost:13618` resolves on the user's browser machine, not the
  server. Receiving the callback server-side is impossible with this topology.
- Fallback B (paste) remains functional for all deployment topologies and is the only safe baseline for remote
  deployments.

**Resulting plan for §6.2 of the spec:**
- **BLOCKER for primary OAuth callback path** — confirmed from the BBL path, not just the Orca path.
- For Phase 4 (auth), commit to **Fallback B (paste fallback)** as the safe baseline. The login UI should open
  the Bambu sign-in URL in the user's browser and instruct them to paste the post-redirect URL (which will contain
  `?code=` and `?state=` or `?ticket=`) back into the gateway. The gateway extracts the relevant parameters and
  completes the exchange via `get_my_token()` / `get_my_profile()` (mirroring `bbl_auth_handle_request`).
- An optional **Fallback A (loopback intercept)** mode may be added later for local-only deployments.

## Q11.2 — Two-3MF export from orcaslicer-headless

**Current orcaslicer-headless capability:** Returns **one 3MF only** — the gcode-3MF
(`SaveStrategy::WithGcode`). The `/slice/v2` and `/slice-stream/v2` endpoints each return a single
`output_token`; the JSON response schema has no `config_token`, `config_output_token`, or second-file
field. The OpenAPI spec (v2.3.2-53, probed live at `http://10.0.1.9:8070`) lists 28 paths and none of
them map to a config-3MF or slice-info export. There is no `export-config` or `config-3mf` endpoint.

**Citations:**
- `app/slicer_client.py:200-202` — after a successful `/slice/v2` POST, the gateway reads
  `payload["output_token"]` and calls `_download_3mf(output_token)`; there is no second token consumed
- `app/slicer_client.py:294-296` — the `/slice-stream/v2` `result` SSE event also carries only
  `output_token` + `download_url`; `_inflate_v2_result` fetches just that one file
- orcaslicer-headless `/slice/v2` — `SliceTokenRequest` input schema, untyped `{}` response schema in
  OpenAPI; the actual JSON response shape is `{output_token, settings_transfer, estimate}`

**Source flags from OrcaSlicer (Plater.cpp:15914–15962):**
- gcode-3MF (`send_gcode`, line 15930): `Silence | SkipModel | WithGcode | SkipAuxiliary`
- config-3MF (`export_config_3mf`, line 15958): `Silence | SkipModel | WithSliceInfo | SkipAuxiliary`

The two functions are separate in the GUI: `send_gcode` produces the file the printer executes;
`export_config_3mf` produces a metadata-only companion that Bambu's cloud UI uses for job-history
previews. The headless binary currently exposes only the `send_gcode` equivalent (`WithGcode`).

**Path forward for Phase 6 (print submission):**
- **v1: ship with gcode-3MF only.** Cloud print submission works with just the gcode-3MF — the
  config-3MF is consumed exclusively by the Bambu web dashboard's job-history preview feature, not by
  the printer or the cloud dispatch API. Omitting it means cloud print history entries will lack a
  config snapshot thumbnail, but the print itself succeeds. This is the right v1 baseline: zero changes
  to the gateway or the slicer service, unblocks Phase 6 entirely.
- **v2 (post-launch): extend orcaslicer-headless.** Add a `config_output_token` field to the
  `/slice/v2` response (and a parallel SSE key in `/slice-stream/v2`) so the slicer binary calls
  `export_config_3mf` in addition to `send_gcode` and returns both tokens. The gateway then uploads
  both to the Bambu cloud job endpoint. This is a clean extension with no in-process post-processing
  needed.

## Q11.3 — Plugin CDN endpoint

**Base URL:** `https://api.bambulab.com/` (China: `https://api.bambulab.cn/`)

**Endpoint(s):**

- `GET v1/iot-service/api/slicer/resource?slicer/plugins/cloud=<current_version>` — returns a JSON listing of available resource versions; the client sends the currently-installed version as a query param and the server replies with newer versions if any exist.
- `GET <url-from-listing>` — direct download of a ZIP archive containing the plugin `.so` files and `linux_payload_manifest.json`; the URL is opaque (provided by the listing response).

**Listing response shape:**
```json
{
  "message": "success",
  "resources": [
    {
      "type": "slicer/plugins/cloud",
      "version": "02.05.02.51",
      "url": "https://...",
      "force_update": false,
      "description": ""
    }
  ]
}
```
The `url` value is the direct-download ZIP URL. The client compares `version` against the currently-installed version (semver major/minor/patch-CC must match; patch-DD must be newer) before downloading.

**Citations:**
- `GUI_App.cpp:1141-1164` — `get_http_url()`: base URL selection by `country_code`; default path appended is `v1/iot-service/api/slicer/resource`
- `PresetUpdater.cpp:980-985` — resource key `"slicer/plugins/cloud"` passed to `sync_resources()`; changelog file is `"network_plugins.json"`
- `PresetUpdater.cpp:484-554` — `sync_resources()`: builds `?slicer/plugins/cloud=<version>` query, GETs listing, parses `resources[]` array (fields: `type`, `version`, `url`, `force_update`, `description`), then GETs `url` and extracts ZIP
- `PresetUpdater.cpp:948-985` — `sync_plugins()` bridge path: overrides `X-BBL-OS-Type` to `"linux"`, `X-BBL-Client-Name` to `"BambuStudio"`, and `X-BBL-Client-Version` before calling `sync_resources()`

**Manifest schema (`linux_payload_manifest.json`):**

The manifest is **constructed locally by the packaging script** (`tools/pjarczak_bambu_linux_host/package_linux_host_runtime.sh`) and bundled inside the downloaded ZIP. It is not a separate CDN download. Its schema:

```json
{
  "files": [
    {
      "name": "libbambu_networking.so",
      "sha256": "<hex-sha256-of-the-.so>",
      "abi_version": "02.05.02.51"
    },
    {
      "name": "libBambuSource.so",
      "sha256": "<hex-sha256-of-the-.so>"
    }
  ]
}
```

Key points:
- Top-level key is `files` (array); there is **no top-level `version` field** in the manifest itself.
- Only `libbambu_networking.so` carries `abi_version`; `libBambuSource.so` has only `name` and `sha256`.
- `sha256` is a lowercase hex string (SHA-256 of the raw `.so` bytes).
- The ZIP may also contain `liblive555.so`, `libagora_rtc_sdk.so`, `libagora-fdkaac.so` but these are not in the manifest.
- The local `network_plugins.json` (written by `sync_resources()` after extraction) holds `{"version": "...", "description": "...", "force": false}` — this is separate from the manifest and used only for version bookkeeping.

**Citation:**
- `package_linux_host_runtime.sh:96-109` — Python snippet that builds and writes `linux_payload_manifest.json`; confirms `files[]` array with `name`, `sha256`, optional `abi_version` (only for `libbambu_networking.so`)
- `PJarczakLinuxBridgeConfig.cpp:95-107` — `find_manifest_entry()`: parses `root["files"]` array, matches by `entry["name"]`
- `PJarczakLinuxBridgeConfig.cpp:400-446` — `validate_linux_payload_file_against_manifest()`: reads `sha256` and `abi_version` fields per entry

**Required request headers (confirming §5.1):**
- `User-Agent: BambuStudio/02.05.02.51`
- `X-BBL-Client-Type: slicer`
- `X-BBL-Client-Name: BambuStudio`
- `X-BBL-Client-Version: 02.05.02.51`
- `X-BBL-OS-Type: linux`

## Q11.4 — change_user() payload shape

**Canonical `change_user()` payload (from `HttpServer.cpp:38-65`, `build_canonical_login_payload`):**

```json
{
  "command": "user_login",
  "data": {
    "token": "<accessToken / access_token / token>",
    "access_token": "<same value as token>",
    "refresh_token": "<refreshToken / refresh_token>",
    "expires_in": "<expiresIn / expires_in>",
    "refresh_expires_in": "<refreshExpiresIn / refresh_expires_in>",
    "user_id": "<uidStr / uid / id from profile>",
    "uidStr": "<same value as user_id>",
    "user": {
      "id": "<uidStr / uid / id from profile>",
      "uid": "<same value>",
      "uidStr": "<same value>",
      "name": "<name from profile>",
      "account": "<account from profile>",
      "avatar": "<avatar from profile>"
    }
  }
}
```

The builder (`build_canonical_login_payload`) accepts two JSON objects — a token response and a profile response — and normalises field-name variants (camelCase vs snake_case) via `json_string_first()`. Both `token` and `access_token` are written with the same value for compatibility.

**Alternative legacy formats accepted (`ICloudServiceAgent.hpp:80-90`):**

1. Traditional login: `{ "username": "...", "password": "..." }`
2. WebView/OAuth canonical: `{ "command": "user_login", "data": { ... } }` ← what we use
3. Token-only nested: `{ "data": { "token": "...", "refresh_token": "...", "user": { ... } } }`

**What Bambu's hosted sign-in actually posts back (three redirect shapes):**

All three shapes are handled by `bbl_auth_handle_request` in `HttpServer.cpp:282-416`.

**Shape 1 — `?ticket=<val>` (loopback redirect, lines 290-348)**

The `ticket` is a short-lived opaque token. The handler:
1. Calls `agent->get_my_token(ticket, ...)` → receives a token JSON body with fields
   `accessToken` (or `access_token` / `token`), `refreshToken`, `expiresIn`, `refreshExpiresIn`.
2. Calls `agent->get_my_profile(access_token, ...)` → receives a profile JSON body with
   fields `uidStr` (or `uid` / `id`), `name`, `account`, `avatar`.
3. Passes both to `build_canonical_login_payload()` → calls `change_user(canonical_json)`.

URL fields: `ticket` only. The gateway must call the token and profile API endpoints itself;
it cannot build the canonical payload from the URL alone.

**Shape 2 — `?code=<val>&state=<val>` (OAuth PKCE, lines 350-378)**

The handler builds a **minimal** payload — it does NOT call get_my_token or get_my_profile.
Instead the NetworkAgent's `change_user` implementation is expected to handle the code exchange
internally:

```json
{
  "command": "user_login",
  "data": {
    "code": "<auth_code>",
    "state": "<state>"
  }
}
```

URL fields → payload mapping:
- `code` → `data.code`
- `state` → `data.state`

This shape delegates the token exchange to the NetworkAgent plugin (libbambu_networking.so).
The gateway cannot replicate this without the plugin's PKCE verifier stored in memory.

**Shape 3 — `?access_token=<val>&refresh_token=<val>&expires_in=<val>&refresh_expires_in=<val>&redirect_url=<val>` (token fragment, lines 381-413)**

The handler:
1. Extracts all token fields directly from the URL query params.
2. Calls `agent->get_my_profile(access_token, ...)` to fetch user profile.
3. Constructs a synthetic token JSON object and passes both to `build_canonical_login_payload()`.

URL fields → canonical payload mapping:
- `access_token` → `data.token` and `data.access_token`
- `refresh_token` → `data.refresh_token`
- `expires_in` → `data.expires_in`
- `refresh_expires_in` → `data.refresh_expires_in`
- profile API response `uidStr` / `uid` / `id` → `data.user_id`, `data.uidStr`, `data.user.id`, etc.
- profile API response `name` → `data.user.name`
- profile API response `account` → `data.user.account`
- profile API response `avatar` → `data.user.avatar`

After calling `change_user()` the handler redirects the browser to `<redirect_url>?result=success`.

**Mapping for our gateway (Phase 4 OAuth flow):**

Shape 3 is the most actionable for a gateway implementation because all token fields are
present in the redirect URL — the gateway only needs to call the profile API once and then
assemble the canonical payload. Shape 1 (ticket) requires an additional token-exchange API
call. Shape 2 (code+state) cannot be replicated without the in-memory PKCE verifier from the
NetworkAgent plugin.

**Recommended Phase 4 approach:**

Instruct the user to paste the post-redirect URL. If it contains `access_token`:
1. Extract `access_token`, `refresh_token`, `expires_in`, `refresh_expires_in` from URL.
2. Call `GET /v1/user-service/u/info` (or equivalent profile endpoint) with `Authorization: Bearer <access_token>`.
3. Build the canonical payload using the field mappings above.
4. Store token + profile fields; use `access_token` for subsequent Bambu Cloud API calls.

If the URL contains `ticket`: call `get_my_token(ticket)` first (BBL token-exchange endpoint),
then proceed as above.

**Citations:**
- `HttpServer.cpp:38-65` — `build_canonical_login_payload()`: normalises token + profile JSON into canonical shape
- `HttpServer.cpp:282-416` — `bbl_auth_handle_request()`: three redirect-shape branches
- `ICloudServiceAgent.hpp:80-87` — `change_user()` comment: three accepted format descriptions
- `WebUserLoginDialog.cpp:407-418` — JS bridge: `user_login` / `user_ticket_login` commands routed to `handle_script_message()`

## Verified CDN reachability

**With forged BambuStudio headers (listing endpoint):** `HTTP 200`

Sample response body (first ~30 lines):
```
HTTP/2 200
content-type: application/json; charset=utf-8
content-length: 288
x-bbl-be: go
server: cloudflare

{"message":"success","code":null,"error":null,"software":null,"guide":null,"resources":[{"type":"slicer/plugins/cloud","version":"02.05.02.58","description":"","url":"https://public-cdn.bblmw.com/upgrade/studio/plugins/02.05.02.58/9fb586c207/linux_02.05.02.58.zip","force_update":false}]}
```

**Without headers (control):** `HTTP 200` — also 200, but the response body is different:
the server returned the **Windows** binary (`win_02.05.02.58.zip`) together with a `software`
entry advertising `Bambu_Studio_win-v02.06.00.51.exe`. Without `X-BBL-OS-Type: linux`, Bambu
defaults to Windows. The listing endpoint does not gate on identity (no 4xx), but it does use
the headers to select the OS-appropriate payload. Without the headers the gateway would
silently download the wrong (Windows) ZIP.

**ZIP URL reachability (probed):** `HTTP 200`, `Content-Length: 20915124` (~20 MB),
`Content-Type: binary/octet-stream`, served via CloudFront with a 1-year cache TTL.
The CDN ZIP URL (`https://public-cdn.bblmw.com/upgrade/studio/plugins/02.05.02.58/9fb586c207/linux_02.05.02.58.zip`)
is publicly reachable without any auth headers.

**Note on version skew:** The probed version was `02.05.02.51` (currently shipped by
orcaslicer-headless), but the listing returned `02.05.02.58` as the latest available. This
is expected — the server always returns the current latest regardless of the client-supplied
version. The gateway downloader should use the `version` field from the listing response, not
the query-param version, when naming/storing the downloaded plugin.

**Conclusion:** Phase 1 downloader is unblocked. Headers are required to receive the Linux
ZIP rather than the Windows one; the ZIP CDN itself is open (no auth token needed).
