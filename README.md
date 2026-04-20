# Bambu Gateway

A self-hosted web application for managing Bambu Lab 3D printers over your local
network. Provides a REST API and web dashboard for monitoring printer status and
submitting print jobs.

Works with any Bambu Lab printer in **developer/LAN mode**

An iOS client is also available: **[BambuGateway iOS](https://github.com/leolobato/bambu-gateway-ios)** — print 3MF files from MakerWorld directly from your phone.

## Features

- Real-time printer status (state, temperatures, print progress, preparation stages)
- Pause, resume, cancel, and adjust speed of active prints
- AMS unit info (humidity, temperature) and external spool holder support
- AMS filament drying control (AMS 2 Pro and AMS HT)
- Upload, slice and print 3MF files from the browser with upload progress
- Automatic AMS tray matching for project filaments
- Supports custom filament profiles through [orcaslicer-cli](https://github.com/leolobato/orcaslicer-cli)
- Multi-printer support
- Optional iOS Live Activities and push notifications via APNs
- REST API for automation and integration
- Runs entirely on your local network

## Quick Start

### Prerequisites

- Python 3.12+
- A Bambu Lab printer with **Developer Mode** enabled (find the access code and IP in
  the printer's network settings)

### Setup

```bash
git clone https://github.com/leolobato/bambu-gateway.git
cd bambu-gateway
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your printer details:

```env
BAMBU_PRINTER_IP=192.168.1.100
BAMBU_PRINTER_ACCESS_CODE=12345678
BAMBU_PRINTER_SERIAL=01P00A000000000
```

For multiple printers, use comma-separated values:

```env
BAMBU_PRINTER_IP=192.168.1.100,192.168.1.101
BAMBU_PRINTER_ACCESS_CODE=12345678,87654321
BAMBU_PRINTER_SERIAL=01P00A000000000,01P00A000000001
```

### Run

```bash
python -m app
```

Open [http://localhost:4844](http://localhost:4844) in your browser.

### Docker

```bash
docker compose up --build
```

The Docker image reads `.env` for printer configuration and persists data to a `./data` volume.

A pre-built image is also available from GitHub Container Registry:

```bash
docker pull ghcr.io/leolobato/bambu-gateway:latest
```

### iOS push notifications (optional)

The gateway can send push notifications and Live Activity updates to the
companion iOS app when a print changes state — paused, failed, completed,
cancelled, offline, or flagged by the printer's health-monitoring system.
When enabled, the iPhone shows a Live Activity on the Lock Screen and
Dynamic Island with progress, remaining time, and current layer, updated
in real time even when the app is closed.

Enabling this requires a **paid Apple Developer account** so you can mint
an APNs Auth Key. The four required env vars are:

```env
APNS_KEY_PATH=/path/to/AuthKey_KEYID.p8
APNS_KEY_ID=KEYID
APNS_TEAM_ID=TEAMID
APNS_BUNDLE_ID=org.yourname.BambuGateway
APNS_ENVIRONMENT=sandbox   # or "production" for TestFlight / App Store builds
```

Leaving any of them empty disables push entirely. The iOS app degrades
gracefully in that case — Live Activities still run locally while the app is
foregrounded, but remote updates and notifications aren't delivered.

See **[docs/APNS.md](docs/APNS.md)** for the full walkthrough: creating the
`.p8` key, choosing sandbox vs. production, installing the key for Docker
deployments, rotating keys, and troubleshooting.

## Configuration

### Printer config

Printers are stored in `printers.json` (see `printers.example.json` for the
format). The file is created automatically on first run — either seeded from
environment variables or empty if none are set. After that, manage printers
through the **Settings** page at `/settings`.

To use a custom path (e.g. a Docker volume):

```bash
python -m app -c /data/printers.json
```

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BAMBU_PRINTER_IP` | | Printer IP(s), comma-separated — seeds initial config |
| `BAMBU_PRINTER_ACCESS_CODE` | | Printer access code(s), comma-separated |
| `BAMBU_PRINTER_SERIAL` | | Printer serial number(s), comma-separated |
| `SERVER_HOST` | `0.0.0.0` | Server bind address |
| `SERVER_PORT` | `4844` | Server bind port |
| `LOG_LEVEL` | `INFO` | Logging level |
| `MAX_FILE_SIZE_MB` | `200` | Maximum upload file size in MB |
| `ORCASLICER_API_URL` | | OrcaSlicer CLI API URL (e.g. `http://10.0.1.9:8070`) — required for slicing |
| `APNS_KEY_PATH` | | Path to APNs Auth Key `.p8` — see [docs/APNS.md](docs/APNS.md). All four APNS_* vars must be set to enable push |
| `APNS_KEY_ID` | | 10-character Key ID from the Apple Developer portal |
| `APNS_TEAM_ID` | | 10-character Team ID from the Apple Developer portal |
| `APNS_BUNDLE_ID` | | Bundle ID of the iOS app receiving push |
| `APNS_ENVIRONMENT` | `production` | `sandbox` for dev builds, `production` for TestFlight / App Store |

Environment variables only seed the config on first run when no `printers.json`
exists.

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/printers` | List printers with live status |
| `GET` | `/api/printers/{id}` | Status for a single printer |
| `POST` | `/api/printers/{id}/pause` | Pause current print |
| `POST` | `/api/printers/{id}/resume` | Resume paused print |
| `POST` | `/api/printers/{id}/cancel` | Cancel current print |
| `POST` | `/api/printers/{id}/speed` | Set print speed (1–4: silent/standard/sport/ludicrous) |
| `GET` | `/api/ams` | AMS units, trays, and external spool info |
| `POST` | `/api/printers/{id}/ams/{ams_id}/start-drying` | Start AMS filament drying |
| `POST` | `/api/printers/{id}/ams/{ams_id}/stop-drying` | Stop AMS filament drying |
| `POST` | `/api/filament-matches` | Match project filaments to AMS trays |
| `POST` | `/api/print` | Upload 3MF to default printer |
| `POST` | `/api/print-stream` | Upload and print with SSE progress |
| `POST` | `/api/print-preview` | Slice and preview without printing |
| `POST` | `/api/parse-3mf` | Parse 3MF metadata without printing |
| `GET` | `/api/slicer/machines` | List slicer machine profiles |
| `GET` | `/api/slicer/processes` | List slicer process profiles |
| `GET` | `/api/slicer/filaments` | List slicer filament profiles |
| `GET` | `/api/slicer/plate-types` | List available plate types |
| `GET` | `/api/settings/printers` | List configured printers |
| `POST` | `/api/settings/printers` | Add a printer |
| `PUT` | `/api/settings/printers/{serial}` | Update a printer |
| `DELETE` | `/api/settings/printers/{serial}` | Remove a printer |
| `GET` | `/api/uploads/{id}` | Poll FTP upload progress |

Interactive API docs are available at `/docs` (Swagger UI).

## OrcaSlicer CLI Integration

Bambu Gateway integrates with [orcaslicer-cli](https://github.com/leolobato/orcaslicer-cli),
a headless slicing server built on OrcaSlicer's engine. This enables:

- **Custom filament profiles** — use your own filament settings (temperature,
  flow, pressure advance, etc.) instead of being limited to built-in profiles
- **Headless slicing** — slice 3MF files on the server without needing OrcaSlicer's
  GUI, ideal for running on a Raspberry Pi or NAS
- **Preview before printing** — slice a file and review the result before sending
  it to the printer

### Setup

Run the orcaslicer-cli server (see the [orcaslicer-cli docs](https://github.com/leolobato/orcaslicer-cli)
for full setup instructions), then point Bambu Gateway to it:

```env
ORCASLICER_API_URL=http://10.0.1.9:8070
```

When a slicer URL is configured, the web UI will show machine and filament profile
selectors, and unsliced 3MF files will be sliced automatically before printing.
Without it, only pre-sliced 3MF files can be printed.

## How It Works

The app communicates with Bambu Lab printers using their LAN protocol:

1. **MQTT over TLS** (port 8883) for real-time status updates and print commands
2. **FTPS** (port 990, implicit TLS) for uploading 3MF files

When you submit a print, the file is uploaded to the printer via FTPS, then an
MQTT command triggers the print. Status updates flow back continuously over MQTT.

## License

Bambu Gateway is available under the MIT License. See [LICENSE](LICENSE) for details.
