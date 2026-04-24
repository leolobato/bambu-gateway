# Web UI Redesign — Phase 5: Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the existing `/settings` page (Printers CRUD + Push Notifications) onto the Phase 1–4 design system at `/beta/settings`, plus a small About card. The existing Jinja `/settings` route stays untouched until the Phase 6 cutover.

**Architecture:** Three Card-wrapped sections inside the existing `<AppShell/>`. Printers and Push Notifications reuse `<TrayRow/>` from Phase 2 — same row primitive that already shapes the AMS section. Add/Edit Printer is a shadcn `<Dialog/>`; per-row Edit/Delete uses shadcn `<DropdownMenu/>` (newly installed). Forms use plain controlled `useState` + a tiny inline validator (`react-hook-form` + `zod` would be 60 kB of overkill for five fields). Mutations go through React Query (`useMutation`) and invalidate the relevant queries on success.

**Tech Stack:** All Phase 1–4 deps stay. New shadcn primitives this phase installs: `input`, `label`, `dropdown-menu`. One small backend addition: extend `/api/capabilities` to include `version` so the About card can display the running gateway version.

**Spec reference:** `docs/superpowers/specs/2026-04-23-webui-redesign-design.md` — section "Settings route" + the "Incremental ship plan" step 5.

**Deliberate deferrals (out of scope for Phase 5):**

- The spec's About card mentions "MQTT/FTPS/slicer connection test buttons." No backend endpoints exist for MQTT or FTPS connection tests, and adding them is a non-trivial feature in its own right (per-printer connection probes that don't crash the live MQTT loop). Phase 5 ships an About card with just the version + repo link; the test buttons can land as a follow-up phase or as part of Phase 6.
- The spec mentions "section-level 'Notification settings' link opens a sub-dialog" under Push Notifications. There's no backend for per-section notification settings (the toggle is push-enabled-globally via env vars). Skipped — would be a UX placeholder otherwise.

---

## File structure

**Created in this plan:**

```
web/
└── src/
    ├── lib/
    │   └── api/
    │       ├── printer-configs.ts    # GET/POST/PUT/DELETE /api/settings/printers
    │       ├── devices.ts            # GET /api/devices, DELETE, POST test
    │       └── capabilities.ts       # GET /api/capabilities (includes version)
    └── components/
        ├── ui/
        │   ├── input.tsx             # shadcn primitive
        │   ├── label.tsx             # shadcn primitive
        │   └── dropdown-menu.tsx     # shadcn primitive
        └── settings/
            ├── printers-section.tsx       # Card wrapper + list of printer rows + Add button
            ├── printer-row.tsx            # one row in the printers list (status dot + name + overflow menu)
            ├── printer-form-dialog.tsx    # Add/Edit dialog with validated inputs
            ├── push-section.tsx           # Card wrapper + status line + device list
            ├── push-device-row.tsx        # one device row (Test push + Delete actions)
            └── about-section.tsx          # version + repo link
tests/
└── test_capabilities_endpoint.py     # version field in /api/capabilities
```

**Modified in this plan:**

- `app/main.py` — extend `get_capabilities()` to include `app.version`. Also add `version: str` to the `CapabilitiesResponse` model in `app/models.py`.
- `app/models.py` — add `version: str = ""` to `CapabilitiesResponse`.
- `web/src/lib/api/types.ts` — add `PrinterConfigResponse`, `PrinterConfigInput`, `DeviceInfo`, `Capabilities` interfaces (mirroring the Pydantic models).
- `web/src/routes/settings.tsx` — replace placeholder with the three sections.
- `web/components.json`, `web/package.json`, `web/package-lock.json` — only via `npx shadcn@latest add input label dropdown-menu`.

**Untouched in this plan:**

- All other `app/*` Python files. The CRUD routes (`/api/settings/printers`, `/api/devices`) already exist.
- Phase 2/3/4 frontend code.
- Old Jinja `/settings` route (kept until Phase 6 cutover).

## Prerequisites

- Phase 4 must be on `main` (commit `002b6d0` or later).
- Local FastAPI at `http://localhost:4844`.
- At least one printer in `printers.json` so the Printers list isn't empty during smoke-test.
- `cd web && npm install` already done.

---

## Task 0: Backend — add `version` to `/api/capabilities`

**Files:**
- Modify: `app/main.py:291-296` (the `get_capabilities()` route)
- Modify: `app/models.py` (add `version: str = ""` to `CapabilitiesResponse`)
- Create: `tests/test_capabilities_endpoint.py`

- [ ] **Step 0.1: Write the failing test**

Create `tests/test_capabilities_endpoint.py`:

```python
"""Tests for the version field on GET /api/capabilities."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("APNS_KEY_PATH", "")
    monkeypatch.chdir(tmp_path)
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_getCapabilities_includesVersion(client):
    res = client.get("/api/capabilities")
    assert res.status_code == 200
    body = res.json()
    assert "version" in body
    # The exact string is whatever the FastAPI app declares; just check it's
    # a non-empty semver-ish value.
    assert isinstance(body["version"], str)
    assert body["version"]
```

- [ ] **Step 0.2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_capabilities_endpoint.py -v`
Expected: 1 failed (`'version' not in body`).

Then run the existing capabilities test to confirm we don't break the older shape check:

Run: `.venv/bin/pytest tests/test_device_endpoints.py::test_capabilities_reports_push_disabled -v`
Expected: PASS (the existing test does `body == {"push": False, "live_activities": False}` — that exact-match assertion will need updating in Step 0.5).

- [ ] **Step 0.3: Add `version` to the `CapabilitiesResponse` model**

Open `app/models.py` and find the `CapabilitiesResponse` class:

```python
class CapabilitiesResponse(BaseModel):
    push: bool
    live_activities: bool
```

Replace with:

```python
class CapabilitiesResponse(BaseModel):
    push: bool
    live_activities: bool
    version: str = ""
```

- [ ] **Step 0.4: Wire the version into the route**

Open `app/main.py` and find the `get_capabilities()` route (~line 291):

```python
@app.get("/api/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities():
    return CapabilitiesResponse(
        push=settings.push_enabled,
        live_activities=settings.push_enabled,
    )
```

Replace with:

```python
@app.get("/api/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities():
    return CapabilitiesResponse(
        push=settings.push_enabled,
        live_activities=settings.push_enabled,
        version=app.version,
    )
```

`app.version` comes from the `FastAPI(title="Bambu Gateway", version="1.5.0", lifespan=lifespan)` declaration at `app/main.py:236` — no extra import needed.

- [ ] **Step 0.5: Update the older capabilities test to allow the new field**

Open `tests/test_device_endpoints.py` and find:

```python
def test_capabilities_reports_push_disabled(client):
    res = client.get("/api/capabilities")
    assert res.status_code == 200
    body = res.json()
    assert body == {"push": False, "live_activities": False}
```

Replace the `assert body == ...` line with field-by-field checks so the test doesn't break each time we add a new capabilities field:

```python
def test_capabilities_reports_push_disabled(client):
    res = client.get("/api/capabilities")
    assert res.status_code == 200
    body = res.json()
    assert body["push"] is False
    assert body["live_activities"] is False
```

- [ ] **Step 0.6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_capabilities_endpoint.py tests/test_device_endpoints.py -v`
Expected: all green.

Then run the full suite:

Run: `.venv/bin/pytest`
Expected: all green (105 total — was 104).

- [ ] **Step 0.7: Commit**

```bash
git add app/main.py app/models.py tests/test_capabilities_endpoint.py tests/test_device_endpoints.py
git commit -m "Expose gateway version on GET /api/capabilities"
```

---

## Task 1: Mirror new types in `types.ts`

**Files:**
- Modify: `web/src/lib/api/types.ts`

- [ ] **Step 1.1: Append new interfaces**

Open `web/src/lib/api/types.ts` and append at the end:

```typescript
// --- Settings: printer configs (mirror app/models.py) ---

export interface PrinterConfigResponse {
  serial: string;
  ip: string;
  name: string;
  machine_model: string;
}

export interface PrinterConfigListResponse {
  printers: PrinterConfigResponse[];
}

export interface PrinterConfigInput {
  serial: string;
  ip: string;
  access_code: string;
  name: string;
  machine_model: string;
}

// --- Settings: push devices (mirror app/models.py DeviceInfo) ---

export interface DeviceInfo {
  id: string;
  name: string;
  has_device_token: boolean;
  has_live_activity_start_token: boolean;
  active_activity_count: number;
  subscribed_printers: string[];
  registered_at: string;
  last_seen_at: string;
}

export interface DeviceListResponse {
  devices: DeviceInfo[];
}

// --- Settings: capabilities (push enabled + gateway version) ---

export interface Capabilities {
  push: boolean;
  live_activities: boolean;
  version: string;
}
```

- [ ] **Step 1.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 1.3: Commit**

```bash
git add web/src/lib/api/types.ts
git commit -m "Add Phase 5 types: printer configs, devices, capabilities"
```

---

## Task 2: Add API helpers for Settings endpoints

**Files:**
- Create: `web/src/lib/api/printer-configs.ts`, `web/src/lib/api/devices.ts`, `web/src/lib/api/capabilities.ts`

- [ ] **Step 2.1: Create `web/src/lib/api/printer-configs.ts`**

```typescript
import { fetchJson } from './client';
import type {
  PrinterConfigInput,
  PrinterConfigListResponse,
  PrinterConfigResponse,
} from './types';

export async function listPrinterConfigs(): Promise<PrinterConfigListResponse> {
  return fetchJson<PrinterConfigListResponse>('/api/settings/printers');
}

export async function createPrinterConfig(
  input: PrinterConfigInput,
): Promise<PrinterConfigResponse> {
  return fetchJson<PrinterConfigResponse>('/api/settings/printers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
}

export async function updatePrinterConfig(
  serial: string,
  input: PrinterConfigInput,
): Promise<PrinterConfigResponse> {
  return fetchJson<PrinterConfigResponse>(
    `/api/settings/printers/${encodeURIComponent(serial)}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    },
  );
}

export async function deletePrinterConfig(serial: string): Promise<void> {
  // 204 No Content — fetchJson is JSON-only, so use fetch directly here.
  const res = await fetch(`/api/settings/printers/${encodeURIComponent(serial)}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // not JSON
    }
    throw new Error(`Delete failed: ${detail}`);
  }
}
```

- [ ] **Step 2.2: Create `web/src/lib/api/devices.ts`**

```typescript
import { fetchJson } from './client';
import type { DeviceListResponse } from './types';

export async function listDevices(): Promise<DeviceListResponse> {
  return fetchJson<DeviceListResponse>('/api/devices');
}

export async function deleteDevice(deviceId: string): Promise<void> {
  await fetchJson<unknown>(`/api/devices/${encodeURIComponent(deviceId)}`, {
    method: 'DELETE',
  });
}

export async function sendTestPush(deviceId: string): Promise<void> {
  await fetchJson<unknown>(`/api/devices/${encodeURIComponent(deviceId)}/test`, {
    method: 'POST',
  });
}
```

- [ ] **Step 2.3: Create `web/src/lib/api/capabilities.ts`**

```typescript
import { fetchJson } from './client';
import type { Capabilities } from './types';

export async function getCapabilities(): Promise<Capabilities> {
  return fetchJson<Capabilities>('/api/capabilities');
}
```

- [ ] **Step 2.4: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 2.5: Commit**

```bash
git add web/src/lib/api/printer-configs.ts web/src/lib/api/devices.ts web/src/lib/api/capabilities.ts
git commit -m "Add API helpers for printer configs, devices, capabilities"
```

---

## Task 3: Install shadcn primitives needed for Phase 5

**Files:**
- Create (via shadcn CLI): `web/src/components/ui/input.tsx`, `web/src/components/ui/label.tsx`, `web/src/components/ui/dropdown-menu.tsx`
- Modify: `web/package.json`, `web/package-lock.json` (CLI installs `@radix-ui/react-label`, `@radix-ui/react-dropdown-menu`)

- [ ] **Step 3.1: Add the three primitives in one CLI run**

Run from the repo root:

```bash
cd web && npx shadcn@latest add input label dropdown-menu --yes --overwrite
```

Expected: the three component files are written to `web/src/components/ui/`. The CLI installs `@radix-ui/react-label` and `@radix-ui/react-dropdown-menu` as new peer deps.

- [ ] **Step 3.2: Verify lint + build**

Run: `cd web && npm run lint && npm run build`
Expected: both exit 0.

- [ ] **Step 3.3: Commit**

```bash
git add web/components.json web/package.json web/package-lock.json web/src/components/ui/
git commit -m "Install shadcn input, label, dropdown-menu for Phase 5"
```

---

## Task 4: Build `<PrinterFormDialog/>`

**Files:**
- Create: `web/src/components/settings/printer-form-dialog.tsx`

The Add/Edit dialog. Five fields: name (optional), serial (required), IP (required), access code (required for new printers; optional for edit — empty means "don't change"), machine model (optional).

A tiny inline validator returns `{valid: boolean, errors: Record<string, string>}` so we don't pull in `react-hook-form` + `zod` for five fields. Submit fires either `createPrinterConfig` or `updatePrinterConfig` via `useMutation` and closes the dialog on success.

The Machine Model select uses the slicer machines query (already loaded by Phase 4 in some cases; here it loads on first dialog open via React Query's lazy fetching).

- [ ] **Step 4.1: Create `web/src/components/settings/printer-form-dialog.tsx`**

```typescript
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  createPrinterConfig,
  updatePrinterConfig,
} from '@/lib/api/printer-configs';
import { getSlicerMachines } from '@/lib/api/slicer-profiles';
import type { PrinterConfigInput, PrinterConfigResponse } from '@/lib/api/types';

export type PrinterFormMode =
  | { kind: 'add' }
  | { kind: 'edit'; printer: PrinterConfigResponse };

interface FormState {
  name: string;
  serial: string;
  ip: string;
  access_code: string;
  machine_model: string;
}

interface FieldErrors {
  serial?: string;
  ip?: string;
  access_code?: string;
}

function validate(state: FormState, isEdit: boolean): FieldErrors {
  const errors: FieldErrors = {};
  if (!state.serial.trim()) errors.serial = 'Required';
  if (!state.ip.trim()) errors.ip = 'Required';
  // Access code is required only when adding; empty on edit means "keep existing".
  if (!isEdit && !state.access_code.trim()) errors.access_code = 'Required';
  return errors;
}

export function PrinterFormDialog({
  mode,
  open,
  onClose,
}: {
  mode: PrinterFormMode | null;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const isEdit = mode?.kind === 'edit';

  const [state, setState] = useState<FormState>({
    name: '',
    serial: '',
    ip: '',
    access_code: '',
    machine_model: '',
  });
  const [touched, setTouched] = useState<Record<string, boolean>>({});

  // Reset form whenever the mode changes (open/close cycle).
  useEffect(() => {
    if (mode?.kind === 'edit') {
      setState({
        name: mode.printer.name,
        serial: mode.printer.serial,
        ip: mode.printer.ip,
        access_code: '',
        machine_model: mode.printer.machine_model,
      });
    } else if (mode?.kind === 'add') {
      setState({ name: '', serial: '', ip: '', access_code: '', machine_model: '' });
    }
    setTouched({});
  }, [mode]);

  const machinesQuery = useQuery({
    queryKey: ['slicer', 'machines'],
    queryFn: getSlicerMachines,
    staleTime: Infinity,
    enabled: open,
  });

  const machineOptions = useMemo(
    () =>
      (machinesQuery.data ?? [])
        .filter((m) => m.setting_id)
        .map((m) => ({ value: m.setting_id, label: m.name })),
    [machinesQuery.data],
  );

  const errors = validate(state, isEdit);
  const hasErrors = Object.keys(errors).length > 0;

  const submit = useMutation({
    mutationFn: async () => {
      const input: PrinterConfigInput = {
        serial: state.serial.trim(),
        ip: state.ip.trim(),
        access_code: state.access_code.trim(),
        name: state.name.trim(),
        machine_model: state.machine_model,
      };
      if (mode?.kind === 'edit') {
        return updatePrinterConfig(mode.printer.serial, input);
      }
      return createPrinterConfig(input);
    },
    onSuccess: () => {
      toast.success(isEdit ? 'Printer updated' : 'Printer added');
      queryClient.invalidateQueries({ queryKey: ['printer-configs'] });
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      onClose();
    },
    onError: (err: Error) => {
      toast.error(`${isEdit ? 'Update' : 'Add'} failed: ${err.message}`);
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setTouched({ serial: true, ip: true, access_code: true });
    if (hasErrors) return;
    submit.mutate();
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
    >
      <DialogContent className="bg-bg-1 border-border text-text-0 max-w-md">
        <DialogHeader>
          <DialogTitle className="text-white">
            {isEdit ? 'Edit Printer' : 'Add Printer'}
          </DialogTitle>
          <DialogDescription className="text-text-1">
            {isEdit
              ? 'Update connection details. Leave Access Code blank to keep the current value.'
              : 'Enter the printer connection details from Bambu Studio or the printer LCD.'}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <Field
            label="Name"
            value={state.name}
            onChange={(name) => setState((s) => ({ ...s, name }))}
            placeholder="Living Room A1 Mini"
          />
          <Field
            label="Serial"
            value={state.serial}
            onChange={(serial) => setState((s) => ({ ...s, serial }))}
            error={touched.serial ? errors.serial : undefined}
            disabled={isEdit}
            required
          />
          <Field
            label="IP Address"
            value={state.ip}
            onChange={(ip) => setState((s) => ({ ...s, ip }))}
            error={touched.ip ? errors.ip : undefined}
            placeholder="192.168.1.42"
            required
          />
          <Field
            label="Access Code"
            value={state.access_code}
            onChange={(access_code) => setState((s) => ({ ...s, access_code }))}
            error={touched.access_code ? errors.access_code : undefined}
            type="password"
            placeholder={isEdit ? 'Leave blank to keep current' : '8 digits'}
            required={!isEdit}
          />
          <div className="flex flex-col gap-1">
            <Label htmlFor="machine_model" className="text-xs text-text-1">
              Machine Model
            </Label>
            <Select
              value={state.machine_model || '__none__'}
              onValueChange={(v) =>
                setState((s) => ({ ...s, machine_model: v === '__none__' ? '' : v }))
              }
            >
              <SelectTrigger id="machine_model" className="bg-bg-0 border-border">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">None (no filament filtering)</SelectItem>
                {machineOptions.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <DialogFooter className="mt-2 gap-2">
            <Button
              type="button"
              variant="ghost"
              onClick={onClose}
              disabled={submit.isPending}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={submit.isPending || hasErrors}
              className="bg-gradient-to-r from-accent-strong to-accent text-white border-0"
            >
              {submit.isPending ? 'Saving…' : isEdit ? 'Save changes' : 'Add Printer'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  value,
  onChange,
  error,
  type = 'text',
  placeholder,
  disabled = false,
  required = false,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  error?: string;
  type?: string;
  placeholder?: string;
  disabled?: boolean;
  required?: boolean;
}) {
  const id = `field-${label.toLowerCase().replace(/\s+/g, '-')}`;
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id} className="text-xs text-text-1">
        {label}
        {required && <span className="text-danger ml-0.5">*</span>}
      </Label>
      <Input
        id={id}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        className="bg-bg-0 border-border text-text-0"
      />
      {error && <span className="text-[11px] text-danger">{error}</span>}
    </div>
  );
}
```

- [ ] **Step 4.2: Type-check + build**

Run: `cd web && npm run lint && npm run build`
Expected: both exit 0.

- [ ] **Step 4.3: Commit**

```bash
git add web/src/components/settings/printer-form-dialog.tsx
git commit -m "Add PrinterFormDialog for Add/Edit Printer in Settings"
```

---

## Task 5: Build `<PrinterRow/>`

**Files:**
- Create: `web/src/components/settings/printer-row.tsx`

A row in the Printers list. Reuses `<TrayRow/>` from Phase 2 for visual consistency, but the dot color comes from the printer's live status (matching `<PrinterPicker/>`'s dot logic). The right side has a `<DropdownMenu/>` with Edit / Delete items.

Status data comes from the live `/api/printers` query so a paused printer in the Settings list shows amber etc.

- [ ] **Step 5.1: Create `web/src/components/settings/printer-row.tsx`**

```typescript
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { MoreHorizontal } from 'lucide-react';
import { toast } from 'sonner';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { TrayRow } from '@/components/tray-row';
import { deletePrinterConfig } from '@/lib/api/printer-configs';
import type { PrinterConfigResponse, PrinterStatus } from '@/lib/api/types';

function dotColor(status: PrinterStatus | undefined): string | null {
  if (!status || !status.online) return '#6B7280'; // text-2 / offline
  switch (status.state) {
    case 'printing':
    case 'preparing':
      return '#60A5FA'; // accent
    case 'paused':
      return '#FBBF24'; // warm
    case 'error':
      return '#EF4444'; // danger
    default:
      return '#22C55E'; // success
  }
}

export function PrinterRow({
  printer,
  liveStatus,
  onEdit,
}: {
  printer: PrinterConfigResponse;
  /** Live status from /api/printers; may be undefined while loading. */
  liveStatus: PrinterStatus | undefined;
  onEdit: () => void;
}) {
  const queryClient = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const remove = useMutation({
    mutationFn: () => deletePrinterConfig(printer.serial),
    onSuccess: () => {
      toast.success(`${printer.name || printer.serial} removed`);
      queryClient.invalidateQueries({ queryKey: ['printer-configs'] });
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      setConfirmOpen(false);
    },
    onError: (err: Error) => {
      toast.error(`Remove failed: ${err.message}`);
    },
  });

  const subtitle = `${printer.serial} · ${printer.ip}`;
  const body = printer.machine_model || 'No machine model set';

  return (
    <>
      <TrayRow
        colorDot={dotColor(liveStatus)}
        title={printer.name || `Printer ${printer.serial.slice(-4)}`}
        subtitle={subtitle}
        body={body}
        right={
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                className="h-8 w-8 p-0 text-text-1 hover:text-white"
                aria-label={`Actions for ${printer.name || printer.serial}`}
              >
                <MoreHorizontal className="w-4 h-4" aria-hidden />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="bg-bg-1 border-border">
              <DropdownMenuItem onSelect={onEdit}>Edit</DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => setConfirmOpen(true)}
                className="text-danger focus:text-danger focus:bg-danger/10"
              >
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        }
      />
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Remove {printer.name || printer.serial}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              The MQTT connection will be dropped and the printer disappears from
              the Dashboard. The printer itself isn't affected.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>Keep</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                remove.mutate();
              }}
              disabled={remove.isPending}
              className="bg-danger text-white hover:bg-danger/90"
            >
              {remove.isPending ? 'Removing…' : 'Remove'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
```

- [ ] **Step 5.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 5.3: Commit**

```bash
git add web/src/components/settings/printer-row.tsx
git commit -m "Add PrinterRow for Settings list (status dot + overflow menu)"
```

---

## Task 6: Build `<PrintersSection/>`

**Files:**
- Create: `web/src/components/settings/printers-section.tsx`

The Card wrapper around the Printers list. Holds the list, the Add button, and the dialog state (which printer — if any — is being edited).

- [ ] **Step 6.1: Create `web/src/components/settings/printers-section.tsx`**

```typescript
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { PrinterRow } from '@/components/settings/printer-row';
import {
  PrinterFormDialog,
  type PrinterFormMode,
} from '@/components/settings/printer-form-dialog';
import { listPrinterConfigs } from '@/lib/api/printer-configs';
import { listPrinters } from '@/lib/api/printers';
import type { PrinterStatus } from '@/lib/api/types';

export function PrintersSection() {
  const [mode, setMode] = useState<PrinterFormMode | null>(null);

  const configsQuery = useQuery({
    queryKey: ['printer-configs'],
    queryFn: listPrinterConfigs,
    staleTime: 30_000,
  });

  // Live statuses for the per-row status dot.
  const printersQuery = useQuery({
    queryKey: ['printers'],
    queryFn: listPrinters,
    refetchInterval: 4_000,
  });
  const statusBySerial = useMemo(() => {
    const map = new Map<string, PrinterStatus>();
    for (const p of printersQuery.data?.printers ?? []) map.set(p.id, p);
    return map;
  }, [printersQuery.data]);

  const printers = configsQuery.data?.printers ?? [];

  return (
    <section className="flex flex-col gap-2">
      <header className="flex items-center justify-between px-1">
        <h2 className="text-base font-semibold text-white">Printers</h2>
        <Button
          type="button"
          onClick={() => setMode({ kind: 'add' })}
          className="rounded-full h-8 px-3 bg-surface-1 hover:bg-surface-2 text-accent border-0 text-[13px] font-semibold"
        >
          <Plus className="w-3.5 h-3.5 mr-1" aria-hidden /> Add Printer
        </Button>
      </header>
      <Card className="bg-card border-border p-2 flex flex-col gap-1.5">
        {configsQuery.isLoading ? (
          <Skeleton className="h-14 rounded-2xl" />
        ) : printers.length === 0 ? (
          <p className="text-sm text-text-1 px-3 py-4">
            No printers configured yet. Add one to start monitoring.
          </p>
        ) : (
          printers.map((printer) => (
            <PrinterRow
              key={printer.serial}
              printer={printer}
              liveStatus={statusBySerial.get(printer.serial)}
              onEdit={() => setMode({ kind: 'edit', printer })}
            />
          ))
        )}
      </Card>
      <PrinterFormDialog
        mode={mode}
        open={mode !== null}
        onClose={() => setMode(null)}
      />
    </section>
  );
}
```

- [ ] **Step 6.2: Type-check + build**

Run: `cd web && npm run lint && npm run build`
Expected: both exit 0.

- [ ] **Step 6.3: Commit**

```bash
git add web/src/components/settings/printers-section.tsx
git commit -m "Add PrintersSection wiring Add/Edit/Delete printer config flow"
```

---

## Task 7: Build `<PushDeviceRow/>` and `<PushSection/>`

**Files:**
- Create: `web/src/components/settings/push-device-row.tsx`, `web/src/components/settings/push-section.tsx`

Push Notifications section — status line (push enabled / disabled) + list of registered devices, each with Test push and Delete actions. When push is disabled, the section shows a muted message and skips the device list.

- [ ] **Step 7.1: Create `web/src/components/settings/push-device-row.tsx`**

```typescript
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { MoreHorizontal } from 'lucide-react';
import { toast } from 'sonner';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { TrayRow } from '@/components/tray-row';
import { deleteDevice, sendTestPush } from '@/lib/api/devices';
import type { DeviceInfo } from '@/lib/api/types';

export function PushDeviceRow({ device }: { device: DeviceInfo }) {
  const queryClient = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const test = useMutation({
    mutationFn: () => sendTestPush(device.id),
    onSuccess: () => toast.success(`Test push sent to ${device.name || device.id}`),
    onError: (err: Error) => toast.error(`Test push failed: ${err.message}`),
  });

  const remove = useMutation({
    mutationFn: () => deleteDevice(device.id),
    onSuccess: () => {
      toast.success(`${device.name || device.id} removed`);
      queryClient.invalidateQueries({ queryKey: ['devices'] });
      setConfirmOpen(false);
    },
    onError: (err: Error) => toast.error(`Remove failed: ${err.message}`),
  });

  const tokens: string[] = [];
  if (device.has_device_token) tokens.push('alert');
  if (device.has_live_activity_start_token) tokens.push('live activity');
  if (device.active_activity_count > 0) {
    tokens.push(
      `${device.active_activity_count} activity${device.active_activity_count === 1 ? '' : 'ies'}`,
    );
  }
  const subtitle = device.id;
  const body = tokens.length > 0 ? tokens.join(' · ') : 'no tokens';

  return (
    <>
      <TrayRow
        // Devices don't carry a color — show a muted dot to keep alignment.
        colorDot="#6B7280"
        title={device.name || 'Unnamed device'}
        subtitle={subtitle}
        body={body}
        right={
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                className="h-8 w-8 p-0 text-text-1 hover:text-white"
                aria-label={`Actions for ${device.name || device.id}`}
              >
                <MoreHorizontal className="w-4 h-4" aria-hidden />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="bg-bg-1 border-border">
              <DropdownMenuItem
                onSelect={() => test.mutate()}
                disabled={test.isPending || !device.has_device_token}
              >
                Send test push
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => setConfirmOpen(true)}
                className="text-danger focus:text-danger focus:bg-danger/10"
              >
                Remove
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        }
      />
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this device?</AlertDialogTitle>
            <AlertDialogDescription>
              {device.name || device.id} will stop receiving push notifications
              until it re-registers (typically on next iOS app launch).
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>Keep</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                remove.mutate();
              }}
              disabled={remove.isPending}
              className="bg-danger text-white hover:bg-danger/90"
            >
              {remove.isPending ? 'Removing…' : 'Remove'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
```

- [ ] **Step 7.2: Create `web/src/components/settings/push-section.tsx`**

```typescript
import { useQuery } from '@tanstack/react-query';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { PushDeviceRow } from '@/components/settings/push-device-row';
import { listDevices } from '@/lib/api/devices';
import { getCapabilities } from '@/lib/api/capabilities';

export function PushSection() {
  const capsQuery = useQuery({
    queryKey: ['capabilities'],
    queryFn: getCapabilities,
    staleTime: 60_000,
  });

  const devicesQuery = useQuery({
    queryKey: ['devices'],
    queryFn: listDevices,
    enabled: capsQuery.data?.push === true,
  });

  const pushEnabled = capsQuery.data?.push === true;
  const devices = devicesQuery.data?.devices ?? [];

  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-base font-semibold text-white px-1">Push Notifications</h2>
      <Card className="bg-card border-border p-4 flex flex-col gap-3">
        {capsQuery.isLoading ? (
          <Skeleton className="h-5 w-2/3" />
        ) : !pushEnabled ? (
          <p className="text-sm text-text-1">
            Push is <span className="text-text-0 font-semibold">disabled</span>. Configure
            APNs credentials in the gateway environment to enable — see{' '}
            <a
              href="https://github.com/leolobato/bambu-gateway/blob/main/docs/APNS.md"
              target="_blank"
              rel="noreferrer"
              className="text-accent hover:underline"
            >
              docs/APNS.md
            </a>
            .
          </p>
        ) : (
          <>
            <p className="text-sm text-text-1">
              Push is <span className="text-text-0 font-semibold">enabled</span>. Devices
              register automatically when the iOS app launches and notifications are
              allowed.
            </p>
            {devicesQuery.isLoading ? (
              <Skeleton className="h-14 rounded-2xl" />
            ) : devices.length === 0 ? (
              <p className="text-sm text-text-2">No devices registered yet.</p>
            ) : (
              <div className="flex flex-col gap-1.5">
                {devices.map((d) => (
                  <PushDeviceRow key={d.id} device={d} />
                ))}
              </div>
            )}
          </>
        )}
      </Card>
    </section>
  );
}
```

- [ ] **Step 7.3: Type-check + build**

Run: `cd web && npm run lint && npm run build`
Expected: both exit 0.

- [ ] **Step 7.4: Commit**

```bash
git add web/src/components/settings/push-device-row.tsx web/src/components/settings/push-section.tsx
git commit -m "Add Push Notifications section with device list, test, delete"
```

---

## Task 8: Build `<AboutSection/>`

**Files:**
- Create: `web/src/components/settings/about-section.tsx`

Static-ish card with the gateway version (from `/api/capabilities`) and a link to the source repo. Connection-test buttons are deferred (see plan header).

- [ ] **Step 8.1: Create `web/src/components/settings/about-section.tsx`**

```typescript
import { useQuery } from '@tanstack/react-query';
import { ExternalLink } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { getCapabilities } from '@/lib/api/capabilities';

export function AboutSection() {
  const capsQuery = useQuery({
    queryKey: ['capabilities'],
    queryFn: getCapabilities,
    staleTime: 60_000,
  });

  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-base font-semibold text-white px-1">About</h2>
      <Card className="bg-card border-border p-4 flex flex-col gap-3">
        <div className="flex items-baseline justify-between">
          <span className="text-sm text-text-1">Bambu Gateway</span>
          {capsQuery.isLoading ? (
            <Skeleton className="h-4 w-12" />
          ) : (
            <span className="text-sm text-text-0 font-mono tabular-nums">
              v{capsQuery.data?.version || '?'}
            </span>
          )}
        </div>
        <a
          href="https://github.com/leolobato/bambu-gateway"
          target="_blank"
          rel="noreferrer"
          className="text-sm text-accent hover:underline inline-flex items-center gap-1"
        >
          Source code
          <ExternalLink className="w-3.5 h-3.5" aria-hidden />
        </a>
      </Card>
    </section>
  );
}
```

- [ ] **Step 8.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 8.3: Commit**

```bash
git add web/src/components/settings/about-section.tsx
git commit -m "Add About section showing gateway version and source link"
```

---

## Task 9: Wire the Settings route

**Files:**
- Modify: `web/src/routes/settings.tsx`

Replace the placeholder with the three sections.

- [ ] **Step 9.1: Replace `web/src/routes/settings.tsx`**

```typescript
import { PrintersSection } from '@/components/settings/printers-section';
import { PushSection } from '@/components/settings/push-section';
import { AboutSection } from '@/components/settings/about-section';

export default function SettingsRoute() {
  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-[28px] font-extrabold tracking-tight text-white">Settings</h1>
      </header>
      <PrintersSection />
      <PushSection />
      <AboutSection />
    </div>
  );
}
```

- [ ] **Step 9.2: Type-check + build**

Run: `cd web && npm run lint && npm run build`
Expected: both exit 0.

- [ ] **Step 9.3: Commit**

```bash
git add web/src/routes/settings.tsx
git commit -m "Wire Settings route with Printers, Push, About sections"
```

---

## Task 10: Manual smoke-test

**Files:** none modified.

- [ ] **Step 10.1: Refresh `http://localhost:4844/beta/settings`**

Build is current from Task 9. Open the route.

- [ ] **Step 10.2: Walk the three sections**

DevTools → Console + Network. Walk through:

1. **Printers section:**
   - List shows your existing printers (probably one A1 Mini). Each row has the live status dot (green for idle, blue for printing).
   - Click `Add Printer`. The dialog opens. Required-field markers (red asterisks) on Serial / IP / Access Code.
   - Try submitting empty → inline `Required` errors appear, no network call.
   - Fill in a fake printer (e.g. serial `TEST123`, ip `1.2.3.4`, access code `00000000`). Submit → toast "Printer added", dialog closes, the new row appears.
   - Click the `…` overflow on the new row → Edit. Dialog opens with the existing values. Serial input is disabled. Access Code placeholder reads "Leave blank to keep current". Change the IP to `1.2.3.5`. Submit → toast "Printer updated".
   - Click `…` → Delete on the test printer. AlertDialog asks for confirmation. Click `Remove` → toast, row disappears.
2. **Push Notifications section:**
   - If push is disabled (your default — `APNS_KEY_PATH` not set): copy reads "Push is disabled" with link to docs/APNS.md.
   - If push is enabled: copy reads "Push is enabled" + list of registered devices. Each row has Test push / Remove actions in the overflow menu.
3. **About section:**
   - Shows "Bambu Gateway v1.5.0" (or whatever `app.version` is) on the right, "Source code" link below.
4. **Back navigation:**
   - Header `⚙ Settings` pill is highlighted; clicking `Dashboard` or `Print` navigates away cleanly.
5. **Console:** clean — no React warnings, no failed network calls.

If any step fails, **do not commit Task 11** — fix the relevant component task first.

---

## Task 11: README + final audit

**Files:**
- Modify: `README.md`

- [ ] **Step 11.1: Update `README.md`**

Find the Phase 4 line under the `Frontend` section. Append below it:

```markdown
- **Phase 5:** `/beta/settings` is now the redesigned Settings page — Printers list with live status dots, Add/Edit/Delete via shadcn Dialog + inline-validated form, Push Notifications device list with Test push + Remove actions, and an About card with the gateway version. Connection-test buttons (MQTT/FTPS) are deferred — backend endpoints don't exist yet.
```

- [ ] **Step 11.2: Commit**

```bash
git add README.md
git commit -m "README: document Phase 5 Settings redesign at /beta/settings"
```

- [ ] **Step 11.3: Final audit checklist**

- [ ] `cd web && npm run lint` exits 0.
- [ ] `cd web && npm run build` exits 0.
- [ ] `.venv/bin/pytest` exits 0 (was 104; now 105 with the new capabilities-version test).
- [ ] `git status` is clean.
- [ ] All 11 tasks above committed (`git log --oneline` shows ~12 new commits since the start of Phase 5).
- [ ] Old Jinja UI at `/settings` renders unchanged (`curl -s http://localhost:4844/settings | grep '<title>'` still returns the Jinja settings title).
- [ ] Only `app/main.py` and `app/models.py` are modified in `app/` (`git diff <phase5-base>..HEAD -- app/` shows the version field + the new test file only).

If all items check out, Phase 5 is complete. Phase 6 (Cutover) is next: move `/beta/*` → `/*`, delete `app/templates/index.html`, `app/templates/settings.html`, `app/static/style.css`, and the Jinja routes in `app/main.py`.
