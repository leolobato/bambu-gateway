# bambu-gateway-mcp — MCP server for agent-driven prints

**Status:** approved (brainstorm, design level) · **Date:** 2026-06-28 · **Sub-project B of 2**

> Depends on sub-project A (`2026-06-28-print-sessions-gateway-design.md`). Build A first;
> B is a thin client over A's HTTP contract. This spec captures the agreed design; B gets
> its own implementation-plan cycle once A's contract is final. The repo lives at
> `../bambu-gateway-mcp` (new).

## Goal

Expose the gateway's print-session capability to an LLM agent as MCP tools, so
the agent can: discover the user's printers and loaded AMS filament, configure
a print from a model, hand back a URL, and — with explicit in-chat approval —
start the print.

## Principles

- **Thin wrapper.** No slicing, no profile logic, no model parsing. Every tool
  is a call to a gateway endpoint. All domain logic stays in the gateway.
- **Human-in-the-loop for printing.** Starting a print is the only action with
  physical consequences; it is gated by MCP **elicitation** (an explicit
  approval prompt in the agent conversation). Nothing else requires approval.
- **No silent prints.** `start_print` never proceeds without an affirmative
  elicitation response.

## Stack

Python MCP server (official `mcp` SDK or `fastmcp`), packaged as a standalone
repo at `../bambu-gateway-mcp`. Configured with `GATEWAY_BASE_URL` and a
`GATEWAY_TOKEN` (the credential the gateway's `/print` action authenticates).
Talks to the gateway with `httpx`.

## Tools

- `list_printers()` → printers with `id`, `name`, `machine_model`,
  `default_plate_type`. (GET `/api/printers` / settings.)
- `get_ams(printer_id)` → loaded AMS trays/filaments per slot, so the agent can
  choose `tray_slot`. (GET `/api/ams`.)
- `list_filaments()` / `list_processes()` (optional) → slicer profiles for
  explicit overrides. (GET `/api/slicer/...`.)
- `create_print_session(model, printer_id, slice=false, filament_selections=None, plate_type=None, process_overrides=None, filament_overrides=None, ...)`
  → `{ job_id, handoff_url, sliced }`. Wraps `POST /api/print-sessions`. The
  `model` is a local file path the server reads and uploads (`.3mf` or `.stl`).
- `get_print_session(session_id)` → status + `sliced` + `handoff_url`. Wraps
  GET `/api/print-sessions/{id}`. Lets the agent wait for a `slice:true`
  session to finish before offering to print.
- `start_print(session_id)` → **elicits explicit user approval in chat**; on
  yes, calls `POST /api/print-sessions/{id}/print`; on no, returns without
  printing. The elicitation message names the printer, model, and key settings
  so the user approves a concrete action.

There is intentionally **no** tool that both configures and prints in one step.

## Typical agent flow

1. `list_printers()` → pick the target.
2. (optional) `get_ams(printer_id)` → choose filament/tray per slot.
3. `create_print_session(model, printer_id, slice=true, …)` → get
   `handoff_url`; share it with the user ("open this to review/tweak").
4. (optional) `start_print(session_id)` → user approves in chat → print starts.

## Testing

- Tool-level tests with a mocked gateway HTTP layer: each tool maps to the
  right endpoint with the right payload; `create_print_session` uploads the
  file; `start_print` calls `/print` only after an affirmative elicitation and
  not after a negative one.
- A smoke test against a running gateway (manual / opt-in).

## Out of scope

- Any slicing/profile/model logic (lives in the gateway).
- Gateway-side cryptographic approval verification (the gateway's trust model
  is documented in sub-project A; B's elicitation is the v1 approval gate).
