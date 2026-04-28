# Web UI Camera Tab — Design

## Goal

Add a `Camera` tab to the React web UI that mirrors the iOS app's Camera tab,
limited to A1/P1-family printers (TCP-JPEG transport on port 6000). X1 family
(RTSPS) is out of scope for this pass and continues to render a "not available"
placeholder.

## Scope (this pass)

- Backend proxy that decodes the printer's TCP-JPEG stream and re-publishes it
  as MJPEG over HTTP to N concurrent browser viewers.
- New `Camera` tab in the web UI, placed between `Dashboard` and `Print`.
- The tab shows: printer picker (when >1 printer), chamber light toggle, and a
  16:9 feed tile with status dot, error overlay + retry, and tap-to-fullscreen.
- Reuses the existing `POST /api/printers/{id}/chamber-light` endpoint —
  no backend changes for chamber light.

## Non-goals

- RTSPS / X1 family camera support. Server-side ffmpeg transcode is deferred.
- Recording, snapshots, multi-printer grid view, time-lapse — not in this pass.
- Authenticated access controls beyond what the gateway already does.

## Architecture

```
A1/P1 printer ──TCP+TLS:6000──► CameraProxy (per printer)
                                   │
                                   ├─► subscriber queue ──► /api/printers/{id}/camera/stream.mjpg ──► <img>
                                   ├─► subscriber queue ──► /api/printers/{id}/camera/stream.mjpg ──► <img>
                                   └─► state                /api/printers/{id}/camera/status         ──► poll
```

One `CameraProxy` per printer with `tcp_jpeg` transport. The proxy holds **one**
upstream connection — the printer only accepts a single concurrent viewer, so
fan-out to multiple HTTP subscribers is required. The proxy lazy-starts on the
first subscriber and stops the upstream 5s after the last subscriber leaves
(grace window so a tab refresh doesn't tear down and re-handshake).

## Backend

### New module `app/camera_proxy.py` (asyncio, no threads)

`CameraProxy` per printer with:

- `state: "idle" | "connecting" | "streaming" | "failed"`
- `error: str | None`
- `last_frame_at: float | None` (monotonic)
- `_subscribers: set[asyncio.Queue[bytes]]`
- `_latest_frame: bytes | None` — cached so a new subscriber sees a frame
  immediately rather than waiting for the next decode.
- `_upstream_task: asyncio.Task | None`
- `_drain_task: asyncio.Task | None` — scheduled when subscribers go to zero;
  cancels upstream after a 5s grace. Cancelled if a new subscriber arrives
  in the grace window.

Public surface:

- `async def subscribe() -> AsyncIterator[bytes]` — registers a queue, ensures
  upstream is running, and yields JPEG frames. On cancellation/exit, removes
  itself and (if last) schedules drain.
- `def status() -> dict` — `{state, error, last_frame_at}`.
- `async def stop()` — for printer removal/reconfig.

Upstream loop (`_run_upstream`):

1. `state = "connecting"`. Open TLS connection to `(ip, 6000)` with cert
   verification disabled (Bambu uses self-signed certs, matches iOS behavior).
2. Send the 80-byte auth packet (ported byte-for-byte from
   `BambuTCPJPEGFeed.swift:90-107`):
   - `[0..3]   = 0x40 0x00 0x00 0x00` — magic
   - `[4..7]   = 0x00 0x30 0x00 0x00` — length marker (LE 0x3000)
   - `[8..15]  = 0`
   - `[16..19] = "bblp"` — username
   - `[20..47] = 0`
   - `[48..79] = access code (UTF-8, ≤32 bytes, zero-padded)`
3. Read frames in a loop: 16-byte header (first 4 bytes = LE JPEG length),
   then exactly `length` bytes of JPEG. On first JPEG: `state = "streaming"`.
4. For each JPEG: cache as `_latest_frame`, set `last_frame_at`, push to every
   subscriber queue. If a queue is full (configurable maxsize, default 2),
   drop the oldest pending frame so a slow consumer doesn't stall others.
5. On error / EOF: `state = "failed"`, `error = "<message>"`. Sleep 2s, retry
   while subscribers exist. When subscribers reach zero, exit.

### `PrinterService` integration

- New field `self._proxies: dict[str, CameraProxy] = {}`.
- New method `def get_camera_proxy(printer_id) -> CameraProxy | None`. Lazily
  creates a proxy when the printer's transport is `tcp_jpeg`. Returns `None`
  for unknown printers, missing config, or `rtsps` transport.
- `sync_printers()` extension: when a printer is removed or its IP/access code
  changes, `await proxy.stop()` and drop it from `self._proxies`.
- `stop()` extension: cancel and await all proxies.

### New routes (in `app/main.py`)

- `GET /api/printers/{id}/camera/stream.mjpg`
  - 404 if no proxy is available for that printer (unknown printer, no camera,
    or `rtsps` transport).
  - Returns `StreamingResponse(generator, media_type="multipart/x-mixed-replace; boundary=frame")`.
  - The generator yields, per frame, the literal byte sequence:
    `--frame\r\nContent-Type: image/jpeg\r\nContent-Length: N\r\n\r\n<jpeg>\r\n`.
  - On client disconnect (`asyncio.CancelledError` raised inside the generator),
    unsubscribes from the proxy. The proxy schedules drain when count hits 0.
- `GET /api/printers/{id}/camera/status`
  - 404 for unknown printers; otherwise returns
    `{state, error, last_frame_at}`. For printers with `rtsps` or no camera,
    returns `{state: "unsupported", error: null, last_frame_at: null}`.

### Chamber light — no backend changes

`POST /api/printers/{pid}/chamber-light` exists at `app/main.py:425`. The web
UI consumes it directly.

## Frontend

### App shell

`web/src/components/app-shell.tsx` — insert a `Camera` `TabLink` after
`Dashboard` and before `Print`. Order becomes: Dashboard · Camera · Print ·
Jobs · ⚙ Settings.

### New route `web/src/routes/camera.tsx`

Wired into `web/src/App.tsx` as `{ path: 'camera', element: <CameraRoute /> }`.

Renders, vertically stacked:

1. `PrinterPicker` from `web/src/components/printer-picker.tsx` (the existing
   shared picker — same one Dashboard uses). Auto-hidden when only one
   printer is configured.
2. `<ChamberLightToggle printer={selectedPrinter} />`
3. `<CameraFeed printer={selectedPrinter} />`

When no printer is selected, the page renders an empty-state card matching the
existing dashboard's empty state.

### `web/src/components/camera/chamber-light-toggle.tsx` (new)

Mirrors `ChamberLightToggle.swift`:

- Visible only when `printer.camera?.chamber_light?.supported` is true.
- Reads `printer.camera.chamber_light.on` for the current state.
- Pill button — accent fill when on, surface fill when off; lightbulb icon.
- `onClick`: `POST /api/printers/{id}/chamber-light` with `{on: !current}`,
  optimistic update, invalidate the printers query on response.
- Disabled while pending or while `printer.online` is false.

### `web/src/components/camera/camera-feed.tsx` (new)

- 16:9 aspect-ratio wrapper. Border + rounded corners matching existing tile
  styles in `dashboard/`.
- Header row above the frame: small status dot (green = streaming, amber =
  connecting/idle, red = failed, gray = unsupported) + "Printer" label.
- Frame area:
  - `<img src={`/api/printers/${id}/camera/stream.mjpg?t=${retryToken}`} />`
    with `class="w-full h-full object-contain bg-black"`.
  - Fullscreen icon overlay in the top-right corner; clicking the frame calls
    `wrapperRef.current?.requestFullscreen()`.
  - Error overlay rendered on top of `<img>` when status is `failed`:
    icon + message + **Retry** button. Retry bumps `retryToken` (state) to
    force the browser to drop the existing MJPEG connection and start fresh.
  - Connecting overlay (spinner + "Connecting…") when status is `connecting`
    or `idle`.
- State source: `useQuery(['camera-status', printerId], fetchStatus, { refetchInterval: 2000 })`.
  TanStack Query stops polling automatically when the component unmounts.
- When `printer.camera == null` or transport is `rtsps`, render a static
  placeholder card with text "Camera not available for this printer." — no
  network activity.

## Lifecycle & failure handling

- **Tab change / nav away** — `<img>` is unmounted, browser tears down the
  HTTP request, server-side generator gets `CancelledError`, unsubscribes.
  When subscribers hit 0, the proxy schedules a 5s drain. New mount within
  5s cancels the drain (no upstream re-handshake on quick refreshes).
- **Multiple browser tabs viewing the same printer** — share one upstream;
  each tab has its own subscriber queue.
- **Printer offline** — upstream connect fails → `state = "failed"`, error
  message surfaces in the overlay within ~2s of the next status poll.
- **Wrong access code** — printer drops the TCP connection during/after auth.
  No explicit auth-fail signal in the protocol; surfaces as
  `failed: "stream ended"`.
- **Printer reconfigured (IP / access code change)** — `sync_printers()`
  stops the existing proxy and drops it; next subscriber lazy-creates a new
  one with the new config.
- **Slow client** — its subscriber queue fills; oldest frame is dropped to
  keep the upstream loop and other subscribers moving.

## Testing

- Unit test for the auth-packet builder (compare bytes to fixture from
  `BambuTCPJPEGFeed.swift`).
- Unit test for the frame parser: feeds a synthetic `[16-byte header][JPEG]`
  byte stream split across multiple chunks and asserts JPEG payloads emerge
  intact.
- Integration test using a fake asyncio TCP server that performs the auth
  exchange and emits two synthetic JPEG frames; asserts the `/stream.mjpg`
  endpoint emits two `--frame` parts and that `/status` reports `streaming`
  with a non-null `last_frame_at`.
- Lifecycle test: subscribe twice, both unsubscribe, assert the drain task
  fires after 5s and stops the upstream connection.
- Frontend: no test framework configured in `web/`; manual verification via
  the running app is sufficient for this pass.

## Out of scope / future work

- RTSPS support (X1 family) via ffmpeg → HLS or WebRTC.
- Snapshot endpoint (`GET /camera/snapshot.jpg` returning a single cached
  frame) — could be added cheaply later by reusing `_latest_frame`.
- Multi-printer grid view on the Camera tab.
