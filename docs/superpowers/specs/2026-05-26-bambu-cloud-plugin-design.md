# Bambu Cloud Plugin Support — Design

Status: draft (brainstorm output)
Date: 2026-05-26
Owner: Leonardo Lobato
Related: `OrcaSlicer-bambulab/CLOUD_ARCHITECTURE.md`

## 1. Background

bambu-gateway today talks to Bambu Lab printers exclusively over the LAN —
MQTT-TLS on port 8883 and FTPS on port 990. This requires every printer to
be unlocked in LAN / Developer Mode.

The OrcaSlicer-bambulab fork demonstrates a third path: authenticate against
Bambu Lab's cloud the same way Bambu Studio does, and dispatch print jobs
through the cloud relay — no developer mode and no Bambu Studio required.
It does this by embedding Bambu's actual closed-source Linux network plugin
(`libbambu_networking.so` + `libBambuSource.so`) and branding every HTTP
request as `BambuStudio/02.05.02.51`.

bambu-gateway already runs in Docker on Linux x86_64, which sidesteps the
WSL2/Lima bridge that OrcaSlicer-bambulab needs on Windows and macOS. We
can load the Linux plugin directly inside (or rather, alongside) the
gateway container.

## 2. Goals

Primary: **send print jobs to cloud-bound Bambu printers without requiring
LAN dev mode**, with status and basic controls (pause/resume/cancel/speed,
AMS drying) flowing through the same cloud relay.

v1 feature scope (in order of dependency):

1. Plugin acquisition: download the Linux plugin from Bambu's CDN with
   forged BambuStudio headers, validate, persist.
2. Subprocess host: a small C++ binary that `dlopen`s the plugin and
   exposes a focused JSON-RPC subset to Python.
3. Cloud login: OAuth-style redirect from the gateway Settings UI; tokens
   live inside the plugin's state directory.
4. Device list: `GET /api/cloud/devices` returns the printers bound to
   the signed-in account.
5. Cloud MQTT status: subscribe via the plugin; route status JSON into
   the existing `PrinterStatus` / `MachineObject` pipeline so the
   dashboard "just works" for cloud printers.
6. Cloud print submission: `POST /api/print` and `/api/print-stream`
   route to the plugin's `start_print`, with the same SSE progress
   contract the LAN path already provides.
7. Cloud control commands: pause/resume/cancel/speed-level/AMS-drying
   routed through the plugin's `send_message` instead of LAN MQTT.

## 3. Non-goals (v1)

- Mixed deployments. Cloud mode is **exclusive**: when
  `BAMBU_CLOUD_ENABLED=true`, all configured printers are treated as
  cloud-bound. LAN/dev-mode printers in the same gateway are not
  supported in v1.
- Camera URL retrieval (`get_camera_url`) and the camera-proxy
  integration. Deferred.
- Printer binding flow (`request_bind_ticket` / `bind_detect` / `bind`
  / `unbind`). v1 assumes printers are already bound from another
  client (Bambu Handy or Bambu Studio).
- MakerWorld / model-mall integration.
- Multi-account support. One Bambu account per gateway instance.
- Job history / cloud task list browsing.
- Runtime region switching. Region is set once via env var.

## 4. Architecture overview

Cloud mode is gated by env vars set in `docker-compose.yml`:

```
BAMBU_CLOUD_ENABLED=true
BAMBU_CLOUD_REGION=US             # US (default) or CN
BAMBU_CLOUD_PLUGIN_DIR=/data/bambu-plugin
```

When `BAMBU_CLOUD_ENABLED=false` (default), bambu-gateway behaves exactly
as today. When `true`, the LAN-mode code paths are bypassed and the new
`app/cloud/` subsystem is wired in.

New module layout:

```
app/cloud/
├── plugin_host.py         # spawns + supervises the subprocess host, RPC client
├── plugin_downloader.py   # CDN download with forged BambuStudio headers, SHA-256 validation
├── auth.py                # OAuth redirect, callback, change_user() wiring
├── cloud_client.py        # high-level: device list, status subscribe, start_print, send_message
├── event_pump.py          # polls bridge.poll_events on a background asyncio task
├── error_codes.py         # BAMBU_NETWORK_ERR_* → user-facing message
└── models.py              # Pydantic types for PrintParams, cloud responses
```

`PrinterService` gains a `CloudPrinterClient` that exposes the same
status/print interface as `BambuMQTTClient`. Routes in `main.py` are
unchanged; the service picks the right client based on
`BAMBU_CLOUD_ENABLED`.

The subprocess host is a **small C++ binary we build ourselves**
(~one source file) that `dlopen`s the two plugin `.so`s and proxies a
focused subset of ~12-15 RPC methods. PJarczak's
`tools/pjarczak_bambu_linux_host/` provides the trampoline pattern; we
do not need the full 128-method manifest.

RPC protocol: **stdin/stdout, newline-delimited JSON**, one message
per line. No binary framing in v1 — file uploads are passed to the
plugin via filesystem path, and inbound MQTT messages are already
JSON. Length-prefixed framing can be added later without changing
method signatures.

```
┌──────────────────────────────────────────────────┐
│  FastAPI (bambu-gateway) — Python                │
│  ┌─────────────────────────────────────────────┐ │
│  │ PrinterService → CloudPrinterClient         │ │
│  │   ├─ submit_print → plugin start_print      │ │
│  │   ├─ control cmds → plugin send_message     │ │
│  │   └─ status MQTT ← event_pump (OnMessage)   │ │
│  └─────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────┐ │
│  │ PluginHost (RPC client, JSONL over stdio)   │ │
│  └─────────────────────────────────────────────┘ │
└────────────────────┬─────────────────────────────┘
                     │ stdin / stdout
                     ▼
┌──────────────────────────────────────────────────┐
│  bambu-cloud-host (small C++ subprocess)         │
│  dlopen libbambu_networking.so + libBambuSource.so│
└──────────────────────────────────────────────────┘
```

## 5. Plugin acquisition and supervision

### 5.1 Download

On gateway startup, `plugin_downloader.check_and_fetch()` runs before
the subprocess host starts.

Request headers (all four mandatory, per
`OrcaSlicer-bambulab/CLOUD_ARCHITECTURE.md` §3):

| Header | Value |
|--------|-------|
| `User-Agent` | `BambuStudio/02.05.02.51` |
| `X-BBL-Client-Type` | `slicer` |
| `X-BBL-Client-Name` | `BambuStudio` |
| `X-BBL-Client-Version` | `02.05.02.51` |
| `X-BBL-OS-Type` | `linux` |

The version pin `BAMBU_NETWORK_AGENT_VERSION = "02.05.02.51"` lives in
one Python constant in `app/cloud/__init__.py`. Bumping the plugin
requires changing this constant and the User-Agent constant together.

Downloads land in `${BAMBU_CLOUD_PLUGIN_DIR}/active/`:

```
${BAMBU_CLOUD_PLUGIN_DIR}/
├── active/
│   ├── libbambu_networking.so
│   ├── libBambuSource.so
│   └── linux_payload_manifest.json
└── state/                # plugin's own state (tokens, etc.)
```

Validation steps:

1. ELF magic check: `0x7F 'E' 'L' 'F'`, 64-bit, little-endian, machine
   type `x86_64`.
2. SHA-256 of each `.so` matches the manifest's `files[].sha256`.
3. Manifest entry for `libbambu_networking.so` has
   `abi_version` matching `BAMBU_NETWORK_AGENT_VERSION` (patch-level
   wiggle room — first 8 chars `02.05.02` must match).

On validation failure, FastAPI startup fails with a structured log
line and the process exits non-zero. Docker's restart policy handles
back-off.

Re-fetch policy: on startup, if `active/` exists and its manifest
matches the pinned version, skip the download. To force a refresh,
the user deletes `active/`. (A `POST /api/cloud/plugin/refetch` admin
endpoint is not in v1.)

### 5.2 Subprocess host lifecycle

- Started by `PluginHost.__aenter__()` during FastAPI lifespan startup,
  after the download check passes.
- Env vars passed to the host: `PJARCZAK_BAMBU_PLUGIN_DIR`,
  `PJARCZAK_BAMBU_NETWORK_SO`, `PJARCZAK_BAMBU_SOURCE_SO` (same names
  as PJarczak's host, so the loader code is identical).
- Host's stderr is captured into Python logging at the
  `bambu.cloud.host` logger.
- A supervisor `asyncio.Task` watches the process. If it exits
  unexpectedly, the gateway logs and attempts one restart; on second
  crash within 60s, cloud session stays down and all printer statuses
  are reported as `unreachable`.
- On FastAPI shutdown, host gets a graceful `logout` RPC, then
  SIGTERM, then SIGKILL after 5s.

### 5.3 Plugin state persistence

The plugin manages its own token storage in
`${BAMBU_CLOUD_PLUGIN_DIR}/state/`. We mount the parent dir as a
Docker volume so login survives container restarts. No token state
lives in Python — `change_user()` is only called on initial login;
after restart the plugin reloads its own state and fires
`OnUserLoginFn` automatically.

### 5.4 Event pump

A dedicated asyncio task polls `bridge.poll_events` every **80 ms when
active, 250 ms when idle** (matches PJarczak's cadence). Events
dispatch to handler coroutines for:

- `OnMessage(dev_id, json)` → cloud MQTT status updates
- `OnUpdateStatus(stage, code, msg)` → print job progress
- `OnUserLogin(info)` → auth completion / failure
- `OnServerConnected()` → cloud relay ready

Handlers update `PrinterService` state behind its existing
`threading.Lock`.

## 6. Authentication / login flow

### 6.1 UI

Settings page gains a "Bambu Account" panel, visible only when
`BAMBU_CLOUD_ENABLED=true`. States:

- Not signed in → "Sign in to Bambu" button.
- Signed in → user email, avatar, "Sign out" button.

### 6.2 Flow

1. User clicks "Sign in to Bambu" → browser navigates to
   `GET /api/cloud/auth/start`.
2. Backend calls plugin `build_login_cmd()` to get the Bambu sign-in
   URL, API key, and PKCE params. Stashes the PKCE verifier in a
   server-side session keyed by a short-lived state token. Responds
   with HTTP 302 to Bambu's sign-in page.
3. User authenticates on Bambu's page. Bambu redirects to
   `<gateway>/api/cloud/auth/callback?code=...&state=...`.
4. Callback validates `state`, builds the canonical `user_login`
   payload (mirrors `build_canonical_login_payload` from
   `HttpServer.cpp:38-65`), calls plugin `change_user(payload)`.
5. Wait up to 10s for `OnUserLoginFn` on the pump. On success,
   drive `connect_server()` → `start_subscribe("app")` →
   `start_subscribe("printer")` → `add_subscribe(device_ids)`.
6. Callback page renders "Signed in" and auto-redirects to Settings.

### 6.3 Sign out

`POST /api/cloud/auth/logout` → plugin `user_logout(true)` (the `true`
flag tells the plugin to notify the backend, not just clear local
state). Clears persisted auth state.

### 6.4 Region

Read from `BAMBU_CLOUD_REGION` env var on startup, passed to plugin
via `set_country_code()` before the sign-in URL is built. Not
user-changeable at runtime in v1. Supported values: `US` (→
`api.bambulab.com`) and `CN` (→ `api.bambulab.cn`).

## 7. Cloud print job flow

### 7.1 Request shape

`POST /api/print` and `POST /api/print-stream` keep their existing
request shape. The `CloudPrinterClient.submit_print()` path replaces
the LAN `BambuMQTTClient` path.

### 7.2 Slice step

Same as today — call orcaslicer-headless `/slice`. Cloud submissions
need **two artifacts** per
`OrcaSlicer-bambulab/CLOUD_ARCHITECTURE.md` §7.3:

- **gcode-3MF** — the file the printer executes. Save flags:
  `Silence | SkipModel | WithGcode | SkipAuxiliary`.
- **config-3MF** — config-only; cloud uses it for job-history
  previews. Save flags:
  `Silence | SkipModel | WithSliceInfo | SkipAuxiliary`.

The existing `app/slicer_client.py` returns one 3MF today. See open
question §11.2 for how this gets resolved. If config-3MF cannot be
produced in v1, we ship without it; cloud's job-history UI loses
per-plate previews, but the print itself works (config-3MF is a UX
nicety, not a hard requirement).

Pre-sliced inputs (`preview_id`) need the same two-artifact treatment;
the preview cache stores both 3MFs.

### 7.3 AMS mapping

Today the gateway computes filament→tray mapping for the LAN MQTT
command. For cloud, that mapping serializes into `PrintParams`
fields `ams_mapping`, `ams_mapping2`, `ams_mapping_info`,
`nozzles_info`, and `task_use_ams` (JSON strings, **not** embedded in
the 3MF). Existing `filament_selection.py` logic stays; only the
output format changes.

### 7.4 `PrintParams` construction

Mirrors `OrcaSlicer-bambulab/src/slic3r/Utils/bambu_networking.hpp`
fields 217-260. Populated fields:

- Identity: `dev_id`, `task_name = {project_name}_plate_{plate_idx}`,
  `project_name`, `preset_name`
- Files: `filename` (gcode-3MF path), `config_filename` (config-3MF
  path), `plate_index`
- AMS: `ams_mapping`, `ams_mapping2`, `ams_mapping_info`,
  `nozzles_info`, `task_use_ams`
- Routing: `connection_type = ""` (cloud, not `"lan"`)
- Print options: defaults
  (`task_bed_leveling=true`, `task_flow_cali=true`, etc.). Not
  user-tunable in v1.
- LAN-only fields (`dev_ip`, `password`, `ftp_folder`, …) left empty.

### 7.5 Plugin call

`PluginHost.call("start_print", PrintParams)` over RPC. The host
serializes `PrintParams`, hands the 3MF file *paths* to the plugin;
the plugin reads them from disk and handles OSS upload + `/task` POST
itself. The host process and the gateway share the
`BAMBU_CLOUD_PLUGIN_DIR` volume, so paths are mutually visible.

### 7.6 Progress streaming

`OnUpdateStatusFn` events arrive via the event pump and feed a
`Queue` keyed by `dev_id`. `submit_print()` awaits the queue and
translates each event into an SSE event matching the existing
contract:

| Plugin stage | SSE event | UI % |
|---|---|---|
| `PrintingStageCreate` (0) | `progress` | 20 |
| `PrintingStageUpload` (1) | `progress` | 30 → 70 (sub-progress in `code`) |
| `PrintingStageWaiting` (2) | `progress` | 70 |
| `PrintingStageSending` (3) | `progress` | 75 |
| `PrintingStageRecord` (4) | `progress` | 97 |
| `PrintingStageWaitPrinter` (5) | `progress` | 97 |
| `PrintingStageFinished` (6) | `print_started` + `done` | 100 |
| `PrintingStageERROR` (7) | `error` (mapped code) | — |

### 7.7 Error code mapping

`app/cloud/error_codes.py` maps `BAMBU_NETWORK_ERR_*` constants from
`bambu_networking.hpp` to user-facing strings:

| Code | Message |
|---|---|
| `-2040` | "File too large for cloud upload" |
| `-2110` | "Bambu cloud OSS upload failed" |
| `-2120` | "Bambu cloud rejected the print job" |
| `-2060` | "Timed out waiting for printer acknowledgement" |
| `-16` | "Uploaded file checksum mismatch" |

### 7.8 Retry

`POST /api/printers/{id}/retry-print` maps to plugin
`retry_last_print_request(dev_id)` — reuses the cached 3MF, no
re-export. Mirrors OrcaSlicer's manual retry button. Auto-retry on
transient `0500-409D` printer errors mirrors OrcaSlicer's behaviour
(`DeviceManager.cpp:180-188`): one automatic retry only.

## 8. Status MQTT and control commands

### 8.1 Status MQTT

Bambu's print/info/system JSON envelope is **identical** between LAN
MQTT and the cloud relay. The existing `PrinterStatus` updater in
`app/models.py` and `MachineObject.parse_json` logic parses both
formats unchanged.

`OnMessageFn(dev_id, json)` events from the event pump feed straight
into the same updater the LAN path uses. The dashboard polling
endpoints (`GET /api/printers`, `GET /api/ams`, etc.) need no
changes.

### 8.2 Control commands

Pause/resume/cancel/speed-level and AMS drying routes today call
`BambuMQTTClient.publish_*()`. These get extracted into a
transport-neutral helper that returns the command JSON.
`CloudPrinterClient.send_command()` dispatches the same JSON via
plugin `send_message(dev_id, json, qos, flag)` on the cloud relay.

Routes affected (no API contract changes):

- `POST /api/printers/{id}/pause`
- `POST /api/printers/{id}/resume`
- `POST /api/printers/{id}/cancel`
- `POST /api/printers/{id}/speed`
- `POST /api/printers/{id}/ams/{ams_id}/start-drying`
- `POST /api/printers/{id}/ams/{ams_id}/stop-drying`

## 9. Configuration and deployment

### 9.1 Env vars

| Var | Default | Purpose |
|---|---|---|
| `BAMBU_CLOUD_ENABLED` | `false` | Master switch. When `false`, no cloud code paths run. |
| `BAMBU_CLOUD_REGION` | `US` | `US` or `CN`. Selects Bambu API host. |
| `BAMBU_CLOUD_PLUGIN_DIR` | `/data/bambu-plugin` | Where plugin `.so`s + state live. Must be a persistent volume. |

### 9.2 Printers config

When `BAMBU_CLOUD_ENABLED=true`:

- The Settings UI hides the access-code and IP fields; printer
  entries only carry `serial` and `name`.
- `printers.json` schema gains an optional `cloud: true` marker (set
  automatically when cloud mode is on). LAN fields ignored if
  present.

### 9.3 Docker compose

`docker-compose.yml` README section adds a "Cloud mode" example:

```yaml
services:
  bambu-gateway:
    image: bambu-gateway:latest
    environment:
      BAMBU_CLOUD_ENABLED: "true"
      BAMBU_CLOUD_REGION: "US"
    volumes:
      - bambu-cloud-data:/data/bambu-plugin
volumes:
  bambu-cloud-data:
```

LAN-mode users see no change.

## 10. Error handling and testing

### 10.1 Error handling (cross-cutting)

- **Plugin not yet downloaded / corrupt** → startup fails loud,
  process exits non-zero, Docker restart-loop surfaces it. No silent
  fallback to LAN.
- **Subprocess crash** → one automatic restart within 60s; second
  crash leaves cloud session down, statuses reported as
  `unreachable`. `/api/health` reflects this.
- **Login expired / refresh failure** → `OnUserLoginFn` with failure
  code clears the "signed in" UI state; banner prompts re-auth. In-flight
  print jobs surface a clear `error` SSE event.
- **Mid-print cloud failure** → `start_print` runs in the plugin's
  own retry loop; on terminal failure, `OnUpdateStatus` carries
  `PrintingStageERROR` with a code from §7.7.

### 10.2 Testing strategy

- **Unit tests** (no plugin needed): RPC framing round-trips,
  `PrintParams` builder, AMS mapping serializer, error-code mapper,
  event-pump dispatch logic. Subprocess stubbed out.
- **Plugin contract tests** with a recorded fake host: a Python
  script that mimics the host's RPC interface, replays canned
  `OnMessage` / `OnUpdateStatus` event sequences. Lets us test the
  print-progress SSE pipeline end-to-end without touching Bambu's
  cloud.
- **Integration test (manual, runbook)**: one real signed-in
  account, one bound printer, one canned 3MF; asserts `start_print`
  reaches `PrintingStageFinished`. Documented in
  `tests/integration/cloud_print.md`. Not in CI — it costs filament.

## 11. Open questions

These are discovery items for the implementation-planning phase, not
blockers for the design.

### 11.1 OAuth redirect URI

Does Bambu's OAuth accept arbitrary `redirect_uri`s, or is the URI
hardcoded inside the plugin / on Bambu's backend? OrcaSlicer's
WebView intercepts the redirect rather than running an HTTP listener,
so the existing fork doesn't have to answer this question.

- **Primary path:** register the gateway's
  `/api/cloud/auth/callback` as the redirect URI.
- **Fallback A:** loopback intercept on the user's *browser-side*
  machine via `http://localhost:<port>/callback`. Awkward because
  the gateway likely isn't on the user's localhost.
- **Fallback B (paste):** user copies the post-redirect URL from
  their browser bar and pastes it into the gateway. Functional but
  ugly.

To answer: inspect `WebUserLoginDialog.cpp` for the redirect-URI
constant, and probe Bambu's OAuth endpoint with curl during the
implementation discovery sprint.

### 11.2 Two-3MF export from orcaslicer-headless

Can the existing orcaslicer-headless service return both the
gcode-3MF and the config-3MF in one slice call?

- **Preferred:** extend orcaslicer-headless to return both.
- **Fallback:** ship v1 without config-3MF (Bambu's cloud accepts a
  single gcode-3MF; the cloud's job-history UI loses per-plate
  previews, but the print succeeds).

### 11.3 Plugin CDN endpoint

The architecture doc shows the headers but not the exact URL path
the plugin-download flow hits. To answer: traffic capture against a
fresh OrcaSlicer-bambulab install, or read `PresetUpdater.cpp:948-979`
+ the bridge config loader.

### 11.4 `change_user()` payload shape

Does plugin `change_user()` accept the JSON Bambu's hosted sign-in
returns directly, or does the WebView do JS-side massaging that
needs replicating server-side? To answer: read
`build_canonical_login_payload` in `HttpServer.cpp:38-65` and the JS
shim in `WebUserLoginDialog.cpp`.

## 12. Source map

OrcaSlicer-bambulab files referenced by this design (for the
implementation phase to grep into):

| Concern | File |
|---|---|
| Identity headers / UA | `src/slic3r/Utils/Http.cpp:193-200`, `src/slic3r/GUI/GUI_App.cpp:2387-2427`, `src/slic3r/Utils/PresetUpdater.cpp:948-979` |
| Plugin loader | `src/slic3r/Utils/BBLNetworkPlugin.{hpp,cpp}` |
| Bridge config / integrity | `src/slic3r/Utils/PJarczakLinuxBridge/PJarczakLinuxBridgeConfig.{hpp,cpp}` |
| Bridge RPC | `shared/pjarczak_linux_plugin_bridge_core/BridgeCoreFrame.{hpp,cpp}`, `BridgeCoreMethodManifest.{hpp,cpp}` |
| Linux subprocess host (reference) | `tools/pjarczak_bambu_linux_host/{main.cpp,LinuxPluginHost.{hpp,cpp}}` |
| Login UI / canonical payload | `src/slic3r/GUI/WebUserLoginDialog.cpp`, `src/slic3r/GUI/HttpServer.cpp:38-65` |
| Region routing | `src/slic3r/GUI/GUI_App.cpp:1141-1189` |
| Print-send orchestration | `src/slic3r/GUI/Jobs/PrintJob.cpp:524-620`, `src/slic3r/GUI/Plater.cpp:15914-15962` |
| Device messaging | `src/slic3r/GUI/DeviceManager.cpp` (`parse_json`, `command_*`) |
| PrintParams struct | `src/slic3r/Utils/bambu_networking.hpp:217-260` |
| Print stages / error codes | `src/slic3r/Utils/bambu_networking.hpp:136-146` and surrounding `BAMBU_NETWORK_ERR_*` constants |
