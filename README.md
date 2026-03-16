# Bambu Gateway

A self-hosted web application for managing Bambu Lab 3D printers over your local
network. Provides a REST API and web dashboard for monitoring printer status and
submitting print jobs.

Works with any Bambu Lab printer in **developer/LAN mode** (A1 Mini, X1C, P1S,
etc.).

An iOS client is also available: **[BambuGateway iOS](https://github.com/leolobato/bambu-gateway-ios)** — print 3MF files from MakerWorld directly from your phone.

## Features

- Real-time printer status (state, temperatures, print progress)
- AMS unit info (humidity, temperature) and external spool holder support
- Upload and print 3MF files from the browser
- Multi-printer support
- Add/remove/edit printers at runtime via settings page
- REST API for automation and integration
- No external dependencies — runs entirely on your local network

## Quick Start

### Prerequisites

- Python 3.12+
- A Bambu Lab printer with **LAN Mode** enabled (find the access code and IP in
  the printer's network settings)

### Setup

```bash
git clone https://github.com/your-user/bambu-gateway.git
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

Environment variables only seed the config on first run when no `printers.json`
exists.

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/printers` | List printers with live status |
| `GET` | `/api/printers/{id}` | Status for a single printer |
| `GET` | `/api/ams` | AMS units, trays, and external spool info |
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

Interactive API docs are available at `/docs` (Swagger UI).

## How It Works

The app communicates with Bambu Lab printers using their LAN protocol:

1. **MQTT over TLS** (port 8883) for real-time status updates and print commands
2. **FTPS** (port 990, implicit TLS) for uploading 3MF files

When you submit a print, the file is uploaded to the printer via FTPS, then an
MQTT command triggers the print. Status updates flow back continuously over MQTT.

## License

MIT
