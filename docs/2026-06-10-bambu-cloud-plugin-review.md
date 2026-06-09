# Code Review — Bambu Cloud Plugin Integration

> **Status (2026-06-10, branch `fixes`):** all 15 correctness findings and
> recommendations R1 (contract test + shared fixture), R3 (shared submit
> helper), R4 (timeouts/bounds), R5 (no blocking I/O on the print paths) are
> addressed in commits `d618a6a..5d03aff`, plus the small cleanups (region
> map, identity headers, hash loop, auth.py imports, print_error log rule).
> R2 was applied in reduced form: dispatch still branches at the route/manager
> level, but through one `_get_cloud_client`/`get_cloud_client` lookup and one
> `run_cloud_print` helper rather than per-endpoint copies. Not done: a full
> common transport protocol (R2), EventPump push-instead-of-poll, and the
> real-binary end-to-end smoke gate (the runbook remains manual).

**Scope:** `c15335f..HEAD` (~50 commits, phases 1–7 of the cloud plugin plan)
**Date:** 2026-06-10
**Method:** 7 independent finder passes (3 correctness angles, reuse/simplification/efficiency/altitude) followed by per-finding adversarial verification against the actual source. Every finding below was independently confirmed with file:line evidence.

---

## Overall assessment

**The architecture makes sense.** Isolating the proprietary `libbambu_networking` plugin in a C++ subprocess speaking JSONL RPC is the right call: it keeps a closed-source, crash-prone native library out of the gateway process, the RPC surface is small and auditable, and the phase-by-phase plan (host → auth → status → print → controls) with discovery notes is well executed. The shared `apply_print_payload` between MQTT and cloud clients is the right instinct.

**But the integration is not production-ready.** Two findings are fatal — the plugin's cloud MQTT session is never connected/subscribed, and the auth flow calls RPC methods the real C++ host doesn't implement — meaning **cloud status and cloud login cannot work outside the test suite today**. The root cause of both is the same: the test fake host (`tests/cloud_fake_host.py`) implements a superset of the real host and the lifespan wiring was only ever exercised against the fake. Several more findings are hangs with no timeout anywhere on the new RPC/event path.

The good news: nothing here invalidates the design. The fixes are wiring, parity, and timeout work — not rearchitecture.

---

## Critical — feature does not work in production

### 1. The plugin's cloud MQTT relay is never connected and no printer is ever subscribed
`app/main.py:278` (lifespan)

The lifespan calls `init_plugin`, but **no production code ever calls `connect_server`, `start_subscribe`, or `add_subscribe`**. Those RPC methods exist in the C++ host (`tools/bambu_cloud_host/methods.cpp:223-225`) and in the fake host, but their only callers are tests. `PluginLoader::bootstrap()` does not connect implicitly (`plugin_loader.cpp:131-227` — create_agent/config/log/cert/start only), and the discovery note (`docs/superpowers/notes/2026-05-27-bambu-cloud-status-discovery.md`) documents `connect_server` as an explicit post-login call mirroring OrcaSlicer. The status plan built these RPCs in Phase D but the lifespan wiring task never invoked them — the gap was carried from the plan into the code.

**Effect:** `bridge.poll_events` polls forever and returns nothing; every cloud printer stays offline; `send_message`/`start_print` are issued against an unconnected relay.

**Fix:** after login (and on startup when a session exists), call `connect_server`, then `start_subscribe` + `add_subscribe` for the configured serials; re-subscribe on printer CRUD.

### 2. `is_user_login` and `user_logout` don't exist in the real C++ host
`app/cloud/auth.py:238,252,257` vs `tools/bambu_cloud_host/methods.cpp:216-229`

`auth.py` calls `is_user_login` (final verification step of `complete_login`, and `/api/cloud/auth/status`) and `user_logout` (`/logout`). The real host's dispatch table registers neither — it throws `unknown method`, which surfaces as `PluginHostError`. Only `tests/cloud_fake_host.py:36-39` implements them, so the suite passes while production paste-login **fails at its last step even when login actually succeeded**, status always 503s, and logout always fails.

**Fix:** implement both methods in `methods.cpp` (the plugin exports the corresponding agent functions per the auth discovery notes). See also the systemic recommendation below about fake/real host contract testing.

### 3. Cloud mode creates LAN and cloud clients for the same printers
`app/printer_service.py:75-80,169-188`, `app/main.py:284-291,360-366`

In cloud mode the lifespan builds a `CloudPrinterClient` per config **and** `PrinterService.__init__` still creates a LAN `BambuMQTTClient` per config — the docstring at `printer_service.py:87-89` ("LAN MQTT clients are not created") is false. `get_all_statuses` returns each printer twice with the same id, and `get_status` checks `_clients` first, so the **stale, never-connected LAN status shadows the cloud status** while commands route to cloud.

**Fix:** skip LAN client creation when cloud mode is on (or make it a per-printer transport choice), and make `get_status`/`get_all_statuses` resolve exactly one client per serial.

### 4. Cloud printers report `online: false` forever — the UI hides everything
`app/cloud/cloud_printer.py:69-74`, `app/models.py:135`

`handle_event` only calls `apply_print_payload`, which never touches `status.online`; the only code that sets `online = True` is the LAN client's `on_connect` (`mqtt_client.py:663`). The web UI gates on it: `hero-card.tsx:26` renders the offline hero, `control-buttons.tsx:23` returns null, `speed-select.tsx:55` disables. A cloud printer mid-print renders as offline with no controls.

**Fix:** set `online = True` when cloud events arrive for the device (and consider an offline timeout when events stop).

### 5. Cloud `ams_mapping` is sent as a dict-of-strings; the plugin expects a JSON array
`app/main.py:1855,2202`, `app/cloud/print_params.py:38`

The cloud path converts the positional list into `{"0": "1", ...}` and `json.dumps`s that. OrcaSlicer's own cloud path builds `mapping_v0_json = json::array()` of ints (`SelectMachine.cpp:1168` → `PrintJob.cpp:261`), matching the LAN path's plain int list (`mqtt_client.py:625`). `tests/test_cloud_print_params.py:43` asserts the dict shape — it encodes the bug.

**Effect:** multi-filament cloud prints get a malformed mapping → wrong/ignored tray assignment.

**Fix:** pass through the int array (`json.dumps([1, 0])`), update the test.

---

## High — broken entry points and hangs

### 6. The preview/job reprint path never routes to cloud
`app/main.py:1593-1660`

The `job_id`/`preview_id` fast path of `POST /api/print` resolves only the LAN client (`get_client` → `ensure_connected` → FTPS), returning before any cloud logic; only the fresh-upload path checks `_get_cloud_client` (line 1840). The documented preview→print workflow 409s ("Printer offline") or silently attempts LAN FTPS for cloud printers.

### 7. `POST /api/slice-jobs` auto-print is LAN-only, and SSE cloud auto-print is lossy
`app/main.py:2117-2118`, `app/slice_jobs.py:841,869`

`job_auto_print = auto_print and cloud_pair is None` bolts cloud auto-print onto the SSE generator only. Consequences: (a) the async `POST /api/slice-jobs` path calls the LAN-only `printer_service.submit_print` and fails for cloud printers; (b) on the SSE path the cloud submission lives inside the response generator, so a client disconnect cancels it and **the print is silently lost**; (c) `job.printed` is never set for cloud prints, so persistence, restart recovery, and the "printing" push notification all miss them.

### 8. `submit_print` can hang forever and wedge the printer in "in flight"
`app/cloud/cloud_printer.py:121-134`, `tools/bambu_cloud_host/methods.cpp:186-191`

Three compounding problems:
- `method_start_print` only special-cases rc `-98` and returns `{"rc": 0}` for everything else — `PluginLoader::start_print`'s synchronous `-1` (agent not bootstrapped, `plugin_loader.cpp:369`) is reported as success.
- `await self._progress.get()` has no timeout, and host death only fails RPC futures (`plugin_host.py:152-159`) — nothing ever pushes into the progress queue, so the HTTP request hangs indefinitely.
- `_progress` then stays non-None, so every later submission raises "a print job is already in flight" until gateway restart.

**Fix:** propagate the real rc from the host; add a timeout (and host-death signal) to the progress wait; clear `_progress` on failure.

### 9. A single >64 KiB RPC response permanently wedges all cloud RPC
`app/cloud/plugin_host.py:58,96-159`, `tools/bambu_cloud_host/event_queue.hpp:23-28`

`create_subprocess_exec` uses asyncio's default 64 KiB stream limit; `bridge.poll_events` drains the **entire** unbounded event queue into one JSONL line, and each OnMessage event carries a full raw MQTT payload (push_all frames are tens of KB). One oversized line kills `_read_loop` (broad `except Exception`), which fails only the currently-pending futures — the process is still alive, so every subsequent `call()` passes its guards and awaits a future nothing will resolve. EventPump and all cloud RPCs hang permanently.

**Fix:** pass `limit=` (e.g. 16 MiB), cap the drain batch size in C++, and make `call()` fail fast once the read loop is dead.

### 10. dev_id-less `OnUpdateStatus` events can be routed to the wrong printer
`app/main.py:311-314`, `tools/bambu_cloud_host/plugin_loader.cpp:395-403,442`

The OnUpdateStatus trampoline pushes only `{kind, stage, code, msg}` — no `dev_id` — so the fallback routes these to *whichever* client has a non-None `_progress`. The host's global in-flight CAS doesn't close the race: the worker clears `in_flight` before Python drains the queue, so printer B can start a job while printer A still awaits its terminal frame; dict order then decides who gets A's `done`. Related: both consumers `break` out of `submit_print`'s async-for (`main.py:1869-1873, 2228-2233`), leaving the generator suspended so `finally: self._progress = None` only runs at GC-scheduled `aclose()` — extending the misrouting window.

**Fix:** carry `dev_id` through the trampoline (the agent callback provides it per the print discovery notes), key progress queues by device, and wrap consumers in `contextlib.aclosing(...)` or drain to completion.

---

## Medium — robustness and parity

### 11. Cloud startup failure takes down the whole gateway (LAN included)
`app/main.py:257-280`. First boot (or invalidated cache) with the CDN unreachable → `ensure_active()` re-raises out of the lifespan → no web UI, no LAN printing. Degrade to LAN-only with cloud endpoints returning 503, and/or retry the download in the background. (Related: the download buffers the full ZIP in memory twice — `plugin_downloader.py:421` + `_extract_zip` — and the whole download/bootstrap runs serially before the app serves anything.)

### 12. `PluginHostError` surfaces as an unhandled 500 on control routes
`app/main.py:610-644`. The cloud branch of `_run_control_command` handles `rc != 0` (502) but not `PluginHostError` (host dead/busy); `auth_routes.py:44,68,78` shows the intended mapping (503). One `except PluginHostError` in the helper fixes pause/resume/cancel/speed/drying at once.

### 13. Printer CRUD doesn't touch the cloud registries
`app/printer_service.py:113-167`, `app/main.py:285-292`. `sync_printers` mutates only the LAN dicts; `_cloud_clients` and `app.state.cloud_printers` are built once at startup. A printer added via /settings is invisible to cloud routing until restart; a deleted one stays routable.

### 14. Cloud printers never fire the status-change callback → no push notifications
`app/printer_service.py:78-79`, `app/cloud/cloud_printer.py`. `NotificationHub.on_status_change` is wired only into `BambuMQTTClient`s. `CloudPrinterClient.handle_event` does no prev/new diffing and has no callback — APNs pushes and Live Activities never fire for cloud printers. (Compounds with #4: `notification_hub.py:72` also requires `new.online`.)

### 15. `/api/printers/{id}/light` was not migrated to the cloud-aware helper
`app/main.py:681-692`. `_resolve_printer_id` accepts cloud ids, then `set_chamber_light` does a LAN-only lookup → 404 for cloud printers. All six sibling controls (pause/resume/cancel/speed/start-drying/stop-drying) were migrated; only light was missed.

---

## Should we do something different? (structural recommendations)

**R1. Close the fake-host/real-host contract gap — this is the systemic root cause.**
Findings #1, #2, and #5 share one mechanism: the fake host implements methods the real host lacks (or accepts shapes the plugin rejects), so the suite passes while production fails. Recommendations, in increasing strength:
- Add a `list_methods` RPC to the C++ host and a test asserting the fake's method set ⊆ the real dispatch table (parse `methods.cpp` in the test if building the binary in CI is too heavy).
- Promote the six hand-rolled fake-host/lifespan fixtures (`test_cloud_auth_routes.py:39`, `test_cloud_command_dispatch.py:100`, four variants in `test_cloud_config.py`) into one parameterized `conftest.py` fixture so the lifespan contract is stubbed in exactly one place.
- Extend `docs/cloud-host-smoke-test.md` into a runnable end-to-end check (login → connect → subscribe → status → command) against the real binary, run before release.

**R2. Unify transport dispatch behind one printer-client interface.**
Findings #3, #6, #7, #13, #15 are all the same shape: cloud-vs-LAN dispatch is re-decided ad hoc at each entry point (`_get_cloud_client` tuples in routes, `_dispatch_command` in the service, `cloud_pair is None` in slice-jobs), so every entry point added or missed is a new hole. The deeper fix: a common protocol (`get_status / submit_print / send_command / ...`) implemented by both `BambuMQTTClient` and `CloudPrinterClient`, registered in **one** `PrinterService` registry keyed by serial, with `sync_printers` managing both kinds. Routes then never know which transport they're on, and `CloudPrinterClient` should own its `PluginHost` reference instead of every call site threading `(host, client)` tuples (`cloud_printer.py` `host=` params, `main.py:366` dual registries).

**R3. Extract the duplicated cloud submission block.**
`app/main.py:1844-1878` and `2186-2238` are near-verbatim copies (temp-file write, mapping reshape, `build_print_params`, frame loop, unlink) and have already drifted (plate source: form `plate_id or 1` vs job `cur.plate_id or 1`). One `_submit_cloud_print(...)` async helper serves both — and is the natural place to fix #5, #8, and the blocking I/O below, once, and to give `/api/slice-jobs` its cloud path (#7).

**R4. Add timeouts and bounds across the new async boundary.**
There is currently no timeout anywhere on the RPC path (`PluginHost.call`), the progress wait (`submit_print`), or the event queue (unbounded `std::deque` in `event_queue.hpp` — if the pump stalls, the C++ process grows without bound; a capped drop-oldest queue is correct since newer status supersedes older). Suggested defaults: 10–30 s on `call()`, a generous (minutes) ceiling on print-progress frames with host-death cancellation, batch cap on `poll_events`.

**R5. Don't block the event loop in print routes.**
`main.py:1849` writes the uploaded bytes synchronously; `main.py:2188-2192` reads the entire sliced 3MF into memory and rewrites it — yet `build_print_params` only embeds a path string and `cur.output_path` already points at a persistent file, so the SSE path needs **no copy at all**, and the upload path should use `asyncio.to_thread`.

**Smaller cleanups (confirmed, low priority):** region→API-base map duplicated (`main.py:127-131` vs `auth.py:101-104`, with divergent unknown-region behavior); BambuStudio identity headers duplicated (`auth.py:124-128` vs `plugin_downloader.py:208-222`); `validate_sha256` re-implements `_hex_sha256_of_file` in the same file (`plugin_downloader.py:109-131`); `auth.py` has four mid-file import blocks from section-wise assembly; `_run_control_command` routes through the private `printer_service._dispatch_command` whose LAN branch is unreachable from its only caller; EventPump polls every 250 ms even with no session (intentional per the upstream bridge it mirrors, but gating on signed-in state would be free).

---

## Suggested fix order

1. **#1 + #2** — wire `connect_server`/`start_subscribe`/`add_subscribe`, implement `is_user_login`/`user_logout` in the host. Until then nothing else is observable.
2. **#3 + #4** — single client per serial, `online` handling. Makes cloud printers visible and correct in the UI.
3. **#5** — ams_mapping array shape (data-corruption class: wrong filament).
4. **R3 then #6/#7/#8** — extract the shared submit helper, fix the entry-point gaps and the hang/rc-swallow inside it.
5. **#9, #10, R4** — stream limit, dev_id routing, timeouts/bounds.
6. **#11–#15, R1, R2, R5** and the small cleanups as follow-ups.
