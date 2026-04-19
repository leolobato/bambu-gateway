# iOS Live Activities + Push Notifications

**Date:** 2026-04-20
**Branch:** `live-activities-push` (both `bambu-gateway` and `bambu-gateway-ios`)

## Goal

Show a Live Activity on the iPhone Lock Screen and Dynamic Island while a
print is running, and deliver a push notification when a print fails, pauses,
completes, is cancelled, goes offline mid-print, or raises an HMS warning.

All of the above must degrade gracefully when the gateway operator does not
have an Apple Developer account (no APNs credentials): Live Activities still
work locally while the app is foregrounded, but remote updates and push
notifications are disabled.

## Out of scope

- Interactive notification actions (Resume / Cancel buttons).
- Android or other platforms.
- Exposing the gateway over the public internet.
- Authentication on gateway endpoints (consistent with current LAN-only model).
- Per-event-type notification preferences in iOS UI.
- A historical notifications log.

## System architecture

```
┌─────────────────┐       ┌─────────────────────────────┐       ┌───────────┐
│ iOS app         │       │ bambu-gateway (LAN)         │       │ APNs      │
│                 │       │                             │       │           │
│ • Main target   │       │  PrinterService             │       │           │
│ • ActivityKit   │──(1)──▶  ┌──────────────────┐       │       │           │
│ • Widget ext.   │       │  │ MQTT clients     │──(2)──▶       │           │
│ • PushKit regs  │       │  │  state changes   │       │       │           │
└─────────────────┘       │  └──────────────────┘       │       │           │
        ▲                 │           │                 │       │           │
        │                 │           ▼                 │       │           │
        │                 │  ┌──────────────────┐       │       │           │
        │                 │  │ NotificationHub  │──(3)──┼──────▶│  APNs     │
        │                 │  │  + dedupe/debounce       │       │  HTTP/2   │
        │                 │  └──────────────────┘       │       │           │
        │                 │           │                 │       │           │
        │                 │  ┌──────────────────┐       │       │           │
        │                 │  │ DeviceStore      │       │       │           │
        │                 │  │  devices.json    │       │       │           │
        │                 │  └──────────────────┘       │       │           │
        │                 │           ▲                 │       │           │
        │                 │  POST /api/devices/register │       │           │
        └─────────────────┼──(0)──────┘                 │       │           │
                          └─────────────────────────────┘       └───────────┘
                                                                      │
                          ┌──────────────────────────────────────────(4)
                          ▼
                   Live Activity update / push notification on device
```

Flow:

- **(0)** iOS app registers three tokens per device on launch: APNs device
  token (alert notifications), Live Activity push-to-start token, and — when
  a Live Activity is active — its per-activity update token.
- **(1)** User kicks off a print from the iOS app; app locally starts a Live
  Activity via `Activity.request()` and registers its update token.
- **(2)** Gateway MQTT clients detect state transitions and emit events to a
  new `NotificationHub`.
- **(3)** `NotificationHub` consults `DeviceStore`, classifies each event,
  and sends APNs HTTP/2 calls: content-state updates for Live Activities,
  alert notifications for fail / pause / offline / HMS, push-to-start for
  prints initiated outside iOS.
- **(4)** Apple delivers to the phone.

## Graceful degradation

APNs is optional. If the gateway has no `.p8` key configured, the push
subsystem is disabled and the app still works in a reduced mode.

| Capability | APNs configured | APNs not configured |
|---|---|---|
| Live Activity starts when printing from iOS app | yes | yes (local `Activity.request()`) |
| Live Activity updates while app foreground | yes (push or local) | yes (local, driven by existing 4s polling) |
| Live Activity updates when app backgrounded / killed | yes | no |
| Live Activity auto-starts for prints kicked off from OrcaSlicer / web UI | yes (push-to-start) | no |
| Push notification on fail / pause / offline / HMS when app closed | yes | no |
| Local notification on fail / pause while app open | yes (optional) | yes (`UNUserNotificationCenter` on polled transition) |

Gateway exposes `GET /api/capabilities` → `{"push": true|false, "live_activities": true|false}`. iOS calls this on launch:

- If `push: false`, skip `/api/devices/register`, hide the push toggle in
  Settings, show an info line linking to a README explaining how to enable
  it.
- If `push: true`, register tokens normally.

Gateway-side: if any of the APNs env vars is missing, push is disabled at
startup with one info log line. No errors.

## Event model

### Observable inputs

Every `BambuMQTTClient` already updates a `PrinterStatus` behind a lock.
Add a state-change callback hook: whenever the MQTT client applies a
report, record `(prev_snapshot, new_snapshot)` and emit an event to
`NotificationHub` if anything notification-worthy changed. No new polling
loop.

Also add a small parser for the `hms` array in the MQTT `print` payload
(Bambu's Health Monitoring System error codes) — the gateway does not
currently surface these.

### Event taxonomy

Detected in `NotificationHub` by diffing snapshots:

| Event | Detection rule | Action |
|---|---|---|
| `print_started` | `state: * → printing` AND previous was not `paused` | Push-to-start Live Activity (if no local activity yet); no alert |
| `print_paused` | `state: printing → paused` | Update Live Activity content-state; alert notification |
| `print_resumed` | `state: paused → printing` | Update Live Activity content-state; no alert |
| `print_finished` | `state: * → finished` | End Live Activity (dismissal policy: 4 hours); alert notification |
| `print_cancelled` | `state: * → cancelled` | End Live Activity (immediate); alert notification |
| `print_failed` | `state: * → error` | End Live Activity (immediate); alert notification with HMS detail if available |
| `printer_offline_active` | `online: true → false` AND prior state in {`printing`, `paused`, `preparing`} | Update Live Activity to "offline" content-state; alert notification |
| `hms_warning` | New HMS code appears in `hms[]` that was not in prior snapshot | Alert notification (title from severity, body = code + description) |
| `progress_tick` | `progress` delta ≥ 1% OR `layer` changed OR `remaining_minutes` delta ≥ 5 min | Update Live Activity content-state; no alert |

### De-duplication

- **Per-event dedupe key** = `(printer_id, event_type, state_hash)`; same
  event firing twice in < 10s is dropped.
- **HMS codes** are tracked as a set per printer; an alert fires only for
  codes newly added since last snapshot; codes that clear are silently
  removed from the set.
- **State oscillation**: if `error` flickers to another state and back
  within 30s, treat as a single event.
- **Progress ticks** throttled to max 1 APNs push per 10s per Live Activity.

### Live Activity lifecycle

- Starts via `Activity.request()` from iOS (when user prints from app) OR
  via gateway push-to-start (any other origin).
- Ends when gateway observes terminal state (`finished` / `cancelled` /
  `error`). Gateway calls APNs with `"event": "end"`.
- Dismissal policy: `finished` → stays on lock screen for 4 hours;
  `cancelled` / `error` → dismisses immediately (user was notified via
  alert).
- iOS side also calls `Activity.end()` defensively if it observes terminal
  state via polling before the push arrives.

## Data model and APIs

### Gateway — `devices.json`

New file alongside `printers.json`. Managed via API only (no env seeding).

```json
{
  "devices": [
    {
      "id": "ios-CFA...D3B2",
      "name": "Leo's iPhone",
      "device_token": "a8b3f...",
      "live_activity_start_token": "f20...",
      "subscribed_printers": ["*"],
      "registered_at": "2026-04-20T15:30:00Z",
      "last_seen_at": "2026-04-20T18:42:11Z"
    }
  ],
  "active_activities": [
    {
      "device_id": "ios-CFA...D3B2",
      "printer_id": "01P00A...",
      "activity_update_token": "9c8a...",
      "started_at": "2026-04-20T17:00:00Z"
    }
  ]
}
```

Active activities are ephemeral (one per `{device, printer}` active print);
devices persist across prints. On terminal state the gateway removes the
corresponding `active_activities` row after sending the end push.

### Gateway — new endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/capabilities` | `{"push": bool, "live_activities": bool}` |
| `POST` | `/api/devices/register` | Upsert device (id, name, device_token, live_activity_start_token?, subscribed_printers?) |
| `DELETE` | `/api/devices/{device_id}` | Unregister (on logout / token invalidation) |
| `POST` | `/api/devices/{device_id}/activities` | Register activity update token (printer_id, activity_update_token) |
| `DELETE` | `/api/devices/{device_id}/activities/{printer_id}` | iOS-initiated early end |

APNs returns invalid-token responses (410 Unregistered, etc.); `APNsClient`
strips those tokens from `DeviceStore` automatically.

### Gateway — config additions (`.env`)

```
APNS_KEY_PATH=/data/AuthKey_ABC123.p8    # optional; missing = push disabled
APNS_KEY_ID=ABC123
APNS_TEAM_ID=XYZ789
APNS_BUNDLE_ID=org.lobato.BambuGateway
APNS_ENVIRONMENT=production              # or "sandbox" for debug builds
```

All five are required together. Missing any → push disabled, one info log,
`/api/capabilities` reports `push: false`.

### Shared ActivityAttributes (iOS)

Compiled into both the app target and the Widget Extension target.

```swift
struct PrintActivityAttributes: ActivityAttributes {
    // Static — set once at Activity creation
    let printerId: String
    let printerName: String
    let fileName: String
    let thumbnailData: Data?   // nil if unavailable

    struct ContentState: Codable, Hashable {
        var state: PrinterStateBadge
        var stageName: String?
        var progress: Double           // 0.0–1.0
        var remainingMinutes: Int
        var currentLayer: Int
        var totalLayers: Int
        var updatedAt: Date
    }
}
```

Gateway sends `ContentState` as `content-state` in every APNs Live Activity
push; decoded automatically on the iOS side.

### APNs payload shapes

Alert (`apns-push-type: alert`):

```json
{
  "aps": {
    "alert": {"title": "Print paused", "body": "X1C paused during layer 42"},
    "sound": "default",
    "interruption-level": "time-sensitive"
  },
  "printer_id": "01P00A...",
  "event_type": "print_paused"
}
```

Live Activity update (`apns-push-type: liveactivity`):

```json
{
  "aps": {
    "timestamp": 1713628800,
    "event": "update",
    "content-state": { /* ContentState */ },
    "stale-date": 1713632400
  }
}
```

Push-to-start (to start token):

```json
{
  "aps": {
    "timestamp": 1713628800,
    "event": "start",
    "attributes-type": "PrintActivityAttributes",
    "attributes": { "printerId": "...", "printerName": "...", "fileName": "...", "thumbnailData": "..." },
    "content-state": { /* initial ContentState */ }
  }
}
```

## Live Activity content

- **Always**: printer name, progress % (bar + number), remaining time,
  current state badge (e.g., "Printing layer 42/320" / "Paused" / "Bed
  leveling").
- **Nice to have**: 3MF thumbnail, file name.
- **Skip**: nozzle / bed temps, AMS tray info, filament color swatches.

Notifications use the system default appearance. No interactive actions.

## Component breakdown

### bambu-gateway

New files:

- `app/apns_client.py` — HTTP/2 client using `httpx`; JWT generation per
  Apple spec; handles sandbox vs production endpoint. Emits `ApnsResult`
  with outcome + token-invalid flag.
- `app/device_store.py` — JSON-backed persistence for `devices.json`.
  Thread-safe upsert / delete / token-invalidation operations. Seeds an
  empty file on first run.
- `app/notification_hub.py` — State-diff engine. Registers as a callback
  on each `BambuMQTTClient`, runs on a dedicated daemon thread fed by a
  `queue.Queue` so MQTT callbacks stay fast. Owns dedupe / throttle
  bookkeeping.
- `tests/` — pytest scaffolding (the repo has no tests today).

Modified files:

- `app/config.py` — add APNs env vars, `push_enabled` derived property.
- `app/mqtt_client.py` — add `on_state_change` callback hook; add HMS
  array parsing.
- `app/printer_service.py` — wire `NotificationHub` to every MQTT client
  during init / `sync_printers()`.
- `app/main.py` — lifespan wires `APNsClient`, `DeviceStore`,
  `NotificationHub`; new endpoints added.

### bambu-gateway-ios

New target: `LiveActivityExtension` (Widget Extension).

New files:

- Shared: `BambuGateway/Models/PrintActivityAttributes.swift` — compiled
  into both app and extension targets.
- App: `BambuGateway/Services/PushService.swift` — owns APNs device token
  + Live Activity push-to-start token; registers with gateway.
- App: `BambuGateway/Services/LiveActivityService.swift` — owns
  `Activity<PrintActivityAttributes>` lifecycle; starts locally on print
  submit, ends on terminal state detected via polling, registers update
  tokens with gateway.
- App: `BambuGateway/Services/NotificationService.swift` — wraps
  `UNUserNotificationCenter`; fires local notifications on polled
  transitions when push is unavailable.
- Extension: `LiveActivityExtension/PrintLiveActivity.swift` — widget
  view for Lock Screen + Dynamic Island presentations.

Modified files:

- `project.yml` — add `LiveActivityExtension` target, entitlements
  (aps-environment, background-modes: remote-notification), target
  dependency from app.
- `BambuGateway/App/BambuGatewayApp.swift` — register push on launch,
  wire `PushService` / `LiveActivityService` / `NotificationService`.
- `BambuGateway/App/AppViewModel.swift` — call new services on print
  submit and on polled status transitions.
- `BambuGateway/Networking/GatewayClient.swift` — add
  `/api/capabilities`, `/api/devices/register`, activity token endpoints.
- `BambuGateway/Views/SettingsView.swift` — push toggle; hidden when
  capability `push: false`.

## Testing strategy

**Gateway (Python):**

- Unit: `NotificationHub` event detection against synthetic
  `(prev, new)` snapshots.
- Unit: `APNsClient` JWT generation and payload shapes (no live APNs).
- Unit: `DeviceStore` round-trips.
- Integration: end-to-end with `httpx.MockTransport` as fake APNs server.
- Manual: APNs sandbox smoke test with a dev build of the iOS app.

Introduce `pytest` scaffolding; scope test coverage to new modules only.

**iOS:**

- Manual verification on a real device (ActivityKit is not meaningfully
  unit-testable).
- `PushService` token registration tested by mocking `GatewayClient`.
- Manual matrix: print origin (iOS app vs web UI) × app state (foreground
  / backgrounded / killed) × event (start / pause / fail / complete /
  offline / HMS) × APNs configured vs not.

## Rollout

1. Land gateway changes with push disabled by default. Existing clients
   unaffected.
2. Land iOS changes. With APNs not configured, everything degrades per the
   graceful-degradation table.
3. User configures APNs key → restarts gateway → iOS app re-registers on
   next launch → full experience enabled.

Each phase keeps the app fully working end-to-end.

## Open risks

- **APNs key rotation.** New `.p8` requires only dropping the file in
  place + restart; active Live Activity update tokens remain valid.
- **Live Activity 8-hour cap.** Platform limit; long prints fall off the
  Lock Screen toward the end. Terminal-event push notifications still
  fire. Not mitigated.
- **Push-to-start token invalidation.** Watched via
  `ActivityAuthorizationInfo().pushToStartTokenUpdates`; re-registered
  with gateway on change.
- **APNs payload size.** Thumbnails (~30–80KB) exceed per-update budget;
  included only in static `attributes` at Activity creation, never in
  per-update `content-state`.
- **Token leakage.** `devices.json` holds push tokens; a LAN peer could
  read them and spoof a device. Consistent with existing LAN-trust model.
- **Concurrency.** MQTT state-change callbacks fire on the network
  thread; `NotificationHub` marshals work to a dedicated daemon thread
  via `queue.Queue` so MQTT processing stays non-blocking and APNs calls
  are serialized per-gateway.
