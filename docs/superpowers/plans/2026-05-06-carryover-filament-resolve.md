# Carry-over filament resolution in `_normalize_filament_selection` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `filament_machine_mismatch` slicing failures when the iOS app submits a sparse filament-override dict against a machine different from the project's authored one — by routing un-overridden carry-over slot names through `orcaslicer-cli`'s `/profiles/resolve-for-machine` (the headless mirror of OrcaSlicer's `PresetBundle::update_compatible`) before sending the slice request.

**Architecture:** The gateway is the headless equivalent of the OrcaSlicer GUI's "active selection" layer — it composes machine + process + filaments before the slicer runs. The GUI rotates the active filament list to same-alias variants for the target machine on every machine change (`PresetBundle::update_compatible`); the gateway will mirror that for un-overridden slots in `_normalize_filament_selection`. User-overridden slots are never touched (the user's pick wins, matching the GUI). The slicer itself stays strict: incompatible inputs continue to return `filament_machine_mismatch` so genuine errors stay loud.

**Tech Stack:** Python (FastAPI / httpx async), pytest with `httpx.MockTransport`, no live-slicer dependency for unit tests.

**Branch:** `feat/carryover-filament-resolve` (already created off `feat/orca-headless`).

---

## File structure

**Modified:**
- `app/slicer_client.py:297-394` — `_build_v2_slice_body` passes `machine_profile` into `_normalize_filament_selection`; `_normalize_filament_selection` accepts the new keyword and delegates to a private `_resolve_carryover_filaments` helper that calls `resolve_for_machine` for un-overridden slots and substitutes by `match` reason.
- `tests/test_slicer_client_normalize.py` — replaces the legacy "no second-guessing" pin with the new contract: per-`match`-reason behaviour, user-override-wins, resolver-outage-falls-through, and machine-omitted-skips-resolver.

**No new files. No source changes outside of `slicer_client.py`.**

**Already modified in this branch (Task 1 below pins them):**
- `app/slicer_client.py` — implementation in place (live in the working tree, uncommitted)
- `tests/test_slicer_client_normalize.py` — six tests covering the new contract (live in the working tree, uncommitted)

---

## Behavioural contract

`_normalize_filament_selection(input_token, filament_profiles, *, machine_profile=None)`:

| Input shape | Resolver runs? |
|---|---|
| List form (`["A1M", "A1M"]`) | No — caller is explicitly setting all slots; trust them. |
| Dict form, no `machine_profile` | No — older callers / non-machine-context callers stay on legacy contract. |
| Dict form, `machine_profile` set, every slot overridden | No — nothing to resolve. |
| Dict form, `machine_profile` set, ≥1 carry-over slot | Yes — call `/profiles/resolve-for-machine` with the post-override list. |

When the resolver runs, per slot:

| Resolver `match` value | Action |
|---|---|
| `unchanged` | Leave as-is (already compatible). |
| `none` | Leave as-is (no compat candidate; let slicer's 400 surface the real problem). |
| `alias` / `type` / `default` / `first_compat` | Substitute `filament_ids[slot]` with resolved `name`, but only if slot is **not** in `overridden_slots`. |

Resolver outages (any `SlicingError` from `resolve_for_machine`) fall through silently with the post-override list; the slicer's own error path stays the source of truth for "we tried to slice an incompatible config". This keeps the gateway's failure mode loud (slicer 400 with diagnostic body) instead of swallowing problems behind a "resolver failed" log line.

---

### Task 1: Pin the implementation already in the working tree

The implementation diff is already in place. This task formalises it: run the new tests, fix any drift, commit.

**Files:**
- Modify: `app/slicer_client.py:297-498` (already edited in working tree)
- Modify: `tests/test_slicer_client_normalize.py` (already edited in working tree)

- [ ] **Step 1.1: Confirm the working-tree diff matches the contract**

Run:
```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
git diff --stat app/slicer_client.py tests/test_slicer_client_normalize.py
```

Expected:
```
 app/slicer_client.py                   | <some lines>
 tests/test_slicer_client_normalize.py  | <some lines>
 2 files changed, ...
```

- [ ] **Step 1.2: Run the new tests**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
.venv/bin/pytest tests/test_slicer_client_normalize.py -v
```

Expected: 7 tests pass —
- `test_carryover_slot_resolved_to_same_alias_variant`
- `test_user_override_wins_over_resolver`
- `test_unchanged_match_left_alone`
- `test_no_compat_match_leaves_authored_name`
- `test_resolver_outage_keeps_authored_names`
- `test_no_machine_profile_skips_resolver`
- `test_list_form_passes_through`

If a test fails, fix the implementation in `app/slicer_client.py` to match the test's pinned contract. Don't loosen tests. Re-run.

- [ ] **Step 1.3: Run the full slicer_client test suite to catch regressions**

```bash
.venv/bin/pytest tests/test_slicer_client_normalize.py tests/test_slicer_client_resolve.py tests/test_slicer_client_inspect.py -v
```

Expected: all tests pass. The carry-over change must not break the resolver round-trip pin (`test_resolver_resolve_for_machine_round_trip`) or the inspect adapter.

- [ ] **Step 1.4: Run the full project test suite to catch downstream regressions**

```bash
.venv/bin/pytest
```

Expected: all green (or only failures unrelated to slicer_client; if so, capture them in the commit body so the next person knows they're pre-existing).

- [ ] **Step 1.5: Commit**

```bash
git add app/slicer_client.py tests/test_slicer_client_normalize.py
git commit -m "$(cat <<'EOF'
Resolve carry-over filaments via /profiles/resolve-for-machine

Mirror what OrcaSlicer's GUI does on machine change
(`PresetBundle::update_compatible`): rotate the active filament list to
same-alias variants for the target machine before submitting a slice.
Without this, a project authored for a P2S printer that the user retargets
to an A1 mini submits with `Bambu PLA Basic @BBL P2S` carry-over names in
slots the user didn't override, and the slicer correctly rejects with
`filament_machine_mismatch`.

- `_normalize_filament_selection` now accepts an optional `machine_profile`
  and tracks which slots came from user overrides vs. the 3MF's authored
  list.
- Un-overridden slots are passed through `/profiles/resolve-for-machine`
  in one batch; substitutions happen only for slots whose `match` reason
  is `alias` / `type` / `default` / `first_compat`. `unchanged` and `none`
  leave names alone — `none` deliberately falls through to the slicer's
  own 400 so genuine "no compat candidate" errors stay loud.
- User-overridden slots are never touched; the user's explicit pick beats
  the resolver, matching the GUI.
- Resolver outages fall through silently — slicer's own error path is the
  source of truth.

EOF
)"
```

---

### Task 2: Smoke against the live slicer

Verify the fix end-to-end against `10.0.1.9:8070` so we know the wire
contract matches what the live `orcaslicer-cli 2.3.2-39` understands.

**Files:** none (read-only verification).

- [ ] **Step 2.1: Capture a project 3MF authored for a P2S printer**

If `tests/_fixtures` already has one, use it. Otherwise re-use the
fixture from the iOS reproducer the user shared (any 3MF whose
`filament_settings_id` array contains `"Bambu PLA Basic @BBL P2S"`).

```bash
ls /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway/tests/_fixtures 2>/dev/null
```

If no suitable fixture exists, skip Step 2.1 / 2.2 and rely on the unit tests + production smoke after deploy.

- [ ] **Step 2.2: Drive the gateway code path against the live slicer**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
.venv/bin/python - <<'EOF'
import asyncio
from app.slicer_client import SlicerClient

async def main():
    client = SlicerClient("http://10.0.1.9:8070")
    # Upload a P2S-authored 3MF (path to be filled in once Step 2.1 has it).
    # Then call client.slice(...) with sparse override on slot 0 only,
    # and machine_profile="GM020".
    # Expected: slice succeeds (no filament_machine_mismatch).
    pass

asyncio.run(main())
EOF
```

If a P2S fixture isn't available locally, document this step as deferred
to post-deploy production smoke and continue.

- [ ] **Step 2.3: No commit (verification only)**

---

### Task 3: Ship

`bambu-gateway` ships via Portainer redeploy. Verify the running gateway picks up the new behaviour against the live slicer (`2.3.2-39`).

- [ ] **Step 3.1: Identify the deploy mechanism**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
ls scripts/ 2>/dev/null
cat docker-compose.yml | head -20
```

Most likely: Portainer stack, image rebuilt and pushed via the same flow `orcaslicer-cli` uses.

- [ ] **Step 3.2: Build + push image (only if user explicitly requests deploy)**

`bambu-gateway`'s Docker image build is a thin Python container — much faster than `orcaslicer-cli`'s (~1 min vs ~30 min). Use whatever the project's `scripts/build-and-ship.sh` equivalent does.

If unclear, stop and ask.

- [ ] **Step 3.3: Smoke-test against live**

After deploy, reproduce the original failing case from the iOS app: open a P2S-authored project, pick A1 mini machine, override slot 0 only, hit Print. Slice must succeed.

- [ ] **Step 3.4: No commit (verification only).**

---

## Self-review

**Spec coverage:**
- [x] Carry-over slot resolution: Task 1 (Steps 1.1–1.5).
- [x] User-override wins: Task 1 (test pinned).
- [x] Resolver outage fallthrough: Task 1 (test pinned).
- [x] Machine-omitted callers unaffected: Task 1 (test pinned).
- [x] List-form callers unaffected: Task 1 (test pinned).
- [x] Live smoke: Task 2.
- [x] Deploy: Task 3.

**Placeholder scan:** Task 2 has a "if fixture isn't available, skip" branch — that's a real conditional, not a placeholder. Task 3 has a "stop and ask" branch — explicit by design (deploy isn't authorised by default).

**Type consistency:** `_normalize_filament_selection`'s new keyword `machine_profile: str | None` matches the call site in `_build_v2_slice_body` (passes `machine_profile=machine_profile`). `_resolve_carryover_filaments` returns `list[str]` — same type as the input it replaces. Match values are taken from the orcaslicer-cli `FilamentMatchReason` Literal: `unchanged | alias | type | default | first_compat | none`. Tests cover each branch.

**Risks:**
- **Resolver swap surprises the user.** If the user authored a project for P2S and intentionally wants P2S filaments slicing on an A1 mini (e.g. for debugging), the gateway now silently swaps. Mitigation: surface the resolved list in the slice response so the iOS app can show "we substituted these". Out of scope for this plan — the contract is "match the GUI"; the GUI also silently substitutes on machine change. The user can always override explicitly.
- **`match=none` still fails.** When the project's filament has no compat candidate for the target machine (e.g. PA12 on a low-temp printer), the slicer still 400s. That's correct behaviour — better to surface the real problem than swap to a default that won't print well.
- **Resolver latency.** One extra HTTP round-trip per slice (~5–20ms locally, ~30–80ms on the live remote). Acceptable; slice itself is seconds.
