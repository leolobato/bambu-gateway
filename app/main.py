"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.apns_client import ApnsClient
from app.apns_jwt import ApnsJwtSigner
from app.config import PrinterConfig, settings
from app import config_store
from app.device_store import ActiveActivity, DeviceRecord, DeviceStore
from app.filament_selection import build_slicer_filament_payload
from app.notification_hub import NotificationHub
from app.models import (
    ActivityRegisterRequest,
    ActivityRegisterResponse,
    AMSResponse,
    AMSTray,
    AMSUnit,
    CapabilitiesResponse,
    CommandResponse,
    DeviceInfo,
    DeviceListResponse,
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    TestPushResponse,
    FilamentMatchRequest,
    FilamentMatchResponse,
    FilamentMatchReason,
    HealthResponse,
    FilamentInfo,
    LightRequest,
    ProjectFilamentMatch,
    PrinterConfigInput,
    FilamentTransferEntry,
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
from app.parse_3mf import parse_3mf
from app.printer_service import PrinterService
from app.slicer_client import SlicerClient, SliceResult, SlicingError
from app.upload_tracker import UploadCancelledError, tracker as upload_tracker

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("python_multipart").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

printer_service: PrinterService | None = None
slicer_client: SlicerClient | None = None
_APP_DIR = Path(__file__).resolve().parent
_DIST_DIR = _APP_DIR / "static" / "dist"
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

    # Device registry + APNs
    device_store_path = config_store._config_path.parent / "devices.json"
    device_store = DeviceStore(device_store_path)

    apns_client: ApnsClient | None = None
    notification_hub: NotificationHub | None = None
    status_change_callback = None
    if settings.push_enabled:
        signer = ApnsJwtSigner(
            key_path=settings.apns_key_path,
            key_id=settings.apns_key_id,
            team_id=settings.apns_team_id,
        )
        apns_client = ApnsClient(
            signer=signer,
            bundle_id=settings.apns_bundle_id,
            environment=settings.apns_environment,
        )
        notification_hub = NotificationHub(apns=apns_client, device_store=device_store)
        notification_hub.start()
        status_change_callback = notification_hub.on_status_change
        logger.info("APNs push enabled")
    else:
        logger.info("APNs push disabled — set APNS_KEY_PATH and related vars to enable")

    printer_service = PrinterService(
        configs, status_change_callback=status_change_callback,
    )
    printer_service.start()
    if settings.orcaslicer_api_url:
        slicer_client = SlicerClient(settings.orcaslicer_api_url)

    app.state.device_store = device_store
    app.state.notification_hub = notification_hub
    app.state.apns_client = apns_client

    yield
    printer_service.stop()
    if notification_hub is not None:
        notification_hub.stop()
    if apns_client is not None:
        await apns_client.aclose()


app = FastAPI(title="Bambu Gateway", version="1.6.0", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(_APP_DIR / "static")), name="static")


# --- React UI (root) ---

# Mount the hashed asset directory so /assets/* is served directly.
# Guarded so the app boots without a built bundle (fresh clone).
if _DIST_DIR.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_DIST_DIR / "assets")),
        name="assets",
    )


# --- API ---


@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse()


@app.get("/api/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities():
    return CapabilitiesResponse(
        push=settings.push_enabled,
        live_activities=settings.push_enabled,
        version=app.version,
    )


@app.post("/api/devices/register", response_model=DeviceRegisterResponse)
async def register_device(body: DeviceRegisterRequest):
    store: DeviceStore = app.state.device_store
    store.upsert_device(DeviceRecord(
        id=body.id,
        name=body.name,
        device_token=body.device_token,
        live_activity_start_token=body.live_activity_start_token,
        subscribed_printers=body.subscribed_printers or ["*"],
    ))
    return DeviceRegisterResponse()


@app.delete("/api/devices/{device_id}")
async def unregister_device(device_id: str):
    store: DeviceStore = app.state.device_store
    store.remove_device(device_id)
    return {"status": "ok"}


@app.post(
    "/api/devices/{device_id}/activities",
    response_model=ActivityRegisterResponse,
)
async def register_activity(device_id: str, body: ActivityRegisterRequest):
    store: DeviceStore = app.state.device_store
    if store.get_device(device_id) is None:
        raise HTTPException(status_code=404, detail="device not found")
    store.add_activity(ActiveActivity(
        device_id=device_id,
        printer_id=body.printer_id,
        activity_update_token=body.activity_update_token,
    ))
    return ActivityRegisterResponse()


@app.delete("/api/devices/{device_id}/activities/{printer_id}")
async def unregister_activity(device_id: str, printer_id: str):
    store: DeviceStore = app.state.device_store
    store.remove_activity(device_id, printer_id)
    return {"status": "ok"}


@app.get("/api/devices", response_model=DeviceListResponse)
async def list_devices():
    """Sanitized list of registered push devices — never exposes raw tokens."""
    store: DeviceStore = app.state.device_store
    printer_ids = _all_printer_ids()
    devices = []
    for dev in store.list_devices():
        activity_count = sum(
            1 for printer_id in printer_ids
            for a in store.list_activities_for_printer(printer_id)
            if a.device_id == dev.id
        )
        devices.append(DeviceInfo(
            id=dev.id,
            name=dev.name,
            has_device_token=bool(dev.device_token),
            has_live_activity_start_token=bool(dev.live_activity_start_token),
            active_activity_count=activity_count,
            subscribed_printers=list(dev.subscribed_printers),
            registered_at=dev.registered_at,
            last_seen_at=dev.last_seen_at,
        ))
    return DeviceListResponse(devices=devices)


def _all_printer_ids() -> list[str]:
    return [cfg.serial for cfg in printer_service.get_configs()]


@app.post(
    "/api/devices/{device_id}/test",
    response_model=TestPushResponse,
)
async def send_test_push(device_id: str):
    """Send a test alert notification to a specific registered device."""
    if not settings.push_enabled:
        raise HTTPException(status_code=503, detail="push is not enabled on this gateway")
    apns_client = app.state.apns_client
    if apns_client is None:
        raise HTTPException(status_code=503, detail="APNs client not initialized")
    store: DeviceStore = app.state.device_store
    dev = store.get_device(device_id)
    if dev is None:
        raise HTTPException(status_code=404, detail="device not found")
    if not dev.device_token:
        raise HTTPException(status_code=400, detail="device has no APNs token")
    result = await apns_client.send_alert(
        device_token=dev.device_token,
        title="Test notification",
        body=f"Hello from Bambu Gateway — this is a test push to {dev.name}.",
        event_type="test",
        printer_id="",
    )
    if result.ok:
        return TestPushResponse(status="ok")
    if result.token_invalid:
        store.invalidate_token(dev.device_token)
    return TestPushResponse(
        status="failed",
        detail=f"APNs returned {result.status_code} {result.reason}".strip(),
    )


@app.get("/api/uploads/{upload_id}")
async def get_upload_progress(upload_id: str):
    state = upload_tracker.get(upload_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    return state.to_dict()


@app.post("/api/uploads/{upload_id}/cancel")
async def cancel_upload(upload_id: str):
    state = upload_tracker.get(upload_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    state.cancel()
    return {"status": "cancelled"}


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


@app.post("/api/printers/{printer_id}/light", response_model=CommandResponse)
async def set_printer_light(printer_id: str, body: LightRequest):
    pid = _resolve_printer_id(printer_id)
    try:
        printer_service.set_chamber_light(pid, body.on, node=body.node)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return CommandResponse(
        printer_id=pid, command=f"light:{body.node}:{'on' if body.on else 'off'}",
    )


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
async def get_ams(printer_id: str | None = Query(default=None)):
    if printer_id is not None:
        pid = _resolve_printer_id(printer_id)  # raises 404 on unknown
    else:
        pid = printer_service.default_printer_id()
        if pid is None:
            raise HTTPException(status_code=404, detail="No printers configured")

    # Wait briefly for the first MQTT pushall on cold-start so clients don't
    # see a transient empty AMS snapshot.
    ams_info = await printer_service.get_ams_info_async(pid)
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


def _background_submit(
    upload_state,
    printer_id: str,
    file_data: bytes,
    filename: str,
    *,
    plate_id: int = 1,
    ams_mapping: list[int] | None = None,
    use_ams: bool = False,
) -> None:
    """Run submit_print in a background thread, updating upload_state."""
    try:
        printer_service.submit_print(
            printer_id,
            file_data,
            filename,
            plate_id=plate_id,
            ams_mapping=ams_mapping,
            use_ams=use_ams,
            progress_callback=upload_state.advance,
        )
        upload_state.complete()
    except UploadCancelledError:
        logger.info("Upload cancelled by user: %s", filename)
    except Exception as e:
        logger.error("Background upload failed: %s", e)
        upload_state.fail(str(e))


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

        # Sliced files are always single-plate (plate extracted before slicing).
        pplate = 1
        tray_error = await _validate_selected_trays(preview.get("filament_profiles"), pid)
        if tray_error is not None:
            raise HTTPException(status_code=409, detail=tray_error)
        ams_mapping, use_ams = _build_ams_mapping(
            preview.get("filament_profiles"),
            project_filament_count=preview.get("project_filament_count"),
        )
        # Validate printer is reachable before starting background upload
        client = printer_service.get_client(pid)
        if client is None:
            raise HTTPException(status_code=404, detail=f"Printer {pid} not found")
        try:
            client.ensure_connected()
            if not client.get_status().online:
                raise ConnectionError(f"Printer {pid} is offline")
        except ConnectionError as e:
            raise HTTPException(status_code=409, detail=str(e))

        file_data_preview = preview["file_data"]
        fname_preview = preview["filename"]
        state = upload_tracker.create(fname_preview, pid, len(file_data_preview))
        asyncio.get_running_loop().run_in_executor(None, lambda: _background_submit(
            state, pid, file_data_preview, fname_preview,
            plate_id=pplate, ams_mapping=ams_mapping, use_ams=use_ams,
        ))

        return PrintResponse(
            status="uploading",
            file_name=preview["filename"],
            printer_id=pid,
            was_sliced=True,
            upload_id=state.upload_id,
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

        try:
            slice_result = await slicer_client.slice(
                file_data,
                file.filename,
                machine_profile,
                process_profile,
                filament_payload,
                plate_type=plate_type.strip(),
                plate=plate_id or 1,
            )
        except SlicingError as e:
            raise HTTPException(status_code=502, detail=f"Slicing failed: {e}")

        file_data = slice_result.content
        was_sliced = True

    # Build settings transfer info if available
    settings_transfer = None
    if slice_result and (slice_result.settings_transfer_status or slice_result.filament_transfers):
        settings_transfer = SettingsTransferInfo(
            status=slice_result.settings_transfer_status,
            transferred=[
                TransferredSetting(**s) for s in slice_result.settings_transferred
            ],
            filaments=[
                FilamentTransferEntry(**f) for f in slice_result.filament_transfers
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
            if settings_transfer.filaments:
                headers["X-Filament-Settings-Transferred"] = json.dumps(
                    [f.model_dump() for f in settings_transfer.filaments]
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

    # Validate printer is reachable before starting background upload
    client = printer_service.get_client(pid)
    if client is None:
        raise HTTPException(status_code=404, detail=f"Printer {pid} not found")
    try:
        client.ensure_connected()
        if not client.get_status().online:
            raise ConnectionError(f"Printer {pid} is offline")
    except ConnectionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    fname = file.filename
    # Sliced files are always single-plate (plate extracted before slicing),
    # so the gcode is at plate_1 regardless of the original plate_id.
    p_id = 1 if was_sliced else (plate_id or 1)
    state = upload_tracker.create(fname, pid, len(file_data))
    asyncio.get_running_loop().run_in_executor(None, lambda: _background_submit(
        state, pid, file_data, fname,
        plate_id=p_id, ams_mapping=ams_mapping, use_ams=use_ams,
    ))

    return PrintResponse(
        status="uploading",
        file_name=fname,
        printer_id=pid,
        was_sliced=was_sliced,
        settings_transfer=settings_transfer,
        upload_id=state.upload_id,
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

    try:
        slice_result = await slicer_client.slice(
            file_data,
            file.filename,
            machine_profile,
            process_profile,
            filament_payload,
            plate_type=plate_type.strip(),
            plate=plate_id or 1,
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
    if slice_result.filament_transfers:
        headers["X-Filament-Settings-Transferred"] = json.dumps(
            slice_result.filament_transfers
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
                plate=plate_id or 1,
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
                        # upload_id sent so the frontend can cancel
                        pass  # upload_id added after upload_state is created below
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
                            upload_state = upload_tracker.create(
                                filename, pid, len(result_bytes),
                            )
                            yield _sse_event("status", {
                                "phase": "uploading",
                                "message": "Sending to printer...",
                                "upload_id": upload_state.upload_id,
                            })
                            # Sliced files are always single-plate (plate
                            # extracted before slicing), so gcode is at plate_1.
                            upload_future = asyncio.get_running_loop().run_in_executor(
                                None,
                                lambda: _background_submit(
                                    upload_state, pid, result_bytes, filename,
                                    plate_id=1,
                                    ams_mapping=ams_mapping,
                                    use_ams=use_ams,
                                ),
                            )
                            # Stream upload progress until done
                            while not upload_future.done():
                                await asyncio.sleep(0.3)
                                info_dict = upload_state.to_dict()
                                yield _sse_event("upload_progress", {
                                    "percent": info_dict["progress"],
                                    "bytes_sent": info_dict["bytes_sent"],
                                    "total_bytes": info_dict["total_bytes"],
                                })
                            # Check final result
                            await upload_future
                            if upload_state.status == "cancelled":
                                yield _sse_event("error", {
                                    "error": "Upload cancelled",
                                })
                            elif upload_state.status == "failed":
                                yield _sse_event("error", {
                                    "error": upload_state.error or "Upload failed",
                                })
                            else:
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


# --- React SPA catch-all (MUST stay at the END of this file) ---
#
# Starlette matches routes in declaration order. This catch-all matches every
# path that isn't a more specific route declared above (`/api/*`, `/static/*`,
# `/assets/*`, `/docs`, `/openapi.json`, `/redoc`, etc.). Returning the SPA's
# index.html lets the React Router resolve the path client-side.
@app.get("/{path:path}")
async def spa_catchall(path: str = ""):
    index_path = _DIST_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(
            status_code=503,
            detail="React bundle not built. Run 'cd web && npm run build'.",
        )
    return FileResponse(index_path, media_type="text/html")
