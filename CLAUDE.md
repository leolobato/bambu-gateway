# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

```bash
pip install -r requirements.txt
python -m app
```

For development with auto-reload: `uvicorn app.main:app --reload`

Copy `.env.example` to `.env` and fill in printer details.

### Docker

```bash
docker compose up -d
```

Or build and push for Portainer: `docker build -t <registry>/bambu-gateway:latest .`

## Tests

Run the suite with `.venv/bin/pytest` (the project virtualenv lives at `.venv/`,
which has the FastAPI/httpx/jwt deps the system Python lacks). Config is in
`pytest.ini`; shared fixtures in `tests/conftest.py`. There is no linter
configured.

## Architecture

FastAPI app that manages Bambu Lab 3D printers over the local network. Printers
communicate via **MQTT over TLS** (port 8883, username `bblp`) for commands/status
and **FTPS** (port 990, implicit TLS) for file uploads.

**Request flow:** Browser/API → FastAPI (`main.py`) → `PrinterService` → `BambuMQTTClient` / `ftp_client`

**Key layers:**

- `app/main.py` — FastAPI routes, lifespan startup/shutdown, mounts static files
  and templates. The `printer_service` is a module-level global initialized in
  the lifespan context manager.
- `app/printer_service.py` — Owns all `BambuMQTTClient` instances. Provides
  status queries, print submission, and `sync_printers()` for hot-reloading
  config changes at runtime.
- `app/mqtt_client.py` — One instance per printer. Maintains a persistent MQTT
  connection, subscribes to `device/{serial}/report`, publishes to
  `device/{serial}/request`. Updates an in-memory `PrinterStatus` behind a
  threading lock.
- `app/config.py` — `PrinterConfig` dataclass and `Settings` (pydantic-settings)
  for env var parsing.
- `app/config_store.py` — Persistence layer for `printers.json`. Seeds from
  env vars on first run; file becomes source of truth afterward. Path is
  configurable via `python -m app -c /path/to/printers.json`.
- `app/ftp_client.py` — Uploads 3MF files to printer via implicit FTPS.
- `app/models.py` — All Pydantic models for printer state and API responses.
- `app/preparation_stages.py` — Preparation stage definitions (67+ stages) and
  state derivation logic. Determines granular printer state from `gcode_state`,
  `stg_cur`, and `layer_num` MQTT fields.
- `app/parse_3mf.py` — Extracts metadata (plates, filaments, profiles, thumbnails)
  from 3MF ZIP archives. The gateway does not mutate 3MFs before forwarding;
  `orcaslicer-cli` owns all input normalization (clamps, printer-identity
  rebrand, filament-slot trim).
- `app/filament_selection.py` — Validates and normalizes filament profile selections
  before passing them to the slicer API.
- `app/slicer_client.py` — HTTP client for the OrcaSlicer CLI API. Supports both
  the regular `/slice` endpoint and streaming `/slice-stream` (SSE) with automatic
  fallback when streaming is unavailable.

**Concurrency:** MQTT runs a background thread per printer (paho-mqtt's network
loop). `PrinterStatus` updates are guarded by a `threading.Lock`. Each MQTT client
has a 20-second idle disconnect timer on a daemon thread. FastAPI routes are async;
slicer calls use `httpx.AsyncClient`.

**UI:** Vanilla HTML/CSS/JS in `app/templates/` and `app/static/`. No build step.
Dashboard auto-refreshes via 4-second polling. Settings page manages printer CRUD.

## API Endpoints

### Printing

**`POST /api/print`** — Upload and print a 3MF file.
- `file` — 3MF file (not needed when using `preview_id`)
- `printer_id` — target printer serial (default: first printer)
- `plate_id` — plate to print (default: 0)
- `preview_id` — print a previously previewed file (no other params needed)
- `machine_profile`, `process_profile` — slicer profile IDs (required for unsliced files)
- `filament_profiles` — JSON object of slot overrides, e.g. `{"0": "GFL99"}`
- `slice_only` — return sliced file as download instead of printing

**`POST /api/print-stream`** — Same as `/api/print` but returns an SSE stream
with real-time slicing progress. Event types: `status`, `progress`, `result`,
`print_started`, `error`, `done`.
- Accepts all `/api/print` params plus:
- `preview` — store the sliced result instead of printing; the `result` event
  includes a `preview_id` for later use with `/api/print`

**`POST /api/print-preview`** — Non-streaming variant of preview. Slices the file,
stores the result, and returns the sliced 3MF as a download with `X-Preview-Id`
header.

### Preview workflow

1. Call `/api/print-stream` with `preview=true` (or `/api/print-preview`)
2. Receive `preview_id` in the `result` SSE event (or `X-Preview-Id` header)
3. Render the sliced 3MF for the user to review
4. Call `POST /api/print` with just `preview_id` to print — no re-slicing

Previews are stored in `/tmp/bambu-gateway-previews/` and cleaned up on restart.

### Printer control

- `POST /api/printers/{id}/pause` — pause current print
- `POST /api/printers/{id}/resume` — resume paused print
- `POST /api/printers/{id}/cancel` — cancel current print
- `POST /api/printers/{id}/speed` — set print speed (`{"level": 1-4}`:
  silent/standard/sport/ludicrous)
- `POST /api/printers/{id}/ams/{ams_id}/start-drying` — start AMS filament drying
  (`{"temperature": 55, "duration_minutes": 480}`)
- `POST /api/printers/{id}/ams/{ams_id}/stop-drying` — stop AMS drying

### Other endpoints

- `GET /api/printers` — list all printers with status (includes `stg_cur`,
  `stage_name`, `stage_category`, `speed_level`, `active_tray`)
- `GET /api/printers/{id}` — single printer detail
- `GET /api/ams` — AMS tray info with filament matching (units include
  `hw_version`, `ams_type`, `supports_drying`, `max_drying_temp`,
  `dry_time_remaining`)
- `POST /api/parse-3mf` — parse 3MF metadata without printing
- `POST /api/filament-matches` — match project filaments to AMS trays
- `GET /api/slicer/{machines,processes,filaments,plate-types}` — proxy slicer profiles
- `GET/POST/PUT/DELETE /api/settings/printers` — printer CRUD

## Configuration

Printer config is persisted to `printers.json` in the project root (gitignored).
The path can be overridden with `python -m app -c /data/printers.json` (useful
for Docker volumes). On first run with no JSON file, env vars
(`BAMBU_PRINTER_IP`, `BAMBU_PRINTER_ACCESS_CODE`, `BAMBU_PRINTER_SERIAL` —
comma-separated for multiple printers) seed the file. After that, the JSON file
is the source of truth and printers are managed via the `/settings` UI or
`/api/settings/printers` endpoints.

The `ORCASLICER_API_URL` env var points to a running OrcaSlicer CLI API instance
(e.g. `http://10.0.1.9:8070`). Required for slicing unsliced 3MF files.
