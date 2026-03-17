"""FastAPI application entry point."""

from __future__ import annotations

import base64
import json
import logging
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import PrinterConfig, settings
from app import config_store
from app.filament_selection import build_slicer_filament_payload
from app.models import (
    AMSResponse,
    AMSTray,
    AMSUnit,
    CommandResponse,
    FilamentMatchRequest,
    FilamentMatchResponse,
    FilamentMatchReason,
    HealthResponse,
    FilamentInfo,
    ProjectFilamentMatch,
    PrinterConfigInput,
    PrinterConfigListResponse,
    PrinterConfigResponse,
    PrinterDetailResponse,
    PrinterListResponse,
    PrintResponse,
    SettingsTransferInfo,
    SlicerFilament,
    SpeedRequest,
    StartDryingRequest,
    TransferredSetting,
)
from app.parse_3mf import parse_3mf, sanitize_3mf
from app.printer_service import PrinterService
from app.slicer_client import SlicerClient, SliceResult, SlicingError

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

printer_service: PrinterService
slicer_client: SlicerClient | None = None
templates = Jinja2Templates(directory="app/templates")

MAX_FILE_BYTES = settings.max_file_size_mb * 1024 * 1024
DEFAULT_PLATE_TYPES = [
    {"value": "cool_plate", "label": "Cool Plate"},
    {"value": "engineering_plate", "label": "Engineering Plate"},
    {"value": "high_temp_plate", "label": "High Temp Plate"},
    {"value": "textured_pei_plate", "label": "Textured PEI Plate"},
    {"value": "textured_cool_plate", "label": "Textured Cool Plate"},
    {"value": "supertack_plate", "label": "Supertack Plate"},
]

_PREVIEW_DIR = Path(tempfile.gettempdir()) / "bambu-gateway-previews"
_PREVIEW_DIR.mkdir(exist_ok=True)


def _store_preview(
    file_data: bytes,
    filename: str,
    printer_id: str = "",
    plate_id: int = 0,
    filament_profiles: list[str] | dict | None = None,
    project_filament_count: int | None = None,
) -> str:
    preview_id = uuid.uuid4().hex[:12]
    meta = {"filename": filename, "printer_id": printer_id, "plate_id": plate_id}
    if filament_profiles is not None:
        meta["filament_profiles"] = filament_profiles
    if project_filament_count is not None:
        meta["project_filament_count"] = project_filament_count
    (_PREVIEW_DIR / f"{preview_id}.3mf").write_bytes(file_data)
    (_PREVIEW_DIR / f"{preview_id}.json").write_text(json.dumps(meta))
    return preview_id


def _pop_preview(preview_id: str) -> dict | None:
    """Return preview dict with keys: file_data, filename, printer_id, plate_id."""
    data_path = _PREVIEW_DIR / f"{preview_id}.3mf"
    meta_path = _PREVIEW_DIR / f"{preview_id}.json"
    if not data_path.exists():
        return None
    file_data = data_path.read_bytes()
    meta = {
        "filename": preview_id + ".3mf",
        "printer_id": "",
        "plate_id": 0,
        "filament_profiles": None,
        "project_filament_count": None,
    }
    if meta_path.exists():
        try:
            meta.update(json.loads(meta_path.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
        meta_path.unlink(missing_ok=True)
    data_path.unlink(missing_ok=True)
    return {"file_data": file_data, **meta}


def _extract_selected_tray_slots(
    filament_payload: list[str] | dict | None,
) -> dict[int, int]:
    """Return project filament index -> selected AMS tray slot."""
    if not isinstance(filament_payload, dict):
        return {}

    tray_slots: dict[int, int] = {}
    for slot_str, selection in filament_payload.items():
        if not isinstance(selection, dict):
            continue
        tray_slot = selection.get("tray_slot")
        if not isinstance(tray_slot, int):
            continue
        try:
            filament_index = int(slot_str)
        except (TypeError, ValueError):
            continue
        tray_slots[filament_index] = tray_slot
    return tray_slots


def _build_ams_mapping(
    filament_payload: list[str] | dict | None,
    project_filament_count: int | None = None,
) -> tuple[list[int] | None, bool]:
    """Build the ams_mapping array for the printer's project_file command.

    Returns a variable-length array (one entry per project filament) mapping
    each filament index to an AMS tray slot, plus a use_ams flag.
    """
    tray_slots = _extract_selected_tray_slots(filament_payload)
    if not tray_slots:
        logger.info("AMS mapping: no tray_slot selections found in payload=%s", filament_payload)
        return None, False

    if project_filament_count is None:
        project_filament_count = max(tray_slots) + 1

    ams_mapping = [-1] * project_filament_count

    use_ams = False
    for filament_index, tray_slot in tray_slots.items():
        if filament_index < 0 or filament_index >= project_filament_count:
            continue
        ams_mapping[filament_index] = tray_slot
        use_ams = True

    logger.info("AMS mapping: %s, use_ams=%s", ams_mapping, use_ams)
    return ams_mapping, use_ams


@asynccontextmanager
async def lifespan(app: FastAPI):
    global printer_service, slicer_client
    configs = config_store.load()
    printer_service = PrinterService(configs)
    printer_service.start()
    if settings.orcaslicer_api_url:
        slicer_client = SlicerClient(settings.orcaslicer_api_url)
    yield
    printer_service.stop()


app = FastAPI(title="Bambu Gateway", version="0.1.0", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


# --- Web UI ---


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html")


# --- API ---


@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse()


@app.get("/api/printers", response_model=PrinterListResponse)
async def list_printers():
    return PrinterListResponse(printers=printer_service.get_all_statuses())


@app.get("/api/printers/{printer_id}", response_model=PrinterDetailResponse)
async def get_printer(printer_id: str):
    status = printer_service.get_status(printer_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Printer not found")
    return PrinterDetailResponse(printer=status)


def _resolve_printer_id(printer_id: str) -> str:
    """Resolve a printer_id, falling back to the default printer."""
    pid = printer_id or printer_service.default_printer_id()
    if pid is None:
        raise HTTPException(status_code=404, detail="No printers configured")
    if printer_service.get_client(pid) is None:
        raise HTTPException(status_code=404, detail="Printer not found")
    return pid


def _run_printer_command(
    printer_id: str,
    command: str,
    action,
) -> CommandResponse:
    """Execute a printer control command with standard error handling."""
    pid = _resolve_printer_id(printer_id)
    try:
        action(pid)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return CommandResponse(printer_id=pid, command=command)


@app.post("/api/printers/{printer_id}/pause", response_model=CommandResponse)
async def pause_print(printer_id: str):
    return _run_printer_command(printer_id, "pause", printer_service.pause_print)


@app.post("/api/printers/{printer_id}/resume", response_model=CommandResponse)
async def resume_print(printer_id: str):
    return _run_printer_command(printer_id, "resume", printer_service.resume_print)


@app.post("/api/printers/{printer_id}/cancel", response_model=CommandResponse)
async def cancel_print(printer_id: str):
    return _run_printer_command(printer_id, "cancel", printer_service.cancel_print)


@app.post("/api/printers/{printer_id}/speed", response_model=CommandResponse)
async def set_print_speed(printer_id: str, body: SpeedRequest):
    pid = _resolve_printer_id(printer_id)
    try:
        printer_service.set_print_speed(pid, body.level.value)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return CommandResponse(printer_id=pid, command=f"speed:{body.level.name}")


@app.post(
    "/api/printers/{printer_id}/ams/{ams_id}/start-drying",
    response_model=CommandResponse,
)
async def start_drying(
    printer_id: str,
    ams_id: int,
    body: StartDryingRequest | None = None,
):
    pid = _resolve_printer_id(printer_id)
    temp = body.temperature if body else 55
    duration = body.duration_minutes if body else 480
    try:
        printer_service.start_drying(pid, ams_id, temp, duration)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return CommandResponse(printer_id=pid, command=f"start_drying:ams{ams_id}")


@app.post(
    "/api/printers/{printer_id}/ams/{ams_id}/stop-drying",
    response_model=CommandResponse,
)
async def stop_drying(printer_id: str, ams_id: int):
    pid = _resolve_printer_id(printer_id)
    try:
        printer_service.stop_drying(pid, ams_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return CommandResponse(printer_id=pid, command=f"stop_drying:ams{ams_id}")


def _normalize_filament_id(value: str) -> str:
    return str(value or "").strip().upper()


def _to_slicer_filament(profile: dict | None) -> SlicerFilament | None:
    if profile is None:
        return None
    return SlicerFilament(
        name=str(profile.get("name", "")).strip(),
        filament_id=str(profile.get("filament_id", "")).strip(),
        setting_id=str(profile.get("setting_id", "")).strip(),
    )


def _filament_match_priority(filament: dict, machine: str = "") -> int:
    compatible_printers = filament.get("compatible_printers") or []
    if machine and machine in compatible_printers:
        return 2
    if not compatible_printers:
        return 1
    return 0


def _index_filaments_by_id(
    slicer_filaments: list[dict],
    machine: str = "",
) -> dict[str, dict]:
    filaments_by_id: dict[str, dict] = {}
    priorities: dict[str, int] = {}
    for filament in slicer_filaments:
        filament_id = _normalize_filament_id(filament.get("filament_id", ""))
        if not filament_id:
            continue
        priority = _filament_match_priority(filament, machine)
        if priority <= 0:
            continue
        if priority > priorities.get(filament_id, -1):
            filaments_by_id[filament_id] = filament
            priorities[filament_id] = priority
    return filaments_by_id


async def _get_machine_slicer_filaments(printer_id: str) -> tuple[list[dict], str]:
    if slicer_client is None:
        return [], ""

    cfg = printer_service.get_config(printer_id)
    machine = cfg.machine_model if cfg else ""
    slicer_filaments = await slicer_client.get_profiles(
        "filaments",
        machine=machine,
        ams_assignable=True,
    )
    return slicer_filaments, machine


async def _get_slicer_filament_catalog(printer_id: str) -> tuple[list[dict], list[dict], str]:
    if slicer_client is None:
        return [], [], ""

    cfg = printer_service.get_config(printer_id)
    machine = cfg.machine_model if cfg else ""
    filtered = await slicer_client.get_profiles("filaments", machine=machine)
    all_filaments = await slicer_client.get_profiles("filaments")
    return filtered, all_filaments, machine


def _find_project_filament_profile(
    filament: FilamentInfo,
    filtered_profiles: list[dict],
    all_profiles: list[dict],
) -> dict | None:
    wanted = str(filament.setting_id or "").strip()
    if not wanted:
        return None

    for profiles in (filtered_profiles, all_profiles):
        for key in ("setting_id", "name"):
            match = next(
                (p for p in profiles if str(p.get(key, "")).strip() == wanted),
                None,
            )
            if match is not None:
                return match
    return None


def _tray_slot_value(raw_tray: dict) -> int | None:
    try:
        slot = int(raw_tray.get("slot", -1))
    except (TypeError, ValueError):
        return None
    return slot if slot >= 0 else None


def _tray_filament_id(raw_tray: dict) -> str:
    return _normalize_filament_id(
        raw_tray.get("tray_info_idx") or raw_tray.get("filament_id", "")
    )


def _build_project_filament_matches(
    project_filaments: list[FilamentInfo],
    raw_trays: list[dict],
    filtered_profiles: list[dict],
    all_profiles: list[dict],
) -> list[ProjectFilamentMatch]:
    sorted_trays = sorted(
        raw_trays,
        key=lambda raw: (_tray_slot_value(raw) is None, _tray_slot_value(raw) or 0),
    )

    matches: list[ProjectFilamentMatch] = []
    for filament in project_filaments:
        resolved_profile = _find_project_filament_profile(
            filament,
            filtered_profiles,
            all_profiles,
        )
        preferred_tray_slot = None
        match_reason = FilamentMatchReason.none

        resolved_filament_id = _normalize_filament_id(
            resolved_profile.get("filament_id", "") if resolved_profile else ""
        )
        if resolved_filament_id:
            for raw_tray in sorted_trays:
                slot = _tray_slot_value(raw_tray)
                if slot is None:
                    continue
                if _tray_filament_id(raw_tray) == resolved_filament_id:
                    preferred_tray_slot = slot
                    match_reason = FilamentMatchReason.exact_filament_id
                    break

        if preferred_tray_slot is None:
            wanted_type = str(filament.type or "").strip().upper()
            if wanted_type:
                for raw_tray in sorted_trays:
                    slot = _tray_slot_value(raw_tray)
                    if slot is None:
                        continue
                    tray_type = str(raw_tray.get("tray_type", "")).strip().upper()
                    if tray_type == wanted_type:
                        preferred_tray_slot = slot
                        match_reason = FilamentMatchReason.type_fallback
                        break

        matches.append(ProjectFilamentMatch(
            index=filament.index,
            setting_id=filament.setting_id,
            type=filament.type,
            color=filament.color,
            resolved_profile=_to_slicer_filament(resolved_profile),
            preferred_tray_slot=preferred_tray_slot,
            match_reason=match_reason,
        ))

    return matches


async def _get_ams_tray_profile_map(printer_id: str) -> dict[int, str]:
    """Return current AMS tray slot -> matched slicer filament setting_id.

    Includes both AMS trays and the external spool holder (vt_tray).
    """
    ams_info = printer_service.get_ams_info(printer_id)
    if ams_info is None:
        return {}

    raw_trays, _raw_units, raw_vt_tray = ams_info

    slicer_filaments, machine = await _get_machine_slicer_filaments(printer_id)
    filaments_by_id = _index_filaments_by_id(slicer_filaments, machine)

    all_trays = list(raw_trays)
    if raw_vt_tray is not None:
        all_trays.append(raw_vt_tray)

    tray_profile_map: dict[int, str] = {}
    for raw in all_trays:
        try:
            slot = int(raw.get("slot", -1))
        except (TypeError, ValueError):
            continue
        tray_info_idx = str(raw.get("tray_info_idx", "")).strip()
        matched_setting_id = ""
        if tray_info_idx:
            matched = filaments_by_id.get(_normalize_filament_id(tray_info_idx))
            if matched is not None:
                matched_setting_id = str(matched.get("setting_id", "")).strip()
        tray_profile_map[slot] = matched_setting_id

    return tray_profile_map


async def _resolve_slice_filament_payload(
    project_filament_ids: list[str],
    filament_profiles: str,
    printer_id: str = "",
) -> tuple[list[str] | dict | None, str | None]:
    """Return the slicer payload for project filament selections."""
    tray_profile_map = None
    if filament_profiles and '"tray_slot"' in filament_profiles:
        pid = printer_id or printer_service.default_printer_id()
        if pid is None:
            return None, "tray_slot requires a configured printer"
        tray_profile_map = await _get_ams_tray_profile_map(pid)

    return build_slicer_filament_payload(
        project_filament_ids,
        filament_profiles,
        tray_profile_map=tray_profile_map,
    )


async def _validate_selected_trays(
    filament_payload: list[str] | dict | None,
    printer_id: str,
) -> str | None:
    """Re-check tray-specific selections against current AMS state."""
    if not isinstance(filament_payload, dict):
        return None

    tray_selections = {
        slot_str: selection
        for slot_str, selection in filament_payload.items()
        if isinstance(selection, dict) and selection.get("tray_slot") is not None
    }
    if not tray_selections:
        return None

    tray_profile_map = await _get_ams_tray_profile_map(printer_id)
    for slot_str, selection in tray_selections.items():
        tray_slot = selection.get("tray_slot")
        if tray_slot not in tray_profile_map:
            return f"AMS tray slot {tray_slot} is not available on the selected printer"

    return None


@app.get("/api/ams", response_model=AMSResponse)
async def get_ams():
    pid = printer_service.default_printer_id()
    if pid is None:
        raise HTTPException(status_code=404, detail="No printers configured")

    ams_info = printer_service.get_ams_info(pid)
    if ams_info is None:
        raise HTTPException(status_code=404, detail="Printer not found")

    raw_trays, raw_units, raw_vt_tray = ams_info

    slicer_filaments, machine = await _get_machine_slicer_filaments(pid)
    filaments_by_id = _index_filaments_by_id(slicer_filaments, machine)

    trays: list[AMSTray] = []
    for raw in raw_trays:
        trays.append(_build_ams_tray(raw, filaments_by_id))

    units = [AMSUnit(**u) for u in raw_units]

    vt_tray: AMSTray | None = None
    if raw_vt_tray is not None:
        vt_tray = _build_ams_tray(raw_vt_tray, filaments_by_id)

    return AMSResponse(printer_id=pid, trays=trays, units=units, vt_tray=vt_tray)


def _build_ams_tray(raw: dict, filaments_by_id: dict) -> AMSTray:
    """Build an AMSTray from a raw dict, matching filament profile if possible."""
    raw_with_defaults = dict(raw)
    tray_info_idx = str(raw_with_defaults.pop("tray_info_idx", "")).strip()
    matched = None
    if tray_info_idx:
        f = filaments_by_id.get(_normalize_filament_id(tray_info_idx))
        if f is not None:
            matched = SlicerFilament(
                name=f.get("name", ""),
                filament_id=f.get("filament_id", ""),
                setting_id=f.get("setting_id", ""),
            )
    return AMSTray(
        **raw_with_defaults,
        filament_id=tray_info_idx,
        matched_filament=matched,
    )


@app.post("/api/filament-matches", response_model=FilamentMatchResponse)
async def filament_matches(request: FilamentMatchRequest):
    pid = request.printer_id or printer_service.default_printer_id()
    if pid is None:
        raise HTTPException(status_code=404, detail="No printers configured")

    ams_info = printer_service.get_ams_info(pid)
    if ams_info is None:
        raise HTTPException(status_code=404, detail="Printer not found")

    raw_trays, _raw_units, raw_vt_tray = ams_info
    all_trays = list(raw_trays)
    if raw_vt_tray is not None:
        all_trays.append(raw_vt_tray)

    filtered_profiles, all_profiles, _machine = await _get_slicer_filament_catalog(pid)
    matches = _build_project_filament_matches(
        request.filaments,
        all_trays,
        filtered_profiles,
        all_profiles,
    )
    return FilamentMatchResponse(printer_id=pid, matches=matches)


async def _proxy_slicer_profiles(
    category: str,
    machine: str = "",
    ams_assignable: bool | None = None,
) -> list[dict]:
    if slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="Slicer not configured: ORCASLICER_API_URL not set",
        )
    return await slicer_client.get_profiles(
        category,
        machine=machine,
        ams_assignable=ams_assignable,
    )


@app.get("/api/slicer/machines")
async def slicer_machines():
    return await _proxy_slicer_profiles("machines")


@app.get("/api/slicer/processes")
async def slicer_processes(machine: str = ""):
    return await _proxy_slicer_profiles("processes", machine=machine)


@app.get("/api/slicer/filaments")
async def slicer_filaments(
    machine: str = "",
    ams_assignable: bool | None = None,
):
    return await _proxy_slicer_profiles(
        "filaments",
        machine=machine,
        ams_assignable=ams_assignable,
    )


@app.get("/api/slicer/plate-types")
async def slicer_plate_types():
    if slicer_client is None:
        return DEFAULT_PLATE_TYPES
    plate_types = await slicer_client.get_profiles("plate-types")
    return plate_types or DEFAULT_PLATE_TYPES


@app.post("/api/parse-3mf")
async def parse_3mf_file(file: UploadFile):
    if not file.filename or not file.filename.lower().endswith(".3mf"):
        raise HTTPException(status_code=400, detail="File must be a .3mf file")

    file_data = await file.read()

    if len(file_data) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {settings.max_file_size_mb} MB limit",
        )

    try:
        info = parse_3mf(file_data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse 3MF: {e}")

    return info


@app.post("/api/print")
async def print_file(
    file: UploadFile = None,
    printer_id: str = Form(""),
    plate_id: int = Form(0),
    preview_id: str = Form(""),
    machine_profile: str = Form(""),
    process_profile: str = Form(""),
    filament_profiles: str = Form(""),
    plate_type: str = Form(""),
    slice_only: bool = Form(False),
):
    # --- Fast path: print from a stored preview ---
    if preview_id:
        preview = _pop_preview(preview_id)
        if preview is None:
            raise HTTPException(status_code=404, detail="Preview not found or expired")

        pid = printer_id or preview["printer_id"] or printer_service.default_printer_id()
        if pid is None:
            raise HTTPException(status_code=404, detail="No printers configured")

        pplate = preview["plate_id"] or 1
        tray_error = await _validate_selected_trays(preview.get("filament_profiles"), pid)
        if tray_error is not None:
            raise HTTPException(status_code=409, detail=tray_error)
        ams_mapping, use_ams = _build_ams_mapping(
            preview.get("filament_profiles"),
            project_filament_count=preview.get("project_filament_count"),
        )
        try:
            printer_service.submit_print(
                pid,
                preview["file_data"],
                preview["filename"],
                plate_id=pplate,
                ams_mapping=ams_mapping,
                use_ams=use_ams,
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ConnectionError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Print failed: {e}")

        return PrintResponse(
            status="accepted",
            file_name=preview["filename"],
            printer_id=pid,
            was_sliced=True,
        )

    # --- Normal path ---

    # Validate file
    if not file or not file.filename or not file.filename.lower().endswith(".3mf"):
        raise HTTPException(status_code=400, detail="File must be a .3mf file")

    file_data = await file.read()

    if len(file_data) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {settings.max_file_size_mb} MB limit",
        )

    # Parse 3MF metadata
    try:
        info = parse_3mf(file_data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse 3MF: {e}")

    was_sliced = False
    slice_result: SliceResult | None = None
    filament_payload: list[str] | dict | None = None

    # For already-sliced files, resolve AMS tray mapping
    logger.info(
        "Print request: has_gcode=%s filament_profiles=%r filaments=%s",
        info.has_gcode,
        filament_profiles[:200] if filament_profiles else "",
        [f.setting_id for f in info.filaments],
    )
    if info.has_gcode:
        if filament_profiles:
            # Explicit filament_profiles from client (with tray_slot)
            try:
                filament_payload = json.loads(filament_profiles)
                logger.info("Parsed explicit filament_payload: %s", filament_payload)
            except json.JSONDecodeError:
                logger.warning("Failed to parse filament_profiles JSON")

        if filament_payload is None and info.filaments:
            # Auto-match project filaments to AMS trays
            pid = printer_id or printer_service.default_printer_id()
            if pid is not None:
                ams_info = printer_service.get_ams_info(pid)
                if ams_info is not None:
                    raw_trays, _raw_units, raw_vt_tray = ams_info
                    all_trays = list(raw_trays)
                    if raw_vt_tray is not None:
                        all_trays.append(raw_vt_tray)
                    filtered_profiles, all_profiles, _ = await _get_slicer_filament_catalog(pid)
                    matches = _build_project_filament_matches(
                        info.filaments, all_trays, filtered_profiles, all_profiles,
                    )
                    auto_payload: dict = {}
                    for m in matches:
                        if m.preferred_tray_slot is not None and m.resolved_profile:
                            auto_payload[str(m.index)] = {
                                "profile_setting_id": m.resolved_profile.setting_id,
                                "tray_slot": m.preferred_tray_slot,
                            }
                    if auto_payload:
                        filament_payload = auto_payload
                        logger.info(
                            "Auto-matched filaments for pre-sliced 3MF: %s",
                            auto_payload,
                        )

    if not info.has_gcode:
        # Needs slicing
        if not machine_profile or not process_profile:
            raise HTTPException(
                status_code=400,
                detail="File is not sliced. Provide machine_profile and process_profile.",
            )
        if slicer_client is None:
            raise HTTPException(
                status_code=400,
                detail="Slicing not available: ORCASLICER_API_URL not configured",
            )

        filament_payload, filament_error = await _resolve_slice_filament_payload(
            [f.setting_id for f in info.filaments],
            filament_profiles,
            printer_id,
        )
        if filament_error is not None or filament_payload is None:
            raise HTTPException(status_code=400, detail=filament_error)

        file_data = sanitize_3mf(file_data)

        try:
            slice_result = await slicer_client.slice(
                file_data,
                file.filename,
                machine_profile,
                process_profile,
                filament_payload,
                plate_type=plate_type.strip(),
            )
        except SlicingError as e:
            raise HTTPException(status_code=502, detail=f"Slicing failed: {e}")

        file_data = slice_result.content
        was_sliced = True

    # Build settings transfer info if available
    settings_transfer = None
    if slice_result and slice_result.settings_transfer_status:
        settings_transfer = SettingsTransferInfo(
            status=slice_result.settings_transfer_status,
            transferred=[
                TransferredSetting(**s) for s in slice_result.settings_transferred
            ],
        )

    # If slice_only, return the sliced file as a download
    if slice_only:
        headers = {
            "Content-Disposition": f'attachment; filename="{file.filename}"',
        }
        if settings_transfer:
            headers["X-Settings-Transfer-Status"] = settings_transfer.status
            if settings_transfer.transferred:
                headers["X-Settings-Transferred"] = json.dumps(
                    [s.model_dump() for s in settings_transfer.transferred]
                )
        return Response(
            content=file_data,
            media_type="application/octet-stream",
            headers=headers,
        )

    # Resolve printer
    pid = printer_id or printer_service.default_printer_id()
    if pid is None:
        raise HTTPException(status_code=404, detail="No printers configured")

    tray_error = await _validate_selected_trays(filament_payload, pid)
    if tray_error is not None:
        raise HTTPException(status_code=409, detail=tray_error)
    ams_mapping, use_ams = _build_ams_mapping(
        filament_payload,
        project_filament_count=len(info.filaments) or None,
    )
    logger.info(
        "Print submission: filament_payload=%s ams_mapping=%s use_ams=%s filament_count=%s",
        filament_payload, ams_mapping, use_ams, len(info.filaments),
    )

    try:
        printer_service.submit_print(
            pid,
            file_data,
            file.filename,
            plate_id=plate_id or 1,
            ams_mapping=ams_mapping,
            use_ams=use_ams,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Print failed: {e}")

    return PrintResponse(
        status="accepted",
        file_name=file.filename,
        printer_id=pid,
        was_sliced=was_sliced,
        settings_transfer=settings_transfer,
    )


@app.post("/api/print-preview")
async def print_preview(
    file: UploadFile,
    printer_id: str = Form(""),
    plate_id: int = Form(0),
    machine_profile: str = Form(""),
    process_profile: str = Form(""),
    filament_profiles: str = Form(""),
    plate_type: str = Form(""),
):
    """Slice a 3MF file, store the result for later printing, and return it."""
    if not file.filename or not file.filename.lower().endswith(".3mf"):
        raise HTTPException(status_code=400, detail="File must be a .3mf file")

    file_data = await file.read()
    if len(file_data) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {settings.max_file_size_mb} MB limit",
        )

    if not machine_profile or not process_profile:
        raise HTTPException(
            status_code=400,
            detail="machine_profile and process_profile are required",
        )
    if slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="Slicing not available: ORCASLICER_API_URL not configured",
        )

    try:
        info = parse_3mf(file_data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse 3MF: {e}")

    filament_payload, filament_error = await _resolve_slice_filament_payload(
        [f.setting_id for f in info.filaments],
        filament_profiles,
        printer_id,
    )
    if filament_error is not None or filament_payload is None:
        raise HTTPException(status_code=400, detail=filament_error)

    file_data = sanitize_3mf(file_data)

    try:
        slice_result = await slicer_client.slice(
            file_data,
            file.filename,
            machine_profile,
            process_profile,
            filament_payload,
            plate_type=plate_type.strip(),
        )
    except SlicingError as e:
        raise HTTPException(status_code=502, detail=f"Slicing failed: {e}")

    preview_id = _store_preview(
        slice_result.content,
        file.filename,
        printer_id=printer_id,
        plate_id=plate_id,
        filament_profiles=filament_payload,
        project_filament_count=len(info.filaments),
    )

    headers = {
        "Content-Disposition": f'attachment; filename="{file.filename}"',
        "X-Preview-Id": preview_id,
    }
    if slice_result.settings_transfer_status:
        headers["X-Settings-Transfer-Status"] = slice_result.settings_transfer_status
        if slice_result.settings_transferred:
            headers["X-Settings-Transferred"] = json.dumps(
                slice_result.settings_transferred
            )

    return Response(
        content=slice_result.content,
        media_type="application/octet-stream",
        headers=headers,
    )


def _sse_event(event_type: str, data: dict) -> str:
    """Format a Server-Sent Event string."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


@app.post("/api/print-stream")
async def print_file_stream(
    file: UploadFile,
    printer_id: str = Form(""),
    plate_id: int = Form(0),
    machine_profile: str = Form(""),
    process_profile: str = Form(""),
    filament_profiles: str = Form(""),
    plate_type: str = Form(""),
    slice_only: bool = Form(False),
    preview: bool = Form(False),
):
    """Slice and optionally print a 3MF file, streaming progress via SSE."""
    if not file.filename or not file.filename.lower().endswith(".3mf"):
        raise HTTPException(status_code=400, detail="File must be a .3mf file")

    file_data = await file.read()
    if len(file_data) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {settings.max_file_size_mb} MB limit",
        )

    if not machine_profile or not process_profile:
        raise HTTPException(
            status_code=400,
            detail="machine_profile and process_profile are required",
        )
    if slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="Slicing not available: ORCASLICER_API_URL not configured",
        )

    try:
        info = parse_3mf(file_data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse 3MF: {e}")

    filament_payload, filament_error = await _resolve_slice_filament_payload(
        [f.setting_id for f in info.filaments],
        filament_profiles,
        printer_id,
    )
    if filament_error is not None or filament_payload is None:
        raise HTTPException(status_code=400, detail=filament_error)

    file_data = sanitize_3mf(file_data)
    filename = file.filename

    pid = printer_id or printer_service.default_printer_id()
    if not slice_only and not preview and pid is None:
        raise HTTPException(status_code=404, detail="No printers configured")

    async def generate():
        result_bytes = None
        settings_transfer_data = None

        try:
            async for event in slicer_client.slice_stream(
                file_data,
                filename,
                machine_profile,
                process_profile,
                filament_payload,
                plate_type=plate_type.strip(),
            ):
                etype = event["event"]
                edata = event["data"]

                if etype == "result":
                    settings_transfer_data = edata.get("settings_transfer")
                    result_bytes = base64.b64decode(edata["file_base64"])

                    if preview:
                        pid_preview = _store_preview(
                            result_bytes,
                            filename,
                            printer_id=printer_id,
                            plate_id=plate_id,
                            filament_profiles=filament_payload,
                            project_filament_count=len(info.filaments),
                        )
                        edata["preview_id"] = pid_preview
                        yield _sse_event("result", edata)
                    elif slice_only:
                        yield _sse_event("result", edata)
                    else:
                        yield _sse_event("status", {
                            "phase": "uploading",
                            "message": "Sending to printer...",
                        })
                elif etype == "done":
                    if not slice_only and not preview and result_bytes is not None:
                        try:
                            tray_error = await _validate_selected_trays(filament_payload, pid)
                            if tray_error is not None:
                                yield _sse_event("error", {"error": tray_error})
                                yield _sse_event("done", {})
                                return
                            ams_mapping, use_ams = _build_ams_mapping(
                                filament_payload,
                                project_filament_count=len(info.filaments),
                            )
                            printer_service.submit_print(
                                pid,
                                result_bytes,
                                filename,
                                plate_id=plate_id or 1,
                                ams_mapping=ams_mapping,
                                use_ams=use_ams,
                            )
                            yield _sse_event("print_started", {
                                "printer_id": pid,
                                "file_name": filename,
                                "settings_transfer": settings_transfer_data,
                            })
                        except Exception as e:
                            yield _sse_event("error", {"error": str(e)})
                    yield _sse_event("done", {})
                else:
                    yield _sse_event(etype, edata)
        except SlicingError as e:
            yield _sse_event("error", {"error": str(e)})
            yield _sse_event("done", {})
        except Exception as e:
            yield _sse_event("error", {"error": f"Unexpected error: {e}"})
            yield _sse_event("done", {})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Settings API ---


def _config_to_response(cfg: PrinterConfig) -> PrinterConfigResponse:
    return PrinterConfigResponse(
        serial=cfg.serial,
        ip=cfg.ip,
        name=cfg.name,
        machine_model=cfg.machine_model,
    )


@app.get("/api/settings/printers", response_model=PrinterConfigListResponse)
async def list_printer_configs():
    configs = printer_service.get_configs()
    return PrinterConfigListResponse(
        printers=[_config_to_response(c) for c in configs],
    )


@app.post("/api/settings/printers", response_model=PrinterConfigResponse,
          status_code=201)
async def add_printer_config(body: PrinterConfigInput):
    existing = {c.serial for c in printer_service.get_configs()}
    if body.serial in existing:
        raise HTTPException(status_code=409, detail="Printer already exists")
    if not body.serial:
        raise HTTPException(status_code=400, detail="Serial is required")
    if not body.access_code:
        raise HTTPException(status_code=400, detail="Access code is required")

    cfg = PrinterConfig(
        serial=body.serial,
        ip=body.ip,
        access_code=body.access_code,
        name=body.name,
        machine_model=body.machine_model,
    )
    configs = printer_service.get_configs() + [cfg]
    config_store.save(configs)
    printer_service.sync_printers(configs)
    return _config_to_response(cfg)


@app.put("/api/settings/printers/{serial}", response_model=PrinterConfigResponse)
async def update_printer_config(serial: str, body: PrinterConfigInput):
    configs = printer_service.get_configs()
    by_serial = {c.serial: c for c in configs}
    old = by_serial.get(serial)
    if old is None:
        raise HTTPException(status_code=404, detail="Printer not found")

    updated = PrinterConfig(
        serial=serial,
        ip=body.ip,
        access_code=body.access_code if body.access_code else old.access_code,
        name=body.name,
        machine_model=body.machine_model,
    )
    new_configs = [updated if c.serial == serial else c for c in configs]
    config_store.save(new_configs)
    printer_service.sync_printers(new_configs)
    return _config_to_response(updated)


@app.delete("/api/settings/printers/{serial}", status_code=204)
async def delete_printer_config(serial: str):
    configs = printer_service.get_configs()
    new_configs = [c for c in configs if c.serial != serial]
    if len(new_configs) == len(configs):
        raise HTTPException(status_code=404, detail="Printer not found")
    config_store.save(new_configs)
    printer_service.sync_printers(new_configs)
