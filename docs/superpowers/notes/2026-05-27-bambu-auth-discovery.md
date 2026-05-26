# Bambu Cloud Auth — Discovery

Generated: 2026-05-27
Sources: OrcaSlicer-bambulab C++ source read during Phase A of the Bambu Cloud OAuth
paste-fallback plan (`2026-05-27-bambu-cloud-auth.md`).

---

## A.1 — Sign-in URL

### Base URL

The base URL is resolved at runtime from the plugin via `get_cloud_service_host()`, which
ultimately calls `bambu_network_get_bambulab_host()` on the closed-source BBL network plugin.
The fallback (used when the plugin returns an empty string) is hardcoded in
`PJarczakBambuNetworkForwarderExports.cpp:435`:

```
https://bambulab.com
```

`GUI_App::get_http_url()` (GUI_App.cpp:1141-1165) maps region codes to **API** base URLs:

| Region code | API base URL |
|---|---|
| `US` (default) | `https://api.bambulab.com/` |
| `CN` | `https://api.bambulab.cn/` |
| `ENV_CN_DEV` | `https://api-dev.bambu-lab.com/` |
| `ENV_CN_QA` | `https://api-qa.bambu-lab.com/` |
| `ENV_CN_PRE` | `https://api-pre.bambu-lab.com/` |

The **sign-in** URL base is the main `https://bambulab.com` (not `api.bambulab.com`); the
CN region uses a CN-hosted version (the plugin returns a CN-specific host for that region).

### External-browser URL builder

`pjarczak_browser_login_url()` in `WebUserLoginDialog.cpp:64-76` (verbatim):

```cpp
std::string pjarczak_browser_login_url(const std::string& host_value, const std::string& locale, const std::string& localhost_base)
{
    std::string host = host_value;
    while (!host.empty() && host.back() == '/')
        host.pop_back();
    if (host.rfind("http://", 0) != 0 && host.rfind("https://", 0) != 0)
        host = "https://bambulab.com";
    std::string lang = locale.empty() ? "en" : locale;
    std::string callback = host + "/sign-in/callback?source=portal&locale=" + Http::url_encode(lang) +
                           "&redirect_url=" + Http::url_encode(localhost_base) +
                           "&openBy=suite&from=studio&slicerLoginType=ticket";
    return host + "/sign-in?&from=studio&source=portal&to=" + Http::url_encode(callback);
}
```

### URL construction summary

The builder produces a two-level URL:

**Outer sign-in URL:**
```
https://bambulab.com/sign-in?&from=studio&source=portal&to=<encoded-callback>
```

**Inner callback URL (the value of `to=`):**
```
https://bambulab.com/sign-in/callback
  ?source=portal
  &locale=<lang>          (e.g. "en" or "zh-CN")
  &redirect_url=<encoded-loopback>
  &openBy=suite
  &from=studio
  &slicerLoginType=ticket
```

Where `<loopback>` is `http://localhost:13618` (the default from `HttpServer.hpp:19`; the port
can change when the plugin's PKCE bundle specifies a different `loopback_port`).

### Query-param inventory

| Param (outer URL) | Value | Source |
|---|---|---|
| `from` | `studio` | hardcoded |
| `source` | `portal` | hardcoded |
| `to` | URL-encoded callback (below) | built at runtime |

| Param (callback, inside `to=`) | Value | Source |
|---|---|---|
| `source` | `portal` | hardcoded |
| `locale` | locale string (e.g. `en`, `zh-CN`) | app language setting |
| `redirect_url` | `http://localhost:13618` | `LOCALHOST_URL + LOCALHOST_PORT` |
| `openBy` | `suite` | hardcoded |
| `from` | `studio` | hardcoded |
| `slicerLoginType` | `ticket` | hardcoded |

### No `response_type` param in the external-browser builder

There is **no `response_type` query parameter** in `pjarczak_browser_login_url()`. The only
OAuth-style references in the codebase are in unrelated third-party integrations (Obico uses
`response_type=token`, SimplyPrint has it too — neither is the Bambu flow).

**Implication:** The `slicerLoginType=ticket` param in the callback URL signals Bambu's own
server to redirect back with a `?ticket=<val>` parameter, not an OAuth code or token. The
three redirect shapes Bambu emits (all handled by `HttpServer::bbl_auth_handle_request()`) are:

1. `?ticket=<val>` — Bambu's internal short-lived ticket (exchanged for tokens via
   `get_my_token()`)
2. `?code=<val>&state=<val>` — standard auth-code (requires in-plugin PKCE verifier — **not
   usable from Python**)
3. `?access_token=<val>&refresh_token=<val>&expires_in=<n>&refresh_expires_in=<n>&redirect_url=<url>`
   — implicit/token delivery (all tokens in URL — **usable from Python**)

### Forcing the access_token form

The `slicerLoginType=ticket` param causes Bambu to prefer the ticket flow (Shape 1). There is
no `response_type=token` param in the builder, and no evidence that adding one is how the
token form is triggered. The ticket flow requires calling `get_my_token(ticket)` against the
Bambu token-exchange endpoint — this can be done from Python since it is a simple HTTP POST
(the closed-source plugin merely proxies it). **Shape 3 (access_token) may be obtainable by
omitting `slicerLoginType=ticket` from the callback URL or by using a different Bambu auth
entry point** — this is unconfirmed from source code alone; probing the live endpoint would
be needed to determine which URL shape Bambu returns without `slicerLoginType=ticket`.

**Practical recommendation:** support all three shapes in the paste-URL parser (as
`bbl_auth_handle_request` already does); the ticket flow is the most reliable since it is
what Bambu's own slicer uses.

### Country-code / region effect

- The `host` parameter passed to `pjarczak_browser_login_url` comes from `get_cloud_service_host()`,
  which queries the plugin.
- The plugin defaults to `https://bambulab.com` for non-CN regions and returns a CN-specific
  host for the `CN` country code.
- Region selection therefore affects only the **base host** of both the outer and callback
  URLs — the path structure and params are identical.

---

## A.2 — User-profile endpoint

### Discovery method

`BBLCloudServiceAgent::get_my_profile()` (`BBLCloudServiceAgent.cpp:631-639`) is a thin
wrapper that calls `bambu_network_get_my_profile()` from the closed-source BBL plugin `.so`.
The actual HTTP call happens inside the binary; the source only exposes the interface:

```cpp
int BBLCloudServiceAgent::get_my_profile(std::string token, unsigned int* http_code, std::string* http_body)
{
    auto& plugin = BBLNetworkPlugin::instance();
    auto agent = plugin.get_agent();
    auto func = plugin.get_get_my_profile();
    if (func && agent) {
        return func(agent, token, http_code, http_body);
    }
    return -1;
}
```

The function signature is `(token: string) -> (http_code: uint, http_body: string)`,
strongly suggesting a standard `GET` request with Bearer auth.

### Known endpoint (from prior Phase 0 research)

Based on prior discovery (`2026-05-26-bambu-cloud-discovery.md:311`) the profile endpoint is:

```
GET https://api.bambulab.com/v1/user-service/u/info
```

China region:
```
GET https://api.bambulab.cn/v1/user-service/u/info
```

### Headers

| Header | Value |
|---|---|
| `Authorization` | `Bearer <access_token>` |
| `User-Agent` | `BambuStudio/02.05.02.58` (plugin version string) |
| `X-BBL-OS-Type` | OS-specific (e.g. `mac`, `windows`, `linux`) |
| `X-BBL-Client-Name` | `BambuStudio` |
| `X-BBL-Client-Version` | plugin version string |

The `X-BBL-*` identity headers are set on preset/OTA-download calls
(`PresetUpdater.cpp:955-958`); they may or may not be required for the profile endpoint —
the profile endpoint only strictly needs `Authorization: Bearer`.

### Response shape

`build_canonical_login_payload()` (`HttpServer.cpp:38-65`) reads from the raw profile JSON
using `json_string_first()` with fallback key lists, revealing the expected response fields:

```json
{
  "uidStr": "1234567890",
  "uid": "1234567890",
  "id": "1234567890",
  "name": "DisplayName",
  "account": "user@example.com",
  "avatar": "https://...cdn.../avatar.jpg"
}
```

Field-name priority (first non-null wins):
- uid: `uidStr` → `uid` → `id`
- display name: `name`
- login account: `account`
- avatar URL: `avatar`

**Citations:**
- `HttpServer.cpp:44-47` — `json_string_first` key lists for each profile field
- `HttpServer.cpp:38-65` — `build_canonical_login_payload()` full function
- `HttpServer.cpp:390` — call site for `get_my_profile()` in the access_token redirect branch
- `BBLCloudServiceAgent.cpp:631-639` — `get_my_profile` wrapper (delegates to plugin)
- `GUI_App.cpp:1141-1165` — `get_http_url()` region → API base URL mapping

### Token-exchange endpoint (for ticket flow)

When the redirect URL carries `?ticket=<val>` instead of `access_token=`, an extra
token-exchange step is needed before calling the profile endpoint:

```
POST https://api.bambulab.com/<ticket-exchange-path>
```

The exchange is performed by `BBLCloudServiceAgent::get_my_token()` (proxied to
`bambu_network_get_my_token` in the plugin). Response keys searched:
`accessToken` → `access_token` → `token` (HttpServer.cpp:108, 304).

The exact HTTP path of the token-exchange endpoint is inside the binary plugin and was not
discoverable from source alone. The recommended gateway approach is to delegate ticket
exchange to the plugin via the subprocess host rather than replicating it in Python.
