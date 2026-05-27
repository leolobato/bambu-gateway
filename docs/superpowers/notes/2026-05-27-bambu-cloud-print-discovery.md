# Cloud Print C ABI Discovery — `start_print` + `OnUpdateStatusFn`

Sources examined:
- `src/slic3r/Utils/bambu_networking.hpp`
- `src/slic3r/Utils/BBLNetworkPlugin.hpp`
- `src/slic3r/Utils/BBLNetworkPlugin.cpp` (dlsym site: line 728)
- `src/slic3r/GUI/Jobs/PrintJob.cpp` (call site: lines 581, 587)

---

## 1. `start_print` — exact C ABI

### Symbol name (from `BBLNetworkPlugin.cpp:728`)

```
"bambu_network_start_print"
```

### Typedef (from `BBLNetworkPlugin.hpp:77`)

```cpp
typedef int (*func_start_print)(
    void*             agent,
    PrintParams       params,       // struct BY VALUE — full copy on the stack
    OnUpdateStatusFn  update_fn,    // std::function<void(int,int,std::string)>
    WasCancelledFn    cancel_fn,    // std::function<bool()>
    OnWaitFn          wait_fn       // std::function<bool(int, std::string)>
);
```

### Critical answer: struct by value

`PrintParams` is passed **by value** — not by pointer, not by reference, not as JSON.
The callee receives a copy of the struct placed on the stack per the platform C++ ABI.
There is **no JSON string entry point** for `start_print`.

The host must declare a struct whose layout is **binary-compatible** with the plugin's
`PrintParams`. Any struct change in a future Bambu plugin .so will silently break the
call unless the host's struct is updated to match.

### Threading / return-value semantics (from `PrintJob.cpp:580-588`)

`start_print` is **synchronous** in the sense that it blocks the calling thread until
the job is either accepted by the printer or has failed. Progress is reported
incrementally via the `update_fn` callback (called from the plugin's internal threads)
during the blocking period. The return value is an int:
- `0` — success (job accepted / sent)
- negative — error code (see §4 below)

---

## 2. `PrintParams` struct — full definition

Source: `bambu_networking.hpp:217-260`

```cpp
struct PrintParams {
    // --- Identity ---
    std::string dev_id;            // printer serial number
    std::string task_name;         // display name for the print task
    std::string project_name;      // project display name (≤100 chars, truncated)
    std::string preset_name;       // process profile name

    // --- Files ---
    std::string filename;          // local path to the gcode/3MF file to send
    std::string config_filename;   // local path to the config 3MF (may be empty)
    int         plate_index;       // 1-based plate index (0 = first plate)
    std::string ftp_folder;        // FTP destination subfolder (LAN path only)
    std::string ftp_file;          // FTP destination filename
    std::string ftp_file_md5;      // MD5 of the file (for integrity check)
    std::string dst_file;          // alternative destination path (if set)

    // --- AMS / Filament mapping ---
    std::string nozzle_mapping;    // JSON string: nozzle-to-filament mapping
    std::string ams_mapping;       // JSON string: AMS slot mapping (primary)
    std::string ams_mapping2;      // JSON string: AMS slot mapping (secondary/alt)
    std::string ams_mapping_info;  // human-readable mapping description
    std::string nozzles_info;      // JSON string: nozzle metadata

    // --- Routing ---
    std::string connection_type;   // "lan" or "cloud" — controls which code path
                                   //   the plugin takes internally
    std::string comments;          // extra routing hints set by OrcaSlicer:
                                   //   "no_ip", "low_version", "no_sdcard",
                                   //   "no_password", "upload_failed",
                                   //   "failed(<rc>)" — used for diagnostics

    // --- Origin / Provenance ---
    int         origin_profile_id = 0;  // cloud profile ID if printing from cloud
    int         stl_design_id     = 0;  // Bambu Design Studio design ID (0 if none)
    std::string origin_model_id;        // Bambu Design Studio model ID
    std::string print_type;             // "from_normal", "from_sdcard_view", etc.

    // --- LAN credentials (ignored on pure cloud path) ---
    std::string dev_name;          // printer display name
    std::string dev_ip;            // printer LAN IP
    bool        use_ssl_for_ftp;   // whether to use implicit FTPS
    bool        use_ssl_for_mqtt;  // whether to use TLS for MQTT

    std::string username;          // LAN username (always "bblp" for Bambu)
    std::string password;          // LAN access code

    // --- Print options (task_* flags) ---
    bool        task_bed_leveling;
    bool        task_flow_cali;
    bool        task_vibration_cali;
    bool        task_layer_inspect;
    bool        task_record_timelapse;
    bool        task_use_ams;
    std::string task_bed_type;     // e.g. "auto", "pc", "pei_plate"
    std::string extra_options;     // reserved / extra JSON options string

    // --- Auto-calibration overrides (newer plugin versions) ---
    int  auto_bed_leveling{0};
    int  auto_flow_cali{0};
    int  auto_offset_cali{0};
    int  extruder_cali_manual_mode{-1};  // -1 = use printer default

    // --- Additional flags ---
    bool task_ext_change_assist;   // assist with extruder change
    bool try_emmc_print;           // try printing from printer's eMMC
};
```

### What OrcaSlicer populates for a pure-cloud send (no LAN fallback)

From `PrintJob.cpp:201-360` (cloud path, `connection_type != "lan"`):

| Field | Value set |
|---|---|
| `dev_id` | printer serial |
| `dev_ip` | printer LAN IP (may be empty → `comments="no_ip"`) |
| `dev_name` | blank (not set in cloud path) |
| `username` | `"bblp"` |
| `password` | access code (may be empty → `comments="no_password"`) |
| `use_ssl_for_ftp` | `m_local_use_ssl_for_ftp` |
| `use_ssl_for_mqtt` | `m_local_use_ssl` |
| `filename` | full local path to the sliced 3MF |
| `config_filename` | path to config 3MF (may be empty) |
| `plate_index` | selected plate (1-based) |
| `ftp_folder` | `m_ftp_folder` |
| `connection_type` | `"cloud"` (literally — not "lan") |
| `comments` | set to routing hints if applicable, else `""` |
| `task_bed_leveling` | per-job flag |
| `task_flow_cali` | per-job flag |
| `task_vibration_cali` | per-job flag |
| `task_layer_inspect` | per-job flag |
| `task_record_timelapse` | per-job flag |
| `task_use_ams` | per-job flag |
| `task_bed_type` | e.g. `"auto"` |
| `nozzle_mapping` | JSON string |
| `ams_mapping` | JSON string |
| `ams_mapping2` | JSON string |
| `ams_mapping_info` | JSON string |
| `nozzles_info` | JSON string |
| `print_type` | `m_print_type` (e.g. `"from_normal"`) |
| `auto_bed_leveling` | int flag |
| `auto_flow_cali` | int flag |
| `auto_offset_cali` | int flag |
| `task_ext_change_assist` | bool |
| `try_emmc_print` | bool |
| `origin_profile_id` | cloud profile ID or 0 |
| `origin_model_id` | Design Studio model ID or `""` |
| `stl_design_id` | Design Studio design ID or 0 |
| `preset_name` | profile name (e.g. `"<project>_plate_<n>"`) |
| `project_name` | project display name |

---

## 3. `OnUpdateStatusFn` — exact callback signature

Source: `bambu_networking.hpp:124`

```cpp
typedef std::function<void(int status, int code, std::string msg)> OnUpdateStatusFn;
```

Parameters:
- `status` (`int`) — stage enum value (see §5)
- `code` (`int`) — per-stage code: 0-100 = progress percentage during Upload/Record;
  negative = error code; >100 = error
- `msg` (`std::string`) — human-readable message / info string

This is a `std::function` — **not** a raw function pointer. The plugin stores the
callable internally and invokes it from its own threads. The trampoline must be
stored (captured in the passed `std::function`) rather than registered separately
via a set_on_update_status_fn call, because there is **no separate setter** — the
callback is passed directly as an argument to each `start_print` call.

### Important: no `set_on_update_status_fn` function

Unlike `set_on_message_fn`, there is no plugin function called
`bambu_network_set_on_update_status_fn`. The `OnUpdateStatusFn` is passed
**per-invocation** as a parameter to `start_print` (and the other print methods).
The plan's spec for Phase B.1 (`register_update_status_callback`) does not apply as
written — there is nothing to dlsym/register at bootstrap time. Instead, each
`start_print` RPC call constructs an `OnUpdateStatusFn` lambda on the fly and passes
it as the third argument.

---

## 4. `WasCancelledFn` and `OnWaitFn`

Source: `bambu_networking.hpp:125-126`

```cpp
typedef std::function<bool()>                          WasCancelledFn;
typedef std::function<bool(int status, std::string job_info)> OnWaitFn;
```

`WasCancelledFn` — called by the plugin to check if the job was cancelled.
`OnWaitFn` — called during `PrintingStageWaitPrinter` with job info JSON; should
return `true` to continue waiting, `false` to abort.

For the gateway's RPC implementation:
- `WasCancelledFn`: always return `false` (no cancellation support in v1).
- `OnWaitFn`: always return `true` (let the plugin handle the timeout).

---

## 5. `SendingPrintJobStage` enum — complete

Source: `bambu_networking.hpp:136-146`

```cpp
enum SendingPrintJobStage {
    PrintingStageCreate      = 0,   // job creation / setup
    PrintingStageUpload      = 1,   // uploading file to OSS (code = upload %)
    PrintingStageWaiting     = 2,   // waiting for cloud acknowledgment
    PrintingStageSending     = 3,   // sending command to printer
    PrintingStageRecord      = 4,   // recording print task (code = progress %)
    PrintingStageWaitPrinter = 5,   // waiting for printer to acknowledge
    PrintingStageFinished    = 6,   // success (code unused; msg = countdown secs)
    PrintingStageERROR       = 7,   // error (code = error code, see §6)
    PrintingStageLimit       = 8,   // sentinel
};
```

---

## 6. Error code map — print-path (`WR` = with-record / cloud, `LP` = local)

### Generic codes (returned in `code` when `stage == PrintingStageERROR`)

| Code | Symbol | Meaning |
|------|---------|---------|
| -14 | `BAMBU_NETWORK_ERR_FILE_NOT_EXIST` | Local file not found |
| -15 | `BAMBU_NETWORK_ERR_FILE_OVER_SIZE` | File too large (generic) |
| -16 | `BAMBU_NETWORK_ERR_CHECK_MD5_FAILED` | MD5 check failed |
| -17 | `BAMBU_NETWORK_ERR_TIMEOUT` | Operation timed out |
| -18 | `BAMBU_NETWORK_ERR_CANCELED` | Job was cancelled |
| -20 | `BAMBU_NETWORK_ERR_FTP_UPLOAD_FAILED` | FTP upload failed (generic) |

### Cloud (WR = with-record) path codes

| Code | Symbol | Meaning |
|------|---------|---------|
| -2010 | `PRINT_WR_REQUEST_PROJECT_ID_FAILED` | Could not create project on cloud |
| -2020 | `PRINT_WR_CHECK_MD5_FAILED` | MD5 mismatch after OSS upload |
| -2030 | `PRINT_WR_UPLOAD_3MF_CONFIG_TO_OSS_FAILED` | Config 3MF upload to OSS failed |
| -2040 | `PRINT_WR_FILE_OVER_SIZE` | File exceeds cloud upload limit |
| -2050 | `PRINT_WR_PUT_NOTIFICATION_FAILED` | Failed to send print notification |
| -2060 | `PRINT_WR_GET_NOTIFICATION_TIMEOUT` | Printer did not acknowledge in time |
| -2070 | `PRINT_WR_GET_NOTIFICATION_FAILED` | Notification response was an error |
| -2080 | `PRINT_WR_PATCH_PROJECT_FAILED` | Failed to patch project metadata |
| -2090 | `PRINT_WR_GET_MY_SETTING_FAILED` | Could not retrieve user settings |
| -2100 | `PRINT_WR_FILE_NOT_EXIST` | File not found (cloud path) |
| -2110 | `PRINT_WR_UPLOAD_3MF_TO_OSS_FAILED` | 3MF upload to OSS failed |
| -2120 | `PRINT_WR_POST_TASK_FAILED` | Failed to post print task to cloud |
| -2130 | `PRINT_WR_UPLOAD_FTP_FAILED` | FTP upload failed (cloud+LAN hybrid) |
| -2140 | `PRINT_WR_GET_USER_UPLOAD_FAILED` | Could not get user upload config |

### LAN path codes

| Code | Symbol | Meaning |
|------|---------|---------|
| -4010 | `PRINT_LP_FILE_OVER_SIZE` | File too large for LAN/FTP upload |
| -4020 | `PRINT_LP_UPLOAD_FTP_FAILED` | FTP upload failed (LAN path) |
| -4030 | `PRINT_LP_PUBLISH_MSG_FAILED` | Failed to publish MQTT command |

### Connection errors

| Code | Symbol | Meaning |
|------|---------|---------|
| -6010 | `CONNECTION_TO_PRINTER_FAILED` | Could not reach printer |
| -6020 | `CONNECTION_TO_SERVER_FAILED` | Could not reach Bambu cloud |

---

## 7. Implementation guidance for Phase B.2

### Approach: struct by value (no JSON shortcut available)

There is no JSON-string entry point. The host **must** define a matching `PrintParams`
struct and call `bambu_network_start_print(agent, params, update_fn, cancel_fn, wait_fn)`.

The host struct must mirror the layout exactly as shown in §2. All `std::string`
members must be `std::string` (not `const char*`), all `bool` members must be `bool`,
all `int` members must be `int`. The `std::function` callbacks must be
`std::function<void(int,int,std::string)>`, `std::function<bool()>`, and
`std::function<bool(int,std::string)>` respectively.

**Warning:** Any change to `PrintParams` in a future Bambu plugin .so (field added,
reordered, or type changed) will silently corrupt memory. Pin the plugin .so version
and audit struct changes on each upgrade.

### Cloud-only fields to populate from JSON RPC params

For gateway use (`connection_type = "cloud"`):

Required: `dev_id`, `filename`, `connection_type`, `plate_index`

Recommended: `project_name`, `task_name`, `preset_name`, `task_bed_leveling`,
`task_flow_cali`, `task_vibration_cali`, `task_layer_inspect`, `task_record_timelapse`,
`task_use_ams`, `task_bed_type`, `ams_mapping`, `ams_mapping_info`, `print_type`

LAN fields (`dev_ip`, `password`, `username`, `use_ssl_for_ftp`, `use_ssl_for_mqtt`)
can be empty/false for pure cloud — the plugin takes the cloud path when
`connection_type != "lan"`.

### `OnUpdateStatusFn` trampoline — per-call, not at bootstrap

Pass an inline lambda as the `update_fn` argument:

```cpp
OnUpdateStatusFn update_fn = [](int stage, int code, std::string msg) {
    global_event_queue().push({
        {"kind",  "OnUpdateStatus"},
        {"stage", stage},
        {"code",  code},
        {"msg",   std::move(msg)},
    });
};
```

There is nothing to register at bootstrap time for `OnUpdateStatusFn`.
