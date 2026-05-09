"""HTTP client for the OrcaSlicer CLI API."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from app.models import PrintEstimate

logger = logging.getLogger(__name__)


class SlicingError(Exception):
    """Raised when the slicer API returns a non-200 response or is unreachable."""


@dataclass
class SliceResult:
    """Result from a slice request, including content and settings transfer info."""

    content: bytes
    settings_transfer_status: str = ""
    settings_transferred: list[dict] = field(default_factory=list)
    filament_transfers: list[dict] = field(default_factory=list)
    estimate: PrintEstimate | None = None
    process_overrides_applied: list[dict] = field(default_factory=list)


def translate_estimate_from_binary(payload: dict | None) -> dict | None:
    """Map orca-headless's estimate dict onto `PrintEstimate`-shaped keys.

    The C++ binary returns ``{time_seconds, prepare_seconds, weight_g,
    model_weight_g, filament_used_m: list[float],
    model_filament_used_m: list[float]}``. The gateway's `PrintEstimate`
    model and the iOS UI expect GUI-style keys
    (`total_filament_millimeters`, `total_filament_grams`, `total_seconds`,
    `model_filament_*`, `prepare_seconds`, `model_print_seconds`). Without
    this translation `PrintEstimate(**raw)` ignores the unknown keys and
    every field is `None`, so `is_empty` strips the estimate from the API
    response.
    """
    if not payload:
        return None
    out: dict[str, Any] = {}

    fum = payload.get("filament_used_m")
    if isinstance(fum, list) and fum:
        try:
            out["total_filament_millimeters"] = sum(float(v) for v in fum) * 1000.0
        except (TypeError, ValueError):
            pass
    model_fum = payload.get("model_filament_used_m")
    if isinstance(model_fum, list) and model_fum:
        try:
            out["model_filament_millimeters"] = sum(float(v) for v in model_fum) * 1000.0
        except (TypeError, ValueError):
            pass

    weight = payload.get("weight_g")
    if isinstance(weight, (int, float)):
        out["total_filament_grams"] = float(weight)
    model_weight = payload.get("model_weight_g")
    if isinstance(model_weight, (int, float)):
        out["model_filament_grams"] = float(model_weight)

    secs = payload.get("time_seconds")
    prepare = payload.get("prepare_seconds")
    if isinstance(secs, (int, float)):
        out["total_seconds"] = int(secs)
    if isinstance(prepare, (int, float)):
        out["prepare_seconds"] = int(prepare)
    # The "printing" / "model_print" time = total - prepare. The binary
    # doesn't expose it directly because it's redundant; compute here so
    # the UI's three-row breakdown (Prepare / Printing / Total) populates.
    if isinstance(secs, (int, float)) and isinstance(prepare, (int, float)):
        out["model_print_seconds"] = max(0, int(secs) - int(prepare))

    return out or None


def _decode_print_estimate_dict(payload: dict | None) -> PrintEstimate | None:
    translated = translate_estimate_from_binary(payload)
    if not translated:
        return None
    try:
        estimate = PrintEstimate(**translated)
    except (TypeError, ValueError):
        logger.warning("Failed to parse slice estimate payload: %r", payload)
        return None
    return None if estimate.is_empty else estimate


def _slice_result_from_v2(payload: dict, sliced_bytes: bytes) -> "SliceResult":
    """Map a /slice/v2 JSON response onto the gateway's SliceResult shape.

    The v2 ``settings_transfer`` schema is:
    ``{status, process_keys, printer_keys, filament_slots, curr_bed_type?,
    process_overrides_applied?}``. ``process_keys``/``printer_keys`` are
    lists of key names (not key+value+original triples like the legacy
    headers). The gateway's ``TransferredSetting`` model expects the legacy
    shape, so we only surface ``status`` and ``filament_slots`` here —
    keys-only lists land in the logs but aren't yet wired into the response
    model. ``process_overrides_applied`` (rev 41+) carries the
    ``[{key, value, previous}, ...]`` list of overrides the slicer
    actually applied.
    """
    transfer = payload.get("settings_transfer") or {}
    status = str(transfer.get("status", "") or "")
    filament_slots = transfer.get("filament_slots") or []
    overrides_applied = transfer.get("process_overrides_applied") or []
    return SliceResult(
        content=sliced_bytes,
        settings_transfer_status=status,
        settings_transferred=[],
        filament_transfers=filament_slots if isinstance(filament_slots, list) else [],
        estimate=_decode_print_estimate_dict(payload.get("estimate")),
        process_overrides_applied=(
            list(overrides_applied) if isinstance(overrides_applied, list) else []
        ),
    )


class SlicerClient:
    """Thin wrapper around the OrcaSlicer CLI API."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._has_stream: bool | None = None  # None = unknown, check on first call

    async def slice(
        self,
        file_data: bytes,
        filename: str,
        machine_profile: str,
        process_profile: str,
        filament_profiles: list[str] | dict[str, Any],
        plate_type: str = "",
        plate: int = 1,
        process_overrides: dict[str, str] | None = None,
    ) -> SliceResult:
        """Slice a 3MF via orcaslicer-cli's token-based v2 API.

        Uploads the bytes (sha256-deduped, so a re-upload of a file already
        cached by ``parse_3mf_via_slicer`` is free server-side), posts the
        slice request as JSON, and downloads the sliced output.

        ``process_overrides`` is forwarded verbatim into the slice body
        when non-empty. The slicer is permissive — unknown / filament-
        domain / unparseable keys are silently dropped server-side; the
        slicer reports back what was actually applied via
        ``settings_transfer.process_overrides_applied``.
        """
        upload = await self.upload_3mf(file_data, filename=filename)
        input_token = upload["token"]

        body = await self._build_v2_slice_body(
            input_token=input_token,
            machine_profile=machine_profile,
            process_profile=process_profile,
            filament_profiles=filament_profiles,
            plate=plate,
            plate_type=plate_type,
            process_overrides=process_overrides,
        )

        url = f"{self._base_url}/slice/v2"
        logger.info("Sending %s to slicer at %s (token=%s)", filename, url, input_token)
        try:
            async with httpx.AsyncClient(timeout=300, transport=self._transport) as client:
                resp = await client.post(url, json=body)
        except httpx.HTTPError as e:
            raise SlicingError(f"Slicer unreachable: {e}")

        if resp.status_code != 200:
            raise SlicingError(f"Slicer returned {resp.status_code}: {resp.text[:500]}")

        payload = resp.json()
        sliced = await self._download_3mf(payload["output_token"])
        return _slice_result_from_v2(payload, sliced)

    async def _check_stream_support(self) -> bool:
        """Probe the slicer to see if /slice-stream/v2 exists."""
        if self._has_stream is not None:
            return self._has_stream
        try:
            async with httpx.AsyncClient(timeout=5, transport=self._transport) as client:
                resp = await client.options(f"{self._base_url}/slice-stream/v2")
                self._has_stream = resp.status_code != 404
        except httpx.HTTPError:
            self._has_stream = False
        logger.info("Slicer stream support: %s", self._has_stream)
        return self._has_stream

    async def slice_stream(
        self,
        file_data: bytes,
        filename: str,
        machine_profile: str,
        process_profile: str,
        filament_profiles: list[str] | dict[str, Any],
        plate_type: str = "",
        plate: int = 1,
        process_overrides: dict[str, str] | None = None,
    ):
        """Stream SSE events for a slice operation.

        Uses /slice-stream/v2 if available, otherwise falls back to /slice/v2
        and emits synthetic SSE events.
        Yields dicts with 'event' and 'data' keys.
        """
        if await self._check_stream_support():
            async for event in self._slice_stream_real(
                file_data, filename, machine_profile, process_profile, filament_profiles,
                plate, plate_type, process_overrides,
            ):
                yield event
        else:
            async for event in self._slice_stream_fallback(
                file_data, filename, machine_profile, process_profile, filament_profiles,
                plate, plate_type, process_overrides,
            ):
                yield event

    async def _slice_stream_real(
        self, file_data, filename, machine_profile, process_profile, filament_profiles,
        plate=1, plate_type="", process_overrides=None,
    ):
        upload = await self.upload_3mf(file_data, filename=filename)
        input_token = upload["token"]
        body = await self._build_v2_slice_body(
            input_token=input_token,
            machine_profile=machine_profile,
            process_profile=process_profile,
            filament_profiles=filament_profiles,
            plate=plate,
            plate_type=plate_type,
            process_overrides=process_overrides,
        )

        url = f"{self._base_url}/slice-stream/v2"
        logger.info("Streaming slice of %s via %s (token=%s)", filename, url, input_token)
        timeout = httpx.Timeout(connect=10, read=300, write=60, pool=10)
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            async with client.stream("POST", url, json=body) as resp:
                if resp.status_code != 200:
                    raw = await resp.aread()
                    raise SlicingError(
                        f"Slicer returned {resp.status_code}: {raw.decode()[:500]}"
                    )

                event_type = None
                data_lines: list[str] = []
                async for line in resp.aiter_lines():
                    if line.startswith("event: "):
                        event_type = line[7:]
                    elif line.startswith("data: "):
                        data_lines.append(line[6:])
                    elif line == "":
                        if event_type and data_lines:
                            try:
                                payload = json.loads("".join(data_lines))
                            except json.JSONDecodeError:
                                payload = {"raw": "".join(data_lines)}
                            # /slice-stream/v2's `result` event carries
                            # output_token + download_url instead of bytes.
                            # Fetch the bytes and re-emit with file_base64
                            # so the gateway's existing SSE consumer
                            # (slice_jobs._run_job) keeps working unchanged.
                            if event_type == "result" and isinstance(payload, dict):
                                payload = await self._inflate_v2_result(payload)
                            yield {"event": event_type, "data": payload}
                        event_type = None
                        data_lines = []

    async def _slice_stream_fallback(
        self, file_data, filename, machine_profile, process_profile, filament_profiles,
        plate=1, plate_type="", process_overrides=None,
    ):
        """Use the non-streaming /slice/v2 endpoint and emit synthetic SSE events."""
        yield {"event": "status", "data": {"phase": "slicing", "message": "Slicing..."}}

        result = await self.slice(
            file_data, filename, machine_profile, process_profile, filament_profiles,
            plate_type, plate, process_overrides=process_overrides,
        )

        transfer_info = {}
        if result.settings_transfer_status:
            transfer_info["status"] = result.settings_transfer_status
            if result.settings_transferred:
                transfer_info["transferred"] = result.settings_transferred
        if result.filament_transfers:
            transfer_info["filaments"] = result.filament_transfers
        if result.process_overrides_applied:
            transfer_info["process_overrides_applied"] = list(
                result.process_overrides_applied
            )

        yield {"event": "result", "data": {
            "file_base64": base64.b64encode(result.content).decode(),
            "file_size": len(result.content),
            "settings_transfer": transfer_info or None,
            "estimate": result.estimate.model_dump(exclude_none=True)
            if result.estimate else None,
        }}
        yield {"event": "done", "data": {}}

    async def _build_v2_slice_body(
        self,
        *,
        input_token: str,
        machine_profile: str,
        process_profile: str,
        filament_profiles: list[str] | dict[str, Any],
        plate: int,
        plate_type: str = "",
        process_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Translate the gateway's filament_profiles shape to the v2 schema.

        v2 wants ``filament_settings_ids: list[str]`` (positional) plus an
        optional ``filament_map: list[int]`` (per-slot AMS slot index).
        For the dict form, slots not explicitly overridden keep the 3MF's
        authored ``filament_settings_id``; we read that list via
        ``/3mf/{token}/inspect`` so the gateway doesn't need to plumb it
        through every call site.

        ``process_overrides`` is included in the body only when non-empty
        (``None`` and ``{}`` are no-ops per the slicer API contract).
        """
        filament_ids, filament_map = await self._normalize_filament_selection(
            input_token, filament_profiles,
            machine_profile=machine_profile,
        )
        # Headless-only knob. The OrcaSlicer GUI doesn't have an
        # equivalent: when the user retargets to a different printer,
        # the GUI relies on the human visually adjusting placement.
        # Headless has no human-in-the-loop step, so when the target
        # printer differs from the project's authored one, we ask the
        # slicer to auto-center the model on the new bed (delegates to
        # libslic3r's ``Model::center_instances_around_point``, the
        # same primitive the GUI calls on project import).
        # Same-printer retargets keep authored placement.
        auto_center = await self._should_auto_center_for_machine(
            input_token, machine_profile,
        )
        body: dict[str, Any] = {
            "input_token": input_token,
            "machine_id": machine_profile,
            "process_id": process_profile,
            "filament_settings_ids": filament_ids,
            "plate_id": plate or 1,
            "auto_center": auto_center,
        }
        if filament_map is not None:
            body["filament_map"] = filament_map
        if plate_type:
            body["plate_type"] = plate_type
        if process_overrides:
            body["process_overrides"] = dict(process_overrides)
        return body

    async def _normalize_filament_selection(
        self,
        input_token: str,
        filament_profiles: list[str] | dict[str, Any],
        *,
        machine_profile: str | None = None,
    ) -> tuple[list[str], list[int] | None]:
        """Translate filament selection into the slicer's positional shape.

        Returns ``(filament_settings_ids, None)``. ``tray_slot`` values
        present on dict-form selections are intentionally ignored here:
        AMS tray routing is a print-time concern, threaded into the MQTT
        ``project_file`` command via ``build_ams_mapping``. Forwarding
        tray slots to the slicer (the historical behaviour) repurposed
        libslic3r's per-filament-extruder field, which corrupted single-
        extruder slices and silenced the prime-tower auto-disable.

        For dict-form selections the per-slot fill from the 3MF carries
        the project's *authored* filament name. When the requested
        ``machine_profile`` is different from the printer the project
        was authored for, those carry-over names can be incompatible
        (e.g. ``Bambu PLA Basic @BBL P2S`` on an A1 mini target),
        causing the slicer to return ``filament_machine_mismatch``. We
        mirror what the OrcaSlicer GUI's
        ``PresetBundle::update_compatible`` does on machine change:
        substitute incompatible carry-over names with the same-alias
        variant for the target machine via
        ``/profiles/resolve-for-machine`` before sending the slice
        request. User-overridden slots are left exactly as the caller
        specified — the user's explicit pick wins over the resolver,
        matching the GUI's behaviour where the user can override the
        bundle's auto-resolved selection.
        """
        if isinstance(filament_profiles, list):
            return list(filament_profiles), None

        if not isinstance(filament_profiles, dict):
            raise SlicingError(
                f"filament_profiles must be list or dict, got {type(filament_profiles).__name__}",
            )

        # Resolve indexed overrides against the 3MF's authored filament list.
        insp = await self.inspect(input_token)
        base_ids = [
            str(f.get("settings_id", "") or "")
            for f in insp.get("filaments", [])
        ]
        if not base_ids:
            raise SlicingError(
                "filament_profiles dict form requires the input 3MF to declare filament_settings_id",
            )

        filament_ids = list(base_ids)
        overridden_slots: set[int] = set()
        for slot_str, selection in filament_profiles.items():
            try:
                idx = int(slot_str)
            except (TypeError, ValueError):
                raise SlicingError(f"Invalid project filament index: {slot_str!r}")
            if idx < 0 or idx >= len(filament_ids):
                raise SlicingError(
                    f"Project filament index {idx} out of range for "
                    f"{len(filament_ids)} project filament(s)",
                )
            if isinstance(selection, str):
                filament_ids[idx] = selection.strip()
                overridden_slots.add(idx)
            elif isinstance(selection, dict):
                pid = str(selection.get("profile_setting_id", "")).strip()
                if pid:
                    filament_ids[idx] = pid
                    overridden_slots.add(idx)
                # tray_slot is consumed by build_ams_mapping at print time;
                # it MUST NOT leak into the slicer request.
            else:
                raise SlicingError(
                    f"Project filament {idx} selection must be a setting_id string "
                    "or an object with profile_setting_id",
                )

        # GUI parity: rotate carry-over slots to same-alias variants for
        # the target machine. Skip when no machine context is available
        # (older callers); skip overridden slots so the user's explicit
        # pick is never second-guessed.
        if machine_profile and len(overridden_slots) < len(filament_ids):
            filament_ids = await self._resolve_carryover_filaments(
                filament_ids,
                machine_profile=machine_profile,
                overridden_slots=overridden_slots,
            )

        return filament_ids, None

    async def _should_auto_center_for_machine(
        self,
        input_token: str,
        machine_profile: str,
    ) -> bool:
        """Return True when the project was authored for a different printer.

        Headless-only behaviour: the GUI has no equivalent runtime flag
        because a human visually adjusts placement after a printer
        change. For headless retargets, compare the project's authored
        ``printer_settings_id`` (a display name) to the target machine's
        display name and request auto-centering when they differ.
        Best-effort — any upstream error falls through to ``False`` so a
        single bad probe can't break otherwise-valid slices.
        """
        if not machine_profile:
            return False
        try:
            insp = await self.inspect(input_token)
        except SlicingError:
            return False
        authored_name = str(insp.get("printer_settings_id") or "").strip()
        if not authored_name:
            return False
        target_name = await self._machine_display_name(machine_profile)
        if not target_name:
            return False
        return authored_name != target_name

    async def _machine_display_name(self, machine_profile: str) -> str:
        """Fetch the target machine's display name. Empty on any error."""
        url = f"{self._base_url}/profiles/machines/{machine_profile}"
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
                resp = await client.get(url)
        except httpx.HTTPError:
            return ""
        if resp.status_code != 200:
            return ""
        try:
            return str(resp.json().get("name") or "").strip()
        except (ValueError, TypeError):
            return ""

    async def _resolve_carryover_filaments(
        self,
        filament_ids: list[str],
        *,
        machine_profile: str,
        overridden_slots: set[int],
    ) -> list[str]:
        """Run un-overridden carry-over slots through ``/profiles/resolve-for-machine``.

        The resolver returns one entry per requested slot with a ``match``
        reason; ``unchanged`` (already compat) and ``none`` (no compat
        candidate) leave the slot alone, anything else substitutes with
        the resolved name. Mirrors the GUI's update-compatible flow on
        machine change.
        """
        try:
            resolved = await self.resolve_for_machine(
                machine_id=machine_profile,
                filament_names=list(filament_ids),
            )
        except SlicingError as e:
            # Don't fail the slice on a resolver outage — let the slicer
            # itself surface the mismatch with its usual 400. Logging
            # belongs in the caller's slice-job orchestration.
            return filament_ids

        out = list(filament_ids)
        for entry in resolved.get("filaments", []) or []:
            slot = entry.get("slot")
            if not isinstance(slot, int) or slot < 0 or slot >= len(out):
                continue
            if slot in overridden_slots:
                continue
            match = entry.get("match", "")
            if match in ("", "unchanged", "none"):
                # Already compat OR no candidate — leave authored name
                # in place; slicer will accept (compat) or 400 (none),
                # both of which carry clearer signal than a blind swap.
                continue
            resolved_name = (entry.get("name") or "").strip()
            if resolved_name:
                out[slot] = resolved_name
        return out

    async def _download_3mf(self, token: str) -> bytes:
        url = f"{self._base_url}/3mf/{token}"
        try:
            async with httpx.AsyncClient(timeout=120, transport=self._transport) as client:
                r = await client.get(url)
        except httpx.HTTPError as e:
            raise SlicingError(f"Slicer unreachable while fetching {token}: {e}")
        if r.status_code != 200:
            raise SlicingError(
                f"Slicer returned {r.status_code} fetching output {token}: {r.text[:200]}",
            )
        return r.content

    async def _inflate_v2_result(self, payload: dict) -> dict:
        """Add ``file_base64`` to a /slice-stream/v2 ``result`` payload.

        The server emits ``{output_token, download_url, estimate, settings_transfer}``;
        the gateway's existing SSE consumer expects ``file_base64``. Fetch
        the bytes here so the contract stays internal to ``SlicerClient``.
        """
        out_token = payload.get("output_token")
        if not out_token:
            return payload
        sliced = await self._download_3mf(out_token)
        return {
            **payload,
            "file_base64": base64.b64encode(sliced).decode(),
            "file_size": len(sliced),
        }

    async def get_profiles(
        self,
        category: str,
        *,
        machine: str = "",
        ams_assignable: bool | None = None,
    ) -> list[dict]:
        """Fetch profiles of the given category from the slicer.

        category: "machines", "processes", "filaments", or "plate-types"
        machine: optional machine setting_id to filter by (e.g. "GM014")
        ams_assignable: optional filter for filament profiles only.
        """
        url = f"{self._base_url}/profiles/{category}"
        params = {}
        if machine:
            params["machine"] = machine
        if category == "filaments" and ams_assignable is not None:
            params["ams_assignable"] = "true" if ams_assignable else "false"
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
                resp = await client.get(url, params=params)
        except httpx.HTTPError as e:
            logger.error("Failed to fetch %s from slicer: %s", category, e)
            return []

        if resp.status_code != 200:
            logger.error("Slicer /profiles/%s returned %d", category, resp.status_code)
            return []

        return resp.json()

    async def get_process_options(self) -> dict:
        """GET /options/process — process-option metadata catalogue.

        Returns the slicer's JSON response unchanged: ``{version, options}``.
        Raises ``SlicingError`` on non-200 (the 503 ``options_not_loaded``
        case is meaningful — clients retry — so we surface it rather than
        masking it as an empty payload).
        """
        url = f"{self._base_url}/options/process"
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
                resp = await client.get(url)
        except httpx.HTTPError as e:
            raise SlicingError(f"Slicer unreachable: {e}")
        if resp.status_code != 200:
            raise SlicingError(
                f"Slicer returned {resp.status_code}: {resp.text[:500]}",
            )
        return resp.json()

    async def get_process_layout(self) -> dict:
        """GET /options/process/layout — paged + allowlist-filtered layout.

        Returns ``{version, allowlist_revision, pages}`` unchanged. Same
        error semantics as ``get_process_options``.
        """
        url = f"{self._base_url}/options/process/layout"
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
                resp = await client.get(url)
        except httpx.HTTPError as e:
            raise SlicingError(f"Slicer unreachable: {e}")
        if resp.status_code != 200:
            raise SlicingError(
                f"Slicer returned {resp.status_code}: {resp.text[:500]}",
            )
        return resp.json()

    async def get_process_profile(self, setting_id: str) -> dict:
        """GET /profiles/processes/{setting_id} — resolved profile values.

        Returns the slicer's flat ``dict[str, str]`` of every key resolved
        against the named profile. The web client uses this as the
        ``processBaseline`` rung in the effective-value resolver chain.

        Raises ``SlicingError`` on connection failure and on non-2xx
        responses (404 included — caller treats a missing profile as
        "no baseline" and falls back to catalogue defaults).
        """
        url = f"{self._base_url}/profiles/processes/{quote(setting_id, safe='')}"
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
                resp = await client.get(url)
        except httpx.HTTPError as e:
            raise SlicingError(f"Slicer unreachable: {e}")
        if resp.status_code != 200:
            raise SlicingError(
                f"Slicer returned {resp.status_code}: {resp.text[:500]}",
            )
        return resp.json()

    async def upload_3mf(self, data: bytes, *, filename: str = "input.3mf") -> dict:
        """POST /3mf — upload bytes, get a token + sha256.

        Returns the JSON response: ``{token, sha256, size, evicts}``.
        """
        files = {"file": (filename, data, "application/octet-stream")}
        try:
            async with httpx.AsyncClient(timeout=120.0, transport=self._transport) as client:
                r = await client.post(f"{self._base_url}/3mf", files=files)
        except httpx.HTTPError as e:
            raise SlicingError(f"Slicer unreachable: {e}")
        r.raise_for_status()
        return r.json()

    async def inspect(self, token: str) -> dict:
        """GET /3mf/{token}/inspect — return the structured summary.

        Returns the JSON response with ``plates``, ``filaments``,
        ``estimate``, ``bbox``, ``thumbnail_urls``, ``use_set_per_plate``,
        and ``schema_version``.
        """
        try:
            async with httpx.AsyncClient(timeout=60.0, transport=self._transport) as client:
                r = await client.get(f"{self._base_url}/3mf/{token}/inspect")
        except httpx.HTTPError as e:
            raise SlicingError(f"Slicer unreachable: {e}")
        r.raise_for_status()
        return r.json()

    async def delete_token(self, token: str) -> bool:
        """DELETE /3mf/{token} — drop the cached file.

        Returns True when the slicer confirmed the delete, False on 404
        (token already evicted). Other HTTP errors raise.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
                r = await client.delete(f"{self._base_url}/3mf/{token}")
        except httpx.HTTPError as e:
            raise SlicingError(f"Slicer unreachable: {e}")
        if r.status_code == 404:
            return False
        r.raise_for_status()
        return True

    async def resolve_for_machine(
        self,
        *,
        machine_id: str,
        process_name: str = "",
        filament_names: list[str] | None = None,
        plate_type: str = "",
    ) -> dict:
        """POST /profiles/resolve-for-machine.

        Returns the slicer's JSON response unchanged: `{machine_id,
        machine_name, process, filaments, plate_type}` where each resolved
        block carries a `match` reason (`alias`, `default`, `type`,
        `layer_height`, `unchanged`, `first_compat`, `none`). Used by the
        print form to hydrate process / filament / plate-type fields with
        GUI-equivalent defaults when the user picks a target machine.
        """
        body = {
            "machine_id": machine_id,
            "process_name": process_name,
            "filament_names": list(filament_names or []),
            "plate_type": plate_type,
        }
        url = f"{self._base_url}/profiles/resolve-for-machine"
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
                resp = await client.post(url, json=body)
        except httpx.HTTPError as e:
            raise SlicingError(f"Slicer unreachable: {e}")

        if resp.status_code != 200:
            raise SlicingError(
                f"Slicer returned {resp.status_code}: {resp.text[:500]}",
            )
        return resp.json()

    async def get_filament_detail(self, setting_id: str) -> dict | None:
        """Fetch one filament profile with its full resolved field set.

        Wraps the slicer's `/profiles/filaments/{setting_id}` endpoint, which
        returns `{setting_id, name, vendor, resolved: {...}, inheritance_chain}`.
        Returns None when the slicer reports 404 / is unreachable.
        """
        url = f"{self._base_url}/profiles/filaments/{setting_id}"
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
                resp = await client.get(url)
        except httpx.HTTPError as e:
            logger.error("Failed to fetch filament %s from slicer: %s", setting_id, e)
            return None

        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.error(
                "Slicer /profiles/filaments/%s returned %d",
                setting_id, resp.status_code,
            )
            return None
        return resp.json()
