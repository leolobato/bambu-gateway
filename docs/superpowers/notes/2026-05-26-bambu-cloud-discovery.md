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

(unanswered — see Task 0.3)

## Q11.3 — Plugin CDN endpoint

(unanswered — see Task 0.4)

## Q11.4 — change_user() payload shape

(unanswered — see Task 0.5)

## Verified CDN reachability

(unanswered — see Task 0.6)
