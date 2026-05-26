# Bambu Cloud Plugin Support — Implementation Plan (Phases 0–1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the foundation for cloud-direct Bambu printing: resolve the four spec §11 open questions (Phase 0) and implement the plugin downloader (Phase 1) that fetches and validates Bambu's closed-source Linux `.so`s on gateway startup.

**Architecture:** Cloud mode is opt-in via env vars and exclusive. The plugin downloader runs at FastAPI startup, hits Bambu's CDN with forged `BambuStudio/02.05.02.51` headers and `X-BBL-OS-Type: linux`, validates ELF magic + SHA-256 + ABI version, and writes the binaries to `${BAMBU_CLOUD_PLUGIN_DIR}/active/`. Validation failure halts startup. Subsequent phases (subprocess host, auth, status, prints, controls) get their own plan after Phase 0 discoveries are in hand.

**Tech Stack:** Python 3.x, FastAPI, pydantic-settings, httpx, pytest with `asyncio_mode=auto`.

**Spec:** `docs/superpowers/specs/2026-05-26-bambu-cloud-plugin-design.md`

**Reference source tree (read-only):** `/Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/`

---

## File Structure

**New files this plan creates:**

| File | Purpose |
|---|---|
| `docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md` | Phase 0 discovery output (committed) |
| `app/cloud/__init__.py` | Package marker + pinned version constants |
| `app/cloud/plugin_downloader.py` | CDN fetch + manifest/SHA-256/ELF/ABI validation |
| `tests/test_cloud_plugin_downloader.py` | Unit tests for the downloader module |
| `tests/test_cloud_config.py` | Tests for new env-var settings |

**Existing files this plan modifies:**

| File | Change |
|---|---|
| `app/config.py` | Add `bambu_cloud_enabled`, `bambu_cloud_region`, `bambu_cloud_plugin_dir` settings |
| `app/main.py` | Call `PluginDownloader.ensure_active()` inside the lifespan startup when cloud mode is enabled |

Phases 2+ files (subprocess host, auth router, cloud client, event pump, etc.) are out of scope for this plan.

---

## Phase 0 — Discovery

Resolve spec §11 open questions by reading OrcaSlicer-bambulab source and probing Bambu's CDN. Output is `docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md` with concrete answers + verbatim citations.

### Task 0.1: Create the discovery notes file with the question template

**Files:**
- Create: `docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md`

- [ ] **Step 1: Create the file with skeleton sections**

```bash
mkdir -p docs/superpowers/notes
```

Write this exact content to `docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md`:

```markdown
# Bambu Cloud Plugin — Discovery Notes

Resolutions for the four open questions in `docs/superpowers/specs/2026-05-26-bambu-cloud-plugin-design.md` §11.

Source tree probed: `/Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/` (read-only).

## Q11.1 — OAuth redirect URI

(unanswered — see Task 0.2)

## Q11.2 — Two-3MF export from orcaslicer-headless

(unanswered — see Task 0.3)

## Q11.3 — Plugin CDN endpoint

(unanswered — see Task 0.4)

## Q11.4 — change_user() payload shape

(unanswered — see Task 0.5)

## Verified CDN reachability

(unanswered — see Task 0.6)
```

- [ ] **Step 2: Commit the skeleton**

```bash
git add docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md
git commit -m "Cloud plugin: scaffold discovery notes"
```

---

### Task 0.2: Resolve Q11.1 — OAuth redirect URI

**Files:**
- Modify: `docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md` (Q11.1 section)

- [ ] **Step 1: Read the WebView login dialog**

Read `/Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/GUI/WebUserLoginDialog.cpp` end-to-end. Look for:

- The `redirect_uri` query param value (often built from a constant)
- Any `setRedirectURL` / `loadURL` / `url +=` lines that compose the sign-in URL
- The JS-bridge handler that receives the post-login JSON (often `OnScriptMessage` or similar)

Also grep for `redirect_uri` across `src/slic3r/`:

```bash
grep -rn "redirect_uri" /Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/
```

- [ ] **Step 2: Document findings in Q11.1**

Replace the `## Q11.1` section's `(unanswered…)` line with:

```markdown
**Finding:** <one paragraph: is the redirect_uri hardcoded? built from a constant? configurable?>

**Citations:**
- `<file>:<line>` — `<verbatim code snippet>`
- `<file>:<line>` — `<verbatim code snippet>`

**Implication for bambu-gateway:**
- <one of: "we can register our own redirect_uri" / "we must intercept on a fixed URL the plugin emits" / "the URL is constructed inside the plugin and not configurable">

**Resulting plan for §6.2 of the spec:**
- <which of the three paths from §11.1 we will take: primary OAuth callback, loopback intercept, or paste fallback>
```

If the redirect URI is genuinely hardcoded inside the closed plugin and not configurable from outside, mark this as a **blocker** and write that explicitly in this section — the spec's primary OAuth flow will not work and we'll need to commit to the loopback intercept or paste fallback before Phase 4 (auth) can be planned.

- [ ] **Step 3: Commit the Q11.1 resolution**

```bash
git add docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md
git commit -m "Cloud plugin: resolve Q11.1 OAuth redirect URI"
```

---

### Task 0.3: Resolve Q11.2 — Two-3MF export from orcaslicer-headless

**Files:**
- Modify: `docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md` (Q11.2 section)

- [ ] **Step 1: Probe the orcaslicer-headless API surface**

Identify the running orcaslicer-headless URL from `.env` (`ORCASLICER_API_URL`). With it set as `$SLICER`, run:

```bash
curl -sS "$SLICER/openapi.json" | jq '.paths | keys'
curl -sS "$SLICER/" | head -100
```

Look for any `config-3mf`, `slice-info`, or `export-config` endpoint. Also inspect `app/slicer_client.py` to understand what `/slice` currently returns.

- [ ] **Step 2: Read OrcaSlicer's config-3MF export site**

Read `/Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/GUI/Plater.cpp` around line 15914 (the spec cites this range for the two-3MF save).

Note the save flags used for each:

- gcode-3MF: `Silence | SkipModel | WithGcode | SkipAuxiliary`
- config-3MF: `Silence | SkipModel | WithSliceInfo | SkipAuxiliary`

- [ ] **Step 3: Document findings in Q11.2**

Replace the `## Q11.2` section with:

```markdown
**Current orcaslicer-headless capability:** <does it return one 3MF or two? if one, which flags?>

**Citations:**
- `app/slicer_client.py:<line>` — <relevant snippet>
- orcaslicer-headless `<endpoint>` — <response shape from openapi or curl>

**Source flags from OrcaSlicer (Plater.cpp:15914-15962):**
- gcode-3MF: `Silence | SkipModel | WithGcode | SkipAuxiliary`
- config-3MF: `Silence | SkipModel | WithSliceInfo | SkipAuxiliary`

**Path forward for Phase 6 (print submission):**
- <one of: "extend orcaslicer-headless to return both" / "ship v1 without config-3MF, single gcode-3MF only" / "post-process gcode-3MF in-process to strip to config-only">
```

- [ ] **Step 4: Commit the Q11.2 resolution**

```bash
git add docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md
git commit -m "Cloud plugin: resolve Q11.2 two-3MF export"
```

---

### Task 0.4: Resolve Q11.3 — Plugin CDN endpoint

**Files:**
- Modify: `docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md` (Q11.3 section)

- [ ] **Step 1: Read the plugin-download site**

Read `/Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/Utils/PresetUpdater.cpp` lines 900-1050. Locate the function that fetches the network plugin — typically named `update_network_plugins` or `download_network_plugins`. Capture:

- Base URL constant (look at the top of the file or in nearby config)
- Path template (often `/api/v1/iot-network-plugin/...` or similar)
- HTTP method
- Query params
- Any response-shape expectations (JSON-listing-then-download? direct binary?)

Also read `/Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/Utils/PJarczakLinuxBridge/PJarczakLinuxBridgeConfig.cpp` for `linux_payload_manifest.json` references (lines 293-490 per the spec citation). Capture the manifest's expected JSON structure: top-level keys, `files[]` entries, what fields per entry (`name`, `sha256`, `abi_version`, …).

- [ ] **Step 2: Document the CDN endpoint and manifest schema in Q11.3**

Replace the `## Q11.3` section with:

```markdown
**Base URL:** `<https://...>`

**Endpoint(s):**
- `<METHOD> <path>` — <what it returns>
- (e.g. a JSON listing of available plugin versions, followed by a per-file download URL)

**Citations:**
- `PresetUpdater.cpp:<line>` — `<verbatim>`
- `PJarczakLinuxBridgeConfig.cpp:<line>` — `<verbatim>`

**Manifest schema (`linux_payload_manifest.json`):**

```json
{
  "version": "02.05.02.51",
  "files": [
    {
      "name": "libbambu_networking.so",
      "sha256": "...",
      "abi_version": "02.05.02.51",
      "size": 12345678
    },
    {
      "name": "libBambuSource.so",
      "sha256": "...",
      "size": 234567
    }
  ]
}
```
(Edit the above to match what the real manifest looks like; keep field names verbatim.)

**Required request headers (already in spec §5.1, confirming):**
- `User-Agent: BambuStudio/02.05.02.51`
- `X-BBL-Client-Type: slicer`
- `X-BBL-Client-Name: BambuStudio`
- `X-BBL-Client-Version: 02.05.02.51`
- `X-BBL-OS-Type: linux`
```

- [ ] **Step 3: Commit the Q11.3 resolution**

```bash
git add docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md
git commit -m "Cloud plugin: resolve Q11.3 CDN endpoint"
```

---

### Task 0.5: Resolve Q11.4 — `change_user()` payload shape

**Files:**
- Modify: `docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md` (Q11.4 section)

- [ ] **Step 1: Read the canonical payload builder**

Read `/Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/GUI/HttpServer.cpp` lines 38-65, the `build_canonical_login_payload` function. Capture the exact JSON shape it produces.

Then read `/Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/Utils/ICloudServiceAgent.hpp` lines 80-90 for the three accepted formats (token, username+password, nested-data) `change_user()` understands.

- [ ] **Step 2: Read the WebView → C++ bridge to see what Bambu actually sends back**

Grep for the JS bridge call in `WebUserLoginDialog.cpp` and any HTML/JS files that handle the redirect. The goal is to capture exactly what fields Bambu's hosted sign-in posts back (token, refresh_token, expires_in, user_id, user_info, …) and which of those go into `change_user`.

```bash
grep -rn "user_login\|setLoginInfo\|access_token\|refresh_token" \
  /Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/GUI/ | head -50
```

- [ ] **Step 3: Document in Q11.4**

Replace the `## Q11.4` section with the canonical payload shape (verbatim, so the auth implementation in a later phase can construct it):

```markdown
**Canonical `change_user()` payload (from `HttpServer.cpp:38-65`):**

```json
{
  "command": "user_login",
  "data": {
    "token": "...",
    "refresh_token": "...",
    "expires_in": "...",
    "refresh_expires_in": "...",
    "user_id": "...",
    "user": {
      "id": "...",
      "name": "...",
      "account": "...",
      "avatar": "..."
    }
  }
}
```

**Alternative legacy formats accepted (`ICloudServiceAgent.hpp:80-90`):**

- `{ "username": "...", "password": "..." }` — traditional login
- `{ "data": { ... } }` — token-only nested

**What Bambu's hosted sign-in actually posts back:**

<copy the field list from the WebView bridge handler, verbatim>

**Mapping for our gateway (Phase 4 OAuth flow will do this):**

Given the redirect query/fragment params Bambu returns, build the canonical `command=user_login, data={…}` envelope by mapping:

- `<bambu field>` → `data.token`
- `<bambu field>` → `data.refresh_token`
- …
```

- [ ] **Step 4: Commit the Q11.4 resolution**

```bash
git add docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md
git commit -m "Cloud plugin: resolve Q11.4 change_user payload"
```

---

### Task 0.6: Verify CDN reachability with forged headers

**Files:**
- Modify: `docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md` (Verified CDN reachability section)

This step exists to confirm the discovery is actionable *before* writing Phase 1 code — better to find out the CDN rejects us now than after we've built the downloader.

- [ ] **Step 1: Probe with curl using the headers from Task 0.4**

Using the base URL and endpoint discovered in Task 0.4:

```bash
curl -sS -i \
  -H "User-Agent: BambuStudio/02.05.02.51" \
  -H "X-BBL-Client-Type: slicer" \
  -H "X-BBL-Client-Name: BambuStudio" \
  -H "X-BBL-Client-Version: 02.05.02.51" \
  -H "X-BBL-OS-Type: linux" \
  "<base-url><endpoint>" | head -100
```

Expected: HTTP 200 with a JSON listing or manifest, or a redirect to the binary URL.

Also run a control test **without** the headers to confirm Bambu actively gates on them:

```bash
curl -sS -i "<base-url><endpoint>" | head -20
```

Expected: HTTP 4xx or different response body — confirms the gating.

- [ ] **Step 2: Document the results**

Replace the `## Verified CDN reachability` section with:

```markdown
**With forged BambuStudio headers:** `HTTP <code>`

Sample response body (first ~30 lines):
```
<paste here>
```

**Without headers (control):** `HTTP <code>` — confirms the CDN gates on identity.

**Conclusion:** <one of: "Phase 1 downloader is unblocked" / "blocked, reason: <…>">
```

If blocked, **stop** and surface the blocker to the user before continuing to Phase 1. Phase 1 assumes the CDN accepts our requests.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md
git commit -m "Cloud plugin: verify CDN reachability with forged headers"
```

---

## Phase 1 — Plugin downloader

Implement and unit-test the module that runs at gateway startup (when cloud mode is enabled), pulls the Linux plugin from Bambu's CDN with the forged BambuStudio identity, and validates ELF magic + SHA-256 manifest + ABI version pin before declaring the plugin "active". Failure halts FastAPI startup.

> **Prerequisite:** Phase 0 must be complete and `## Verified CDN reachability` must say "unblocked".

### Task 1.1: Add cloud-mode env-var settings

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_cloud_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cloud_config.py`:

```python
"""Tests for cloud-mode settings."""
from __future__ import annotations

from pathlib import Path

from app.config import Settings


def test_cloud_disabled_by_default(monkeypatch):
    monkeypatch.delenv("BAMBU_CLOUD_ENABLED", raising=False)
    monkeypatch.delenv("BAMBU_CLOUD_REGION", raising=False)
    monkeypatch.delenv("BAMBU_CLOUD_PLUGIN_DIR", raising=False)
    settings = Settings()
    assert settings.bambu_cloud_enabled is False
    assert settings.bambu_cloud_region == "US"
    assert settings.bambu_cloud_plugin_dir == Path("/data/bambu-plugin")


def test_cloud_enabled_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BAMBU_CLOUD_ENABLED", "true")
    monkeypatch.setenv("BAMBU_CLOUD_REGION", "CN")
    monkeypatch.setenv("BAMBU_CLOUD_PLUGIN_DIR", str(tmp_path))
    settings = Settings()
    assert settings.bambu_cloud_enabled is True
    assert settings.bambu_cloud_region == "CN"
    assert settings.bambu_cloud_plugin_dir == tmp_path


def test_cloud_region_rejects_unknown(monkeypatch):
    monkeypatch.setenv("BAMBU_CLOUD_REGION", "EU")
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cloud_config.py -v`

Expected: FAIL — `Settings` has no `bambu_cloud_*` attributes yet.

- [ ] **Step 3: Add the settings to `app/config.py`**

Open `app/config.py` and read it first to understand the existing `Settings` shape (it uses `pydantic-settings`'s `BaseSettings`). Add these fields to the `Settings` class (preserving existing ones):

```python
from pathlib import Path
from typing import Literal

# inside the Settings class, alongside the existing fields:
bambu_cloud_enabled: bool = False
bambu_cloud_region: Literal["US", "CN"] = "US"
bambu_cloud_plugin_dir: Path = Path("/data/bambu-plugin")
```

(If the existing `Settings` class uses `model_config` with `env_prefix=""`, the env-var names match the field names uppercased — `BAMBU_CLOUD_ENABLED` etc. If a prefix is set, adjust the test's `monkeypatch.setenv` calls accordingly. Read the existing class before editing.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cloud_config.py -v`

Expected: PASS — all three tests green.

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_cloud_config.py
git commit -m "Cloud plugin: add BAMBU_CLOUD_* settings"
```

---

### Task 1.2: Scaffold the `app/cloud/` package with pinned version constants

**Files:**
- Create: `app/cloud/__init__.py`

- [ ] **Step 1: Create the package**

Write `app/cloud/__init__.py`:

```python
"""Cloud-direct printing subsystem.

Only loaded when ``settings.bambu_cloud_enabled`` is True. Pinned to a
specific Bambu network-plugin release; bumping requires updating both
constants below in lockstep with the bundled ``.so`` files.
"""
from __future__ import annotations

#: Bambu network plugin ABI version that we are pinned to.
#: Used as ``X-BBL-Client-Version`` in CDN requests and as the ``abi_version``
#: that the manifest entry for ``libbambu_networking.so`` must declare.
#: Patch-level drift (first 8 chars, ``02.05.02``) is tolerated.
BAMBU_NETWORK_AGENT_VERSION = "02.05.02.51"

#: ``User-Agent`` string the slicer-side wrapper uses for every CDN call.
#: Must move together with :data:`BAMBU_NETWORK_AGENT_VERSION`.
BAMBU_STUDIO_USER_AGENT = f"BambuStudio/{BAMBU_NETWORK_AGENT_VERSION}"
```

- [ ] **Step 2: Commit**

```bash
git add app/cloud/__init__.py
git commit -m "Cloud plugin: scaffold app/cloud/ with pinned ABI constants"
```

---

### Task 1.3: Test and implement `bambu_studio_headers()`

**Files:**
- Create: `app/cloud/plugin_downloader.py`
- Test: `tests/test_cloud_plugin_downloader.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cloud_plugin_downloader.py`:

```python
"""Tests for the cloud plugin downloader."""
from __future__ import annotations

from app.cloud import BAMBU_NETWORK_AGENT_VERSION
from app.cloud.plugin_downloader import bambu_studio_headers


def test_headers_brand_as_bambu_studio_linux():
    headers = bambu_studio_headers()
    assert headers["User-Agent"] == f"BambuStudio/{BAMBU_NETWORK_AGENT_VERSION}"
    assert headers["X-BBL-Client-Type"] == "slicer"
    assert headers["X-BBL-Client-Name"] == "BambuStudio"
    assert headers["X-BBL-Client-Version"] == BAMBU_NETWORK_AGENT_VERSION
    assert headers["X-BBL-OS-Type"] == "linux"


def test_headers_returns_a_fresh_dict_each_call():
    a = bambu_studio_headers()
    a["User-Agent"] = "mutated"
    b = bambu_studio_headers()
    assert b["User-Agent"] == f"BambuStudio/{BAMBU_NETWORK_AGENT_VERSION}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_cloud_plugin_downloader.py::test_headers_brand_as_bambu_studio_linux -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.cloud.plugin_downloader'`.

- [ ] **Step 3: Implement `bambu_studio_headers()`**

Create `app/cloud/plugin_downloader.py`:

```python
"""Downloads and validates the Bambu Lab network plugin (Linux .so).

Runs at gateway startup when ``settings.bambu_cloud_enabled`` is True.
Pulls ``libbambu_networking.so`` and ``libBambuSource.so`` from Bambu's
CDN, validating ELF magic + SHA-256 manifest + ABI version pin before
declaring the plugin "active".
"""
from __future__ import annotations

from app.cloud import BAMBU_NETWORK_AGENT_VERSION, BAMBU_STUDIO_USER_AGENT


def bambu_studio_headers() -> dict[str, str]:
    """Headers that brand the request as a Linux build of Bambu Studio.

    Required by Bambu's CDN to serve the proprietary plugin payload.
    Identity must match :data:`BAMBU_NETWORK_AGENT_VERSION`; ``X-BBL-OS-Type:
    linux`` is load-bearing on every host (even when called from a Linux
    container) because it selects which prebuilt binary the CDN serves.
    """
    return {
        "User-Agent": BAMBU_STUDIO_USER_AGENT,
        "X-BBL-Client-Type": "slicer",
        "X-BBL-Client-Name": "BambuStudio",
        "X-BBL-Client-Version": BAMBU_NETWORK_AGENT_VERSION,
        "X-BBL-OS-Type": "linux",
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_cloud_plugin_downloader.py -v`

Expected: PASS — both header tests green.

- [ ] **Step 5: Commit**

```bash
git add app/cloud/plugin_downloader.py tests/test_cloud_plugin_downloader.py
git commit -m "Cloud plugin: forge BambuStudio identity headers"
```

---

### Task 1.4: Test and implement manifest parsing

**Files:**
- Modify: `app/cloud/plugin_downloader.py`
- Modify: `tests/test_cloud_plugin_downloader.py`

> Use the exact manifest schema captured in `docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md` §Q11.3. The schema below is a placeholder — **update field names if the real manifest disagrees** before writing the test.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cloud_plugin_downloader.py`:

```python
import json

import pytest

from app.cloud.plugin_downloader import (
    ManifestEntry,
    ManifestParseError,
    parse_manifest,
)


SAMPLE_MANIFEST = {
    "version": "02.05.02.51",
    "files": [
        {
            "name": "libbambu_networking.so",
            "sha256": "a" * 64,
            "abi_version": "02.05.02.51",
            "size": 12_345_678,
        },
        {
            "name": "libBambuSource.so",
            "sha256": "b" * 64,
            "size": 234_567,
        },
    ],
}


def test_parse_manifest_extracts_entries():
    manifest = parse_manifest(json.dumps(SAMPLE_MANIFEST).encode("utf-8"))
    assert manifest.version == "02.05.02.51"
    assert len(manifest.files) == 2
    networking = manifest.find("libbambu_networking.so")
    assert networking == ManifestEntry(
        name="libbambu_networking.so",
        sha256="a" * 64,
        abi_version="02.05.02.51",
        size=12_345_678,
    )
    source = manifest.find("libBambuSource.so")
    assert source.abi_version is None  # libBambuSource doesn't carry one


def test_parse_manifest_rejects_invalid_json():
    with pytest.raises(ManifestParseError):
        parse_manifest(b"not json")


def test_parse_manifest_rejects_missing_files_key():
    with pytest.raises(ManifestParseError):
        parse_manifest(json.dumps({"version": "x"}).encode("utf-8"))


def test_parse_manifest_rejects_entry_missing_sha256():
    bad = {"version": "x", "files": [{"name": "x.so"}]}
    with pytest.raises(ManifestParseError):
        parse_manifest(json.dumps(bad).encode("utf-8"))


def test_find_returns_none_for_unknown_name():
    manifest = parse_manifest(json.dumps(SAMPLE_MANIFEST).encode("utf-8"))
    assert manifest.find("does-not-exist.so") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_cloud_plugin_downloader.py -v -k manifest`

Expected: FAIL — `ImportError: cannot import name 'ManifestEntry'`.

- [ ] **Step 3: Implement manifest parsing**

Append to `app/cloud/plugin_downloader.py`:

```python
import json
from dataclasses import dataclass, field


class ManifestParseError(ValueError):
    """Raised when the plugin manifest JSON is malformed or missing fields."""


@dataclass(frozen=True)
class ManifestEntry:
    """One ``files[]`` entry from ``linux_payload_manifest.json``."""

    name: str
    sha256: str
    size: int
    abi_version: str | None = None


@dataclass(frozen=True)
class Manifest:
    """Parsed ``linux_payload_manifest.json``."""

    version: str
    files: tuple[ManifestEntry, ...] = field(default_factory=tuple)

    def find(self, name: str) -> ManifestEntry | None:
        for entry in self.files:
            if entry.name == name:
                return entry
        return None


def parse_manifest(blob: bytes) -> Manifest:
    """Parse the raw manifest bytes into a :class:`Manifest`.

    Raises :class:`ManifestParseError` if the JSON is invalid or any required
    field is missing.
    """
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ManifestParseError(f"manifest is not valid JSON: {exc}") from exc

    if not isinstance(data, dict) or "files" not in data or "version" not in data:
        raise ManifestParseError(
            "manifest must be an object with 'version' and 'files' keys"
        )

    entries: list[ManifestEntry] = []
    for raw in data["files"]:
        if not isinstance(raw, dict):
            raise ManifestParseError("each entry in files[] must be an object")
        try:
            entries.append(
                ManifestEntry(
                    name=raw["name"],
                    sha256=raw["sha256"],
                    size=int(raw["size"]),
                    abi_version=raw.get("abi_version"),
                )
            )
        except KeyError as exc:
            raise ManifestParseError(
                f"manifest entry missing required field: {exc.args[0]}"
            ) from exc

    return Manifest(version=str(data["version"]), files=tuple(entries))
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_cloud_plugin_downloader.py -v`

Expected: PASS — all manifest tests green plus the two header tests still pass.

- [ ] **Step 5: Commit**

```bash
git add app/cloud/plugin_downloader.py tests/test_cloud_plugin_downloader.py
git commit -m "Cloud plugin: parse linux_payload_manifest.json"
```

---

### Task 1.5: Test and implement SHA-256 validation

**Files:**
- Modify: `app/cloud/plugin_downloader.py`
- Modify: `tests/test_cloud_plugin_downloader.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cloud_plugin_downloader.py`:

```python
from app.cloud.plugin_downloader import IntegrityError, validate_sha256


def test_validate_sha256_accepts_matching_digest(tmp_path):
    p = tmp_path / "x.so"
    p.write_bytes(b"hello world")
    # sha256("hello world") =
    # b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9
    validate_sha256(p, "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9")


def test_validate_sha256_rejects_mismatch(tmp_path):
    p = tmp_path / "x.so"
    p.write_bytes(b"hello world")
    with pytest.raises(IntegrityError):
        validate_sha256(p, "0" * 64)


def test_validate_sha256_rejects_missing_file(tmp_path):
    with pytest.raises(IntegrityError):
        validate_sha256(tmp_path / "absent.so", "0" * 64)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_cloud_plugin_downloader.py -v -k sha256`

Expected: FAIL — `ImportError: cannot import name 'IntegrityError'`.

- [ ] **Step 3: Implement**

Append to `app/cloud/plugin_downloader.py`:

```python
import hashlib
from pathlib import Path


class IntegrityError(RuntimeError):
    """Raised when a downloaded plugin file fails integrity validation."""


def validate_sha256(path: Path, expected_hex: str) -> None:
    """Confirm that ``path`` hashes to ``expected_hex`` under SHA-256.

    Raises :class:`IntegrityError` on mismatch or if the file cannot be read.
    Reads in 1 MiB chunks so the plugin (often 10+ MiB) doesn't load entirely
    into memory.
    """
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError as exc:
        raise IntegrityError(f"cannot read {path}: {exc}") from exc

    actual = h.hexdigest()
    if actual.lower() != expected_hex.lower():
        raise IntegrityError(
            f"SHA-256 mismatch for {path.name}: "
            f"expected {expected_hex}, got {actual}"
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_cloud_plugin_downloader.py -v`

Expected: PASS — all tests green.

- [ ] **Step 5: Commit**

```bash
git add app/cloud/plugin_downloader.py tests/test_cloud_plugin_downloader.py
git commit -m "Cloud plugin: SHA-256 manifest validation"
```

---

### Task 1.6: Test and implement ELF magic + arch validation

**Files:**
- Modify: `app/cloud/plugin_downloader.py`
- Modify: `tests/test_cloud_plugin_downloader.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cloud_plugin_downloader.py`:

```python
from app.cloud.plugin_downloader import validate_elf


# A minimal valid ELF64 little-endian x86_64 header (52 bytes is enough; we
# only inspect the first 20). Bytes 0-3: magic. Byte 4: class (2 = ELF64).
# Byte 5: data encoding (1 = LE). Byte 6: version (1 = EV_CURRENT).
# Bytes 18-19: e_machine (0x3E = x86_64, little-endian).
_ELF_X86_64_LE = (
    b"\x7fELF"            # magic
    b"\x02"               # EI_CLASS = ELFCLASS64
    b"\x01"               # EI_DATA  = ELFDATA2LSB
    b"\x01"               # EI_VERSION
    b"\x00" * 9           # EI_OSABI..EI_PAD
    b"\x03\x00"           # e_type   = ET_DYN
    b"\x3e\x00"           # e_machine = EM_X86_64 (0x3E little-endian)
)


def test_validate_elf_accepts_x86_64_le(tmp_path):
    p = tmp_path / "x.so"
    p.write_bytes(_ELF_X86_64_LE + b"\x00" * 128)
    validate_elf(p)  # does not raise


def test_validate_elf_rejects_non_elf(tmp_path):
    p = tmp_path / "x.so"
    p.write_bytes(b"not an ELF file at all")
    with pytest.raises(IntegrityError):
        validate_elf(p)


def test_validate_elf_rejects_32bit(tmp_path):
    p = tmp_path / "x.so"
    blob = bytearray(_ELF_X86_64_LE)
    blob[4] = 1  # ELFCLASS32
    p.write_bytes(bytes(blob) + b"\x00" * 128)
    with pytest.raises(IntegrityError):
        validate_elf(p)


def test_validate_elf_rejects_big_endian(tmp_path):
    p = tmp_path / "x.so"
    blob = bytearray(_ELF_X86_64_LE)
    blob[5] = 2  # ELFDATA2MSB
    p.write_bytes(bytes(blob) + b"\x00" * 128)
    with pytest.raises(IntegrityError):
        validate_elf(p)


def test_validate_elf_rejects_non_x86_64_machine(tmp_path):
    p = tmp_path / "x.so"
    blob = bytearray(_ELF_X86_64_LE)
    blob[18] = 0xB7  # EM_AARCH64
    p.write_bytes(bytes(blob) + b"\x00" * 128)
    with pytest.raises(IntegrityError):
        validate_elf(p)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_cloud_plugin_downloader.py -v -k elf`

Expected: FAIL — `ImportError: cannot import name 'validate_elf'`.

- [ ] **Step 3: Implement**

Append to `app/cloud/plugin_downloader.py`:

```python
_ELF_MAGIC = b"\x7fELF"
_ELFCLASS64 = 2
_ELFDATA2LSB = 1
_EV_CURRENT = 1
_EM_X86_64 = 0x3E


def validate_elf(path: Path) -> None:
    """Sanity-check that ``path`` is a 64-bit little-endian x86_64 ELF.

    Does not parse program headers — only the first 20 bytes of the file are
    inspected. This catches "the CDN served us the wrong binary" without
    pulling in a full ELF parser. Mirrors OrcaSlicer-bambulab's
    ``validate_linux_so_binary`` (``PJarczakLinuxBridgeConfig.cpp:293+``).

    Raises :class:`IntegrityError` on any mismatch.
    """
    try:
        head = path.read_bytes()[:20]
    except OSError as exc:
        raise IntegrityError(f"cannot read {path}: {exc}") from exc

    if len(head) < 20 or head[:4] != _ELF_MAGIC:
        raise IntegrityError(f"{path.name} is not an ELF file")
    if head[4] != _ELFCLASS64:
        raise IntegrityError(f"{path.name} is not 64-bit ELF")
    if head[5] != _ELFDATA2LSB:
        raise IntegrityError(f"{path.name} is not little-endian")
    if head[6] != _EV_CURRENT:
        raise IntegrityError(f"{path.name} has unexpected ELF version")
    machine = int.from_bytes(head[18:20], "little")
    if machine != _EM_X86_64:
        raise IntegrityError(
            f"{path.name} is for machine 0x{machine:x}, expected x86_64 (0x3E)"
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_cloud_plugin_downloader.py -v`

Expected: PASS — all tests still green.

- [ ] **Step 5: Commit**

```bash
git add app/cloud/plugin_downloader.py tests/test_cloud_plugin_downloader.py
git commit -m "Cloud plugin: ELF magic + arch validation"
```

---

### Task 1.7: Test and implement ABI-version pin check

**Files:**
- Modify: `app/cloud/plugin_downloader.py`
- Modify: `tests/test_cloud_plugin_downloader.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cloud_plugin_downloader.py`:

```python
from app.cloud.plugin_downloader import (
    AbiVersionMismatch,
    validate_abi_version,
)


def test_abi_version_accepts_exact_match():
    validate_abi_version("02.05.02.51", pinned="02.05.02.51")


def test_abi_version_accepts_patch_level_drift():
    # First 8 chars (02.05.02) must match; the 4th component is patch.
    validate_abi_version("02.05.02.99", pinned="02.05.02.51")
    validate_abi_version("02.05.02.00", pinned="02.05.02.51")


def test_abi_version_rejects_minor_drift():
    with pytest.raises(AbiVersionMismatch):
        validate_abi_version("02.05.03.51", pinned="02.05.02.51")


def test_abi_version_rejects_major_drift():
    with pytest.raises(AbiVersionMismatch):
        validate_abi_version("03.05.02.51", pinned="02.05.02.51")


def test_abi_version_rejects_missing():
    with pytest.raises(AbiVersionMismatch):
        validate_abi_version(None, pinned="02.05.02.51")


def test_abi_version_rejects_garbage():
    with pytest.raises(AbiVersionMismatch):
        validate_abi_version("xyz", pinned="02.05.02.51")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_cloud_plugin_downloader.py -v -k abi`

Expected: FAIL — `ImportError: cannot import name 'AbiVersionMismatch'`.

- [ ] **Step 3: Implement**

Append to `app/cloud/plugin_downloader.py`:

```python
class AbiVersionMismatch(IntegrityError):
    """Raised when the manifest's abi_version diverges from the pinned one."""


def validate_abi_version(manifest_abi: str | None, *, pinned: str) -> None:
    """Confirm that the manifest declares a compatible ABI version.

    Compatibility rule mirrors OrcaSlicer-bambulab
    (``PJarczakLinuxBridgeConfig.cpp:293-490``): the first 8 chars
    (``02.05.02``) must match. The 4th component is patch-level and may
    drift.

    Raises :class:`AbiVersionMismatch` if the value is missing or
    incompatible.
    """
    if manifest_abi is None:
        raise AbiVersionMismatch(
            f"manifest entry is missing abi_version; expected {pinned}"
        )
    if len(manifest_abi) < 8 or len(pinned) < 8:
        raise AbiVersionMismatch(
            f"abi_version {manifest_abi!r} has unexpected shape"
        )
    if manifest_abi[:8] != pinned[:8]:
        raise AbiVersionMismatch(
            f"abi_version {manifest_abi!r} incompatible with pinned {pinned!r}"
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_cloud_plugin_downloader.py -v`

Expected: PASS — all tests green.

- [ ] **Step 5: Commit**

```bash
git add app/cloud/plugin_downloader.py tests/test_cloud_plugin_downloader.py
git commit -m "Cloud plugin: ABI-version pin check"
```

---

### Task 1.8: Test and implement the `PluginDownloader.ensure_active()` driver

**Files:**
- Modify: `app/cloud/plugin_downloader.py`
- Modify: `tests/test_cloud_plugin_downloader.py`

The driver glues everything together. Inputs: the target plugin dir and an `httpx.AsyncClient`. Output: a populated `${plugin_dir}/active/` containing both `.so`s + the manifest, all validated. Idempotent: skips work if `active/` already passes validation against the pinned version.

> The exact CDN URL paths come from Phase 0 Task 0.4. **Replace the `_NETWORK_PLUGIN_URL` and `_BAMBU_SOURCE_URL` constants below with the real ones before running the integration tests.**

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cloud_plugin_downloader.py`:

```python
import hashlib
import json as _json
from pathlib import Path

import httpx

from app.cloud.plugin_downloader import PluginDownloader


def _hex_sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _make_fake_so(name: str = "stub") -> bytes:
    # Minimal valid ELF64-LE-x86_64 header + padding so validate_elf passes.
    return _ELF_X86_64_LE + name.encode() + b"\x00" * 64


def _make_fake_cdn_handler(*, network_so: bytes, source_so: bytes):
    """Return an httpx mock handler that serves a manifest + both .so files."""
    manifest = {
        "version": "02.05.02.51",
        "files": [
            {
                "name": "libbambu_networking.so",
                "sha256": _hex_sha256(network_so),
                "abi_version": "02.05.02.51",
                "size": len(network_so),
            },
            {
                "name": "libBambuSource.so",
                "sha256": _hex_sha256(source_so),
                "size": len(source_so),
            },
        ],
    }
    manifest_blob = _json.dumps(manifest).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        # Brand check: every request must look like Linux Bambu Studio.
        assert request.headers["User-Agent"] == "BambuStudio/02.05.02.51"
        assert request.headers["X-BBL-OS-Type"] == "linux"

        url = str(request.url)
        if url.endswith("/manifest"):
            return httpx.Response(200, content=manifest_blob)
        if url.endswith("/libbambu_networking.so"):
            return httpx.Response(200, content=network_so)
        if url.endswith("/libBambuSource.so"):
            return httpx.Response(200, content=source_so)
        return httpx.Response(404)

    return handler


async def test_downloader_fetches_and_validates(tmp_path):
    network_so = _make_fake_so("network")
    source_so = _make_fake_so("source")
    handler = _make_fake_cdn_handler(network_so=network_so, source_so=source_so)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://cdn.example"
    ) as client:
        downloader = PluginDownloader(plugin_dir=tmp_path, client=client)
        await downloader.ensure_active()

    active = tmp_path / "active"
    assert (active / "libbambu_networking.so").read_bytes() == network_so
    assert (active / "libBambuSource.so").read_bytes() == source_so
    assert (active / "linux_payload_manifest.json").exists()


async def test_downloader_is_idempotent_when_already_valid(tmp_path):
    network_so = _make_fake_so("network")
    source_so = _make_fake_so("source")
    handler = _make_fake_cdn_handler(network_so=network_so, source_so=source_so)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://cdn.example"
    ) as client:
        downloader = PluginDownloader(plugin_dir=tmp_path, client=client)
        await downloader.ensure_active()

    # Second call must not hit the CDN — give the downloader a poisoned
    # client that 500s on any request.
    poisoned = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(500, text="should not be called")
        ),
        base_url="https://cdn.example",
    )
    try:
        downloader = PluginDownloader(plugin_dir=tmp_path, client=poisoned)
        await downloader.ensure_active()  # must not raise
    finally:
        await poisoned.aclose()


async def test_downloader_rejects_tampered_so(tmp_path):
    network_so = _make_fake_so("network")
    source_so = _make_fake_so("source")
    base_handler = _make_fake_cdn_handler(
        network_so=network_so, source_so=source_so
    )

    # Override the network .so served to be different content (manifest
    # still claims the original SHA), so SHA validation must reject it.
    def tampered(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/libbambu_networking.so"):
            return httpx.Response(200, content=b"\x7fELF" + b"\x00" * 200)
        return base_handler(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(tampered),
        base_url="https://cdn.example",
    ) as client:
        downloader = PluginDownloader(plugin_dir=tmp_path, client=client)
        with pytest.raises(IntegrityError):
            await downloader.ensure_active()
    # No artifacts left behind on failure.
    assert not (tmp_path / "active").exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_cloud_plugin_downloader.py -v -k downloader`

Expected: FAIL — `ImportError: cannot import name 'PluginDownloader'`.

- [ ] **Step 3: Implement `PluginDownloader`**

Append to `app/cloud/plugin_downloader.py`:

```python
import logging
import shutil
from pathlib import Path

import httpx

from app.cloud import BAMBU_NETWORK_AGENT_VERSION

logger = logging.getLogger("bambu.cloud.downloader")

# REPLACE these with the real endpoint paths discovered in Phase 0 Task 0.4.
_MANIFEST_URL = "/manifest"
_NETWORK_PLUGIN_URL = "/libbambu_networking.so"
_BAMBU_SOURCE_URL = "/libBambuSource.so"

_NETWORK_SO = "libbambu_networking.so"
_SOURCE_SO = "libBambuSource.so"
_MANIFEST_FILE = "linux_payload_manifest.json"


class PluginDownloader:
    """Downloads, validates, and persists the Bambu network plugin.

    Idempotent: ``ensure_active()`` is a no-op if ``${plugin_dir}/active/``
    already contains both ``.so`` files matching the pinned manifest.
    """

    def __init__(self, *, plugin_dir: Path, client: httpx.AsyncClient) -> None:
        self._plugin_dir = Path(plugin_dir)
        self._client = client

    @property
    def _active(self) -> Path:
        return self._plugin_dir / "active"

    async def ensure_active(self) -> None:
        """Guarantee that ``active/`` contains a valid pinned plugin set.

        Raises an :class:`IntegrityError` subclass on validation failure.
        On any failure, partially-written files are removed so the next
        startup tries again from scratch.
        """
        if self._active_is_valid():
            logger.info(
                "Bambu plugin already present and valid at %s; skipping fetch",
                self._active,
            )
            return

        staging = self._plugin_dir / "staging"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)

        try:
            manifest_blob = await self._get(_MANIFEST_URL)
            manifest = parse_manifest(manifest_blob)

            net_entry = manifest.find(_NETWORK_SO)
            src_entry = manifest.find(_SOURCE_SO)
            if net_entry is None or src_entry is None:
                raise ManifestParseError(
                    f"manifest missing required files: {_NETWORK_SO}, "
                    f"{_SOURCE_SO}"
                )
            validate_abi_version(
                net_entry.abi_version, pinned=BAMBU_NETWORK_AGENT_VERSION
            )

            net_path = staging / _NETWORK_SO
            src_path = staging / _SOURCE_SO
            net_path.write_bytes(await self._get(_NETWORK_PLUGIN_URL))
            src_path.write_bytes(await self._get(_BAMBU_SOURCE_URL))

            validate_elf(net_path)
            validate_elf(src_path)
            validate_sha256(net_path, net_entry.sha256)
            validate_sha256(src_path, src_entry.sha256)

            (staging / _MANIFEST_FILE).write_bytes(manifest_blob)

            # Atomic-ish swap: remove old active/, rename staging -> active/.
            if self._active.exists():
                shutil.rmtree(self._active)
            staging.rename(self._active)
            logger.info(
                "Bambu plugin %s installed to %s",
                manifest.version,
                self._active,
            )
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(self._active, ignore_errors=True)
            raise

    async def _get(self, path: str) -> bytes:
        response = await self._client.get(path, headers=bambu_studio_headers())
        response.raise_for_status()
        return response.content

    def _active_is_valid(self) -> bool:
        """Cheap pre-flight: do the files exist and match the manifest?

        Returns False on any discrepancy (caller will then re-fetch).
        """
        manifest_path = self._active / _MANIFEST_FILE
        if not manifest_path.exists():
            return False
        try:
            manifest = parse_manifest(manifest_path.read_bytes())
            net_entry = manifest.find(_NETWORK_SO)
            src_entry = manifest.find(_SOURCE_SO)
            if net_entry is None or src_entry is None:
                return False
            validate_abi_version(
                net_entry.abi_version, pinned=BAMBU_NETWORK_AGENT_VERSION
            )
            validate_sha256(self._active / _NETWORK_SO, net_entry.sha256)
            validate_sha256(self._active / _SOURCE_SO, src_entry.sha256)
        except IntegrityError:
            return False
        return True
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_cloud_plugin_downloader.py -v`

Expected: PASS — all tests green.

- [ ] **Step 5: Commit**

```bash
git add app/cloud/plugin_downloader.py tests/test_cloud_plugin_downloader.py
git commit -m "Cloud plugin: ensure_active() download + validate driver"
```

---

### Task 1.9: Wire the downloader into FastAPI lifespan startup

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_cloud_config.py` (extend with a startup test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cloud_config.py`:

```python
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def test_lifespan_calls_downloader_when_cloud_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("BAMBU_CLOUD_ENABLED", "true")
    monkeypatch.setenv("BAMBU_CLOUD_PLUGIN_DIR", str(tmp_path))
    # Avoid the LAN-mode startup work that would otherwise try to connect to
    # printers; the existing test suite has a pattern for this — re-use it
    # if available, or stub PrinterService.start as below.
    with (
        patch(
            "app.cloud.plugin_downloader.PluginDownloader.ensure_active",
            new=AsyncMock(return_value=None),
        ) as mock_ensure,
        patch("app.printer_service.PrinterService.start", new=AsyncMock()),
    ):
        from app.main import app  # imported lazily so env vars take effect
        with TestClient(app):
            pass
        mock_ensure.assert_awaited_once()


def test_lifespan_skips_downloader_when_cloud_disabled(monkeypatch):
    monkeypatch.setenv("BAMBU_CLOUD_ENABLED", "false")
    with (
        patch(
            "app.cloud.plugin_downloader.PluginDownloader.ensure_active",
            new=AsyncMock(return_value=None),
        ) as mock_ensure,
        patch("app.printer_service.PrinterService.start", new=AsyncMock()),
    ):
        from app.main import app
        with TestClient(app):
            pass
        mock_ensure.assert_not_awaited()
```

> Before running, read `app/main.py` and `app/printer_service.py` to confirm the actual method name used for startup. If it's not `PrinterService.start`, change the `patch` target to whatever the lifespan currently calls. If the test suite already provides a fixture that stubs printer startup (search `tests/conftest.py` and `tests/test_*.py` for `PrinterService` mocking), use that fixture instead.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_cloud_config.py::test_lifespan_calls_downloader_when_cloud_enabled -v`

Expected: FAIL — the lifespan does not yet call the downloader.

- [ ] **Step 3: Wire it in `app/main.py`**

Read `app/main.py` first to find the lifespan context manager. Add the cloud branch immediately before whatever currently kicks off the LAN-mode `PrinterService`. The change is shape:

```python
from contextlib import asynccontextmanager

import httpx

from app.config import settings  # or however the existing module imports it
from app.cloud.plugin_downloader import PluginDownloader


@asynccontextmanager
async def lifespan(app):
    if settings.bambu_cloud_enabled:
        async with httpx.AsyncClient(
            base_url=_bambu_cdn_base_url(settings.bambu_cloud_region),
            timeout=30.0,
        ) as cdn_client:
            downloader = PluginDownloader(
                plugin_dir=settings.bambu_cloud_plugin_dir,
                client=cdn_client,
            )
            await downloader.ensure_active()
    # ... existing PrinterService startup follows ...
    yield
    # ... existing teardown ...


def _bambu_cdn_base_url(region: str) -> str:
    # The exact host comes from Phase 0 Task 0.4. Replace this stub.
    if region == "CN":
        return "https://api.bambulab.cn"
    return "https://api.bambulab.com"
```

Adapt the function body to the lifespan structure that already exists in `main.py` — do not rewrite the whole lifespan.

- [ ] **Step 4: Run the full new test file**

Run: `.venv/bin/pytest tests/test_cloud_config.py -v`

Expected: PASS — both lifespan tests green.

- [ ] **Step 5: Run the full suite to confirm nothing regressed**

Run: `.venv/bin/pytest -q`

Expected: PASS — every previously-passing test still passes.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_cloud_config.py
git commit -m "Cloud plugin: run downloader from FastAPI lifespan"
```

---

## Wrap-up

Phase 0 + Phase 1 are complete. The next plan (`docs/superpowers/plans/2026-XX-XX-bambu-cloud-plugin-host.md`) will cover Phase 2 (small C++ subprocess host) and Phase 3 (Python PluginHost + event pump), informed by the Phase 0 discovery notes.

**Final manual smoke test (optional, requires real CDN reachability):**

1. `export BAMBU_CLOUD_ENABLED=true BAMBU_CLOUD_PLUGIN_DIR=$(mktemp -d)`
2. `.venv/bin/python -m app` (or `uvicorn app.main:app --reload`)
3. Confirm log line `Bambu plugin 02.05.02.51 installed to /tmp/.../active`
4. `ls $BAMBU_CLOUD_PLUGIN_DIR/active/` — both `.so`s + manifest present
5. Restart the process and confirm the log says "already present and valid; skipping fetch"
