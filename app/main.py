"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
from contextlib import asynccontextmanager, AsyncExitStack
from pathlib import Path
from urllib.parse import quote

import httpx

from fastapi import Body, FastAPI, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.apns_client import ApnsClient
from app.apns_jwt import ApnsJwtSigner
from app.cloud.cloud_printer import CloudPrinterClient
from app.cloud.event_pump import EventPump
from app.cloud.plugin_downloader import PluginDownloader
from app.cloud.plugin_host import PluginHost
from app.config import PrinterConfig, settings
from app import config_store
from app.device_store import ActiveActivity, DeviceRecord, DeviceStore
from app.filament_selection import (
    build_ams_mapping,
    build_slicer_filament_payload,
    extract_selected_tray_slots,
    validate_selected_trays,
)
from app.notification_hub import NotificationHub
from app.models import (
    ActivityRegisterRequest,
    ActivityRegisterResponse,
    AmsAutoRefillRequest,
    AMSResponse,
    AMSTray,
    AMSUnit,
    CameraStatusResponse,
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
    PrintEstimate,
    PrintResponse,
    ProcessOverrideApplied,
    SetAmsFilamentRequest,
    SettingsTransferInfo,
    SlicerFilament,
    SliceJobListResponse,
    SliceJobResponse,
    SpeedRequest,
    StartDryingRequest,
    StlMaterializedProject,
    TransferredSetting,
)
from app.cloud.print_params import build_print_params
from app.mqtt_client import (
    build_ams_start_drying_command,
    build_ams_stop_drying_command,
    build_cancel_command,
    build_pause_command,
    build_resume_command,
    build_speed_command,
)
from app.parse_3mf import parse_3mf_token_via_slicer, parse_3mf_via_slicer
from app.printer_service import PrinterService
from app.slice_jobs import SliceJobManager, SliceJobStatus, SliceJobStore
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
slice_jobs: SliceJobManager | None = None
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
_SAFE_STL_DRAFT_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class StlDraftLayoutRequest(BaseModel):
    action: str


class StlDraftMaterializeRequest(BaseModel):
    thumbnail_png_data_url: str = ""


def _bambu_cdn_base_url(region: str) -> str:
    """Map a Bambu region code to the API base URL."""
    if region == "CN":
        return "https://api.bambulab.cn"
    return "https://api.bambulab.com"


def _stl_draft_dir() -> Path:
    root = config_store._config_path.parent / "stl_drafts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _stl_draft_source_path(draft_token: str) -> Path:
    if not _SAFE_STL_DRAFT_TOKEN_RE.fullmatch(draft_token):
        raise HTTPException(status_code=400, detail="Invalid STL draft token")
    return _stl_draft_dir() / f"{draft_token}.stl"


def _stl_draft_name_path(draft_token: str) -> Path:
    if not _SAFE_STL_DRAFT_TOKEN_RE.fullmatch(draft_token):
        raise HTTPException(status_code=400, detail="Invalid STL draft token")
    return _stl_draft_dir() / f"{draft_token}.name"


_STL_DRAFT_MAX_AGE_SECONDS = 24 * 60 * 60  # 24h — matches /tmp/bambu-gateway-previews cleanup cadence


def _sweep_stl_drafts() -> None:
    """Delete `<data>/stl_drafts/*.stl` older than the TTL.

    Best-effort: per-file errors are logged but do not stop the sweep.
    Called from `lifespan` startup so abandoned previews don't accumulate
    across restarts.
    """
    import time as _time

    drafts_dir = config_store._config_path.parent / "stl_drafts"
    if not drafts_dir.exists():
        return
    cutoff = _time.time() - _STL_DRAFT_MAX_AGE_SECONDS
    for path in drafts_dir.iterdir():
        if path.suffix not in (".stl", ".name"):
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError as e:
            logger.warning("Failed to sweep STL draft %s: %s", path, e)


def _stl_scene_with_source_url(scene: dict) -> dict:
    token = str(scene.get("draft_token") or "")
    out = dict(scene)
    if token:
        out["source_url"] = f"/api/stl-drafts/{quote(token, safe='')}/source.stl"
    return out

def _estimate_response_header(estimate: PrintEstimate | None) -> str | None:
    if estimate is None or estimate.is_empty:
        return None
    payload = json.dumps(estimate.model_dump(exclude_none=True)).encode()
    return base64.b64encode(payload).decode()


def _slice_job_to_response(job) -> SliceJobResponse:
    return SliceJobResponse(
        job_id=job.id,
        status=job.status.value,
        progress=job.progress,
        phase=job.phase,
        filename=job.filename,
        printer_id=job.printer_id,
        auto_print=job.auto_print,
        created_at=job.created_at,
        updated_at=job.updated_at,
        estimate=job.estimate,
        settings_transfer=job.settings_transfer,
        output_size=job.output_size,
        error=job.error,
        has_thumbnail=bool(job.thumbnail),
        printed=job.printed,
    )



def _code_fingerprint() -> str:
    """Short hash of the running app source.

    Changes whenever any ``app/**/*.py`` changes, independent of how the
    image was built — so the startup log always reveals exactly which code
    is live, even when no git SHA was baked in at build time. Reproduce
    locally to map it to a commit:

        python -c "from app.main import _code_fingerprint as f; print(f())"
    """
    root = Path(__file__).resolve().parent  # the app/ package dir
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.py")):
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:12]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global printer_service, slicer_client, slice_jobs
    logger.info(
        "Bambu Gateway starting — version=%s sha=%s code=%s",
        app.version,
        os.getenv("GATEWAY_GIT_SHA", "").strip() or "n/a",
        _code_fingerprint(),
    )
    configs = config_store.load()

    async with AsyncExitStack() as stack:
        # Cloud plugin: download before LAN-mode startup so the plugin is on
        # disk before any printer connection is attempted.  Then start the C++
        # host subprocess and keep it alive for the entire lifespan.
        if settings.bambu_cloud_enabled:
            async with httpx.AsyncClient(
                base_url=_bambu_cdn_base_url(settings.bambu_cloud_region),
                timeout=30.0,
            ) as cdn_client:
                downloader = PluginDownloader(
                    plugin_dir=settings.bambu_cloud_plugin_dir,
                    client=cdn_client,
                )
                try:
                    await downloader.ensure_active()
                except Exception as exc:
                    logger.error("Bambu cloud plugin startup failed: %s", exc)
                    raise

            active_dir = settings.bambu_cloud_plugin_dir / "active"
            host = PluginHost(
                cmd=[str(settings.bambu_cloud_host_binary)],
                env={
                    "PJARCZAK_BAMBU_PLUGIN_DIR": str(active_dir),
                    "PJARCZAK_BAMBU_NETWORK_SO": str(
                        active_dir / "libbambu_networking.so"
                    ),
                    "PJARCZAK_BAMBU_SOURCE_SO": str(
                        active_dir / "libBambuSource.so"
                    ),
                    "BAMBU_CLOUD_REGION": settings.bambu_cloud_region,
                },
            )
            await stack.enter_async_context(host)
            boot = await host.call("init_plugin", {})
            if boot.get("bootstrap_rc", -1) != 0:
                raise RuntimeError(f"Bambu plugin bootstrap failed: {boot}")
            app.state.cloud_host = host
            logger.info("Bambu plugin host ready")

            # One CloudPrinterClient per configured printer, keyed by serial.
            cloud_clients: dict[str, CloudPrinterClient] = {
                cfg.serial: CloudPrinterClient(
                    dev_id=cfg.serial,
                    name=cfg.name or f"Printer {cfg.serial[-4:]}",
                )
                for cfg in configs
            }
            app.state.cloud_printers = cloud_clients

            async def _on_message(event: dict) -> None:
                dev_id = event.get("dev_id")
                client = cloud_clients.get(dev_id)
                if client is not None:
                    await client.handle_event(event)

            async def _on_update_status(event: dict) -> None:
                # OnUpdateStatus is per-printer if events carry dev_id; otherwise
                # it belongs to whichever printer has an active submit_print. For
                # v1 we rely on the per-printer single-in-flight invariant: route
                # to WHICHEVER client currently has self._progress != None.
                dev_id = event.get("dev_id")
                if dev_id:
                    client = cloud_clients.get(dev_id)
                    if client is not None:
                        await client.handle_update_status(event)
                    return
                for client in cloud_clients.values():
                    if client._progress is not None:
                        await client.handle_update_status(event)
                        return

            pump = EventPump(host=host, handlers={
                "OnMessage": _on_message,
                "OnUpdateStatus": _on_update_status,
            })
            await pump.start()
            app.state.cloud_event_pump = pump
            stack.push_async_callback(pump.stop)

        # Device registry + APNs
        device_store_path = config_store._config_path.parent / "devices.json"
        device_store = DeviceStore(device_store_path)

        # Slice-job store is also a thumbnail source for Live Activity pushes, so
        # construct it before the notification hub even when no slicer is wired
        # up — an empty store just yields no thumbnails, which is the correct
        # graceful-degradation behavior.
        slice_store_path = config_store._config_path.parent / "slice_jobs.json"
        slice_store = SliceJobStore(slice_store_path)

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
            notification_hub = NotificationHub(
                apns=apns_client,
                device_store=device_store,
                slice_store=slice_store,
            )
            notification_hub.start()
            status_change_callback = notification_hub.on_status_change
            logger.info("APNs push enabled")
        else:
            logger.info("APNs push disabled — set APNS_KEY_PATH and related vars to enable")

        printer_service = PrinterService(
            configs, status_change_callback=status_change_callback,
        )
        printer_service.start()
        # Surface cloud printers through the existing list/status API.
        if settings.bambu_cloud_enabled and hasattr(app.state, "cloud_printers"):
            printer_service.set_cloud_printers(app.state.cloud_printers)
        if notification_hub is not None:
            notification_hub.set_printer_service(printer_service)
        if settings.orcaslicer_api_url:
            slicer_client = SlicerClient(settings.orcaslicer_api_url)

        _sweep_stl_drafts()

        if slicer_client is not None:
            slice_jobs = SliceJobManager(
                store=slice_store,
                slicer=slicer_client,
                printer_service=printer_service,
                notifier=(
                    notification_hub.notify_slice_terminal
                    if notification_hub is not None else None
                ),
                max_concurrent=settings.slice_max_concurrent,
            )
            await slice_jobs.recover_on_startup()
            await slice_jobs.start()

        app.state.device_store = device_store
        app.state.notification_hub = notification_hub
        app.state.apns_client = apns_client

        yield
        if slice_jobs is not None:
            await slice_jobs.stop()
        await printer_service.stop_async()
        if notification_hub is not None:
            notification_hub.stop()
        if apns_client is not None:
            await apns_client.aclose()


app = FastAPI(title="Bambu Gateway", version="2.4.2", lifespan=lifespan)

from app.cloud.auth_routes import router as cloud_auth_router  # noqa: E402
app.include_router(cloud_auth_router)

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
    """Resolve a printer_id, falling back to the default printer.

    Accepts both LAN printers (``printer_service.get_client``) and cloud
    printers (``app.state.cloud_printers``).
    """
    pid = printer_id or printer_service.default_printer_id()
    if pid is None:
        raise HTTPException(status_code=404, detail="No printers configured")
    # Check LAN client first (fast path, backward-compatible with test stubs).
    if printer_service.get_client(pid) is not None:
        return pid
    # Also accept cloud printers when cloud mode is active.
    cloud_printers = getattr(app.state, "cloud_printers", None)
    if cloud_printers and pid in cloud_printers:
        return pid
    raise HTTPException(status_code=404, detail="Printer not found")


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


async def _run_control_command(
    printer_id: str,
    command: str,
    envelope: dict,
    *,
    lan_action,
) -> CommandResponse:
    """Route a control command to cloud or LAN depending on printer type.

    Cloud printers are dispatched via ``PrinterService._dispatch_command`` which
    calls ``CloudPrinterClient.send_command`` and returns the plugin rc.
    LAN printers fall back to the existing synchronous ``lan_action`` path.

    A single branch here replaces per-route ``if cloud`` blocks so each of the
    six control routes stays a 2-3 line body.
    """
    pid = _resolve_printer_id(printer_id)
    cloud_pair = _get_cloud_client(pid)
    if cloud_pair is not None:
        host, cloud_client = cloud_pair
        rc = await printer_service._dispatch_command(pid, envelope, host=host)
        if rc != 0:
            raise HTTPException(
                status_code=502,
                detail=f"Cloud plugin error rc={rc} for command {command}",
            )
        return CommandResponse(printer_id=pid, command=command)
    # LAN path — call the existing synchronous action unchanged.
    try:
        lan_action(pid)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return CommandResponse(printer_id=pid, command=command)


@app.post("/api/printers/{printer_id}/pause", response_model=CommandResponse)
async def pause_print(printer_id: str):
    return await _run_control_command(
        printer_id, "pause", build_pause_command(),
        lan_action=printer_service.pause_print,
    )


@app.post("/api/printers/{printer_id}/resume", response_model=CommandResponse)
async def resume_print(printer_id: str):
    return await _run_control_command(
        printer_id, "resume", build_resume_command(),
        lan_action=printer_service.resume_print,
    )


@app.post("/api/printers/{printer_id}/cancel", response_model=CommandResponse)
async def cancel_print(printer_id: str):
    return await _run_control_command(
        printer_id, "cancel", build_cancel_command(),
        lan_action=printer_service.cancel_print,
    )


@app.post("/api/printers/{printer_id}/speed", response_model=CommandResponse)
async def set_print_speed(printer_id: str, body: SpeedRequest):
    return await _run_control_command(
        printer_id,
        f"speed:{body.level.name}",
        build_speed_command(body.level.value),
        lan_action=lambda pid: printer_service.set_print_speed(pid, body.level.value),
    )


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


@app.get("/api/printers/{printer_id}/camera/status", response_model=CameraStatusResponse)
async def get_camera_status(printer_id: str):
    pid = _resolve_printer_id(printer_id)
    if printer_service.get_status(pid) is None:
        raise HTTPException(status_code=404, detail=f"Printer {pid} not found")
    proxy = printer_service.get_camera_proxy(pid)
    if proxy is None:
        return CameraStatusResponse(state="unsupported", error=None, last_frame_at=None)
    return CameraStatusResponse(**proxy.status())


@app.get("/api/printers/{printer_id}/camera/stream.mjpg")
async def get_camera_stream(printer_id: str):
    pid = _resolve_printer_id(printer_id)
    if printer_service.get_status(pid) is None:
        raise HTTPException(status_code=404, detail=f"Printer {pid} not found")
    proxy = printer_service.get_camera_proxy(pid)
    if proxy is None:
        raise HTTPException(status_code=404, detail="Camera not available for this printer")

    boundary = b"--frame\r\n"

    async def generator():
        try:
            async for jpeg in proxy.subscribe():
                yield (
                    boundary
                    + b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
                    + jpeg
                    + b"\r\n"
                )
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
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
    temp = body.temperature if body else 55
    duration = body.duration_minutes if body else 480
    return await _run_control_command(
        printer_id,
        f"start_drying:ams{ams_id}",
        build_ams_start_drying_command(ams_id, temp, duration),
        lan_action=lambda pid: printer_service.start_drying(pid, ams_id, temp, duration),
    )


@app.post(
    "/api/printers/{printer_id}/ams/{ams_id}/stop-drying",
    response_model=CommandResponse,
)
async def stop_drying(printer_id: str, ams_id: int):
    return await _run_control_command(
        printer_id,
        f"stop_drying:ams{ams_id}",
        build_ams_stop_drying_command(ams_id),
        lan_action=lambda pid: printer_service.stop_drying(pid, ams_id),
    )


@app.post(
    "/api/printers/{printer_id}/ams/auto-refill",
    response_model=CommandResponse,
)
async def set_ams_auto_refill(printer_id: str, body: AmsAutoRefillRequest):
    pid = _resolve_printer_id(printer_id)
    try:
        printer_service.set_ams_auto_refill(pid, body.enabled)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return CommandResponse(
        printer_id=pid,
        command=f"ams_auto_refill:{'on' if body.enabled else 'off'}",
    )


def _first_str(value: object) -> str:
    """Pull a scalar string out of an OrcaSlicer config option that may be a
    list-of-strings, a bare string, or missing entirely. Returns "" on miss."""
    if isinstance(value, list):
        return str(value[0]).strip() if value else ""
    if value is None:
        return ""
    return str(value).strip()


def _first_int(value: object, default: int) -> int:
    raw = _first_str(value)
    if not raw:
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


@app.post(
    "/api/printers/{printer_id}/ams/{ams_id}/tray/{tray_id}/filament",
    response_model=CommandResponse,
)
async def set_ams_filament(
    printer_id: str,
    ams_id: int,
    tray_id: int,
    body: SetAmsFilamentRequest,
):
    """Assign a filament profile to one AMS tray.

    Resolves the slicer profile's `filament_id`, type, default colour, and
    nozzle temperature range, then publishes `ams_filament_setting` over MQTT.
    The printer echoes the new state back over its `pushall` report and the
    cached PrinterStatus picks up the change automatically — clients should
    invalidate their AMS query on success and re-poll.

    `tray_id` is the per-AMS slot index 0..3 (NOT the global slot). Pass
    ams_id=255 / tray_id=254 for the external spool.
    """
    pid = _resolve_printer_id(printer_id)

    if slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="Slicer not configured — ORCASLICER_API_URL is unset.",
        )

    setting_id = body.setting_id.strip()
    if not setting_id:
        raise HTTPException(status_code=400, detail="setting_id is required")

    detail = await slicer_client.get_filament_detail(setting_id)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"Filament profile {setting_id!r} not found in slicer catalog",
        )
    resolved = detail.get("resolved") or {}

    filament_id = _first_str(resolved.get("filament_id"))
    if not filament_id:
        raise HTTPException(
            status_code=422,
            detail=f"Filament profile {setting_id!r} has no filament_id; can't assign to AMS",
        )
    tray_type = _first_str(resolved.get("filament_type")) or "PLA"
    temp_min = _first_int(resolved.get("nozzle_temperature_range_low"), 190)
    temp_max = _first_int(resolved.get("nozzle_temperature_range_high"), 240)

    # Body color wins (caller may want to keep the current spool's colour);
    # otherwise fall back to the profile's default.
    raw_color = (body.tray_color or _first_str(resolved.get("default_filament_colour"))).strip()
    raw_color = raw_color.lstrip("#").upper()
    if len(raw_color) == 6:
        raw_color += "FF"
    if len(raw_color) != 8 or any(c not in "0123456789ABCDEF" for c in raw_color):
        raw_color = "000000FF"

    try:
        printer_service.set_ams_filament(
            pid, ams_id, tray_id,
            tray_info_idx=filament_id,
            tray_color=raw_color,
            tray_type=tray_type,
            nozzle_temp_min=temp_min,
            nozzle_temp_max=temp_max,
            setting_id=setting_id,
            tag_uid=body.tag_uid,
            bed_temp=body.bed_temp,
            tray_weight=body.tray_weight,
            remain=body.remain,
            k=body.k,
            n=body.n,
            tray_uuid=body.tray_uuid,
            cali_idx=body.cali_idx,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return CommandResponse(
        printer_id=pid,
        command=f"set_ams_filament:ams{ams_id}:tray{tray_id}:{filament_id}",
    )


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


def _parse_process_overrides_form(raw: str) -> dict[str, str] | None:
    """Validate and decode the process_overrides form field.

    Returns ``None`` for empty input. Raises ``HTTPException(400)`` on
    malformed input — the slicer is permissive on unknown / unparseable
    keys, but we surface client-side mistakes (bad JSON, wrong shape,
    non-string values) early rather than silently swallowing them.

    The ``isinstance(raw, str)`` guard handles direct in-process route
    invocation (e.g. ``await app_main.print_preview(...)`` from tests),
    where the parameter receives FastAPI's ``Form("")`` sentinel rather
    than the empty-string default a real request would yield.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid process_overrides JSON")
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=400,
            detail="process_overrides must be a JSON object",
        )
    if any(not isinstance(v, str) for v in parsed.values()):
        raise HTTPException(
            status_code=400,
            detail="process_overrides values must be strings",
        )
    return parsed


async def _resolve_slice_filament_payload(
    project_filament_ids: list[str],
    filament_profiles: str,
    printer_id: str = "",
    used_filament_indices: set[int] | None = None,
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
        used_filament_indices=used_filament_indices,
    )



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

    status = printer_service.get_status(pid)
    return AMSResponse(
        printer_id=pid,
        trays=trays,
        units=units,
        vt_tray=vt_tray,
        auto_refill_enabled=(
            status.ams_auto_refill_enabled if status is not None else None
        ),
        auto_refill_supported=(
            status.ams_auto_refill_supported if status is not None else None
        ),
    )


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


@app.get("/api/slicer/options/process")
async def slicer_options_process():
    """Process-option metadata catalogue. Pass-through to the slicer.

    Clients cache by the response's ``version`` field. ~150 KB; the
    gateway does not cache server-side.
    """
    if slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="Slicer not configured: ORCASLICER_API_URL not set",
        )
    try:
        return await slicer_client.get_process_options()
    except SlicingError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/slicer/options/process/layout")
async def slicer_options_process_layout():
    """Allowlist-filtered editor layout. Pass-through to the slicer.

    Clients cache by ``(version, allowlist_revision)``.
    """
    if slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="Slicer not configured: ORCASLICER_API_URL not set",
        )
    try:
        return await slicer_client.get_process_layout()
    except SlicingError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/slicer/processes/{setting_id}")
async def slicer_process_profile(setting_id: str):
    """Pass-through to orcaslicer-headless's per-profile detail endpoint.

    Returns the slicer's resolved flat values for the named process
    profile. Used by the web process-parameter editor to fill the
    ``processBaseline`` rung between 3MF modifications and catalogue
    defaults. Errors (including 404 for an unknown ``setting_id``)
    propagate as 502 via the existing ``SlicingError`` path.
    """
    if slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="Slicer not configured: ORCASLICER_API_URL not set",
        )
    try:
        return await slicer_client.get_process_profile(setting_id)
    except SlicingError as e:
        raise HTTPException(status_code=502, detail=str(e))


class _ResolveForMachineBody(BaseModel):
    machine_id: str
    process_name: str = ""
    filament_names: list[str] = []
    plate_type: str = ""


@app.post("/api/slicer/resolve-for-machine")
async def slicer_resolve_for_machine(body: _ResolveForMachineBody):
    """Forward to the slicer's GUI-equivalent profile resolver.

    Used by the print form to populate process / filament / plate-type
    defaults whenever the user picks a machine, so a 3MF authored for
    printer X can be retargeted to printer Y without manually re-picking
    every same-alias variant.
    """
    if slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="Slicer not configured: ORCASLICER_API_URL not set",
        )
    try:
        return await slicer_client.resolve_for_machine(
            machine_id=body.machine_id,
            process_name=body.process_name,
            filament_names=body.filament_names,
            plate_type=body.plate_type,
        )
    except SlicingError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/parse-3mf")
async def parse_3mf_file(file: UploadFile, plate_id: int | None = None):
    if not file.filename or not file.filename.lower().endswith(".3mf"):
        raise HTTPException(status_code=400, detail="File must be a .3mf file")

    file_data = await file.read()

    if len(file_data) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {settings.max_file_size_mb} MB limit",
        )

    try:
        info = await parse_3mf_via_slicer(
            file_data, slicer_client, plate_id=plate_id,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse 3MF: {e}")

    return info


@app.post("/api/stl-drafts")
async def create_stl_draft(
    file: UploadFile,
    machine_profile: str = Form(...),
    process_profile: str = Form(...),
    plate_type: str = Form(""),
    auto_orient: bool = Form(False),
    arrange: bool = Form(True),
    center: bool = Form(True),
):
    if slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="STL preview not available: ORCASLICER_API_URL not configured",
        )
    if not file.filename or not file.filename.lower().endswith(".stl"):
        raise HTTPException(status_code=400, detail="File must be an STL file")

    file_data = await file.read()
    if len(file_data) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {settings.max_file_size_mb} MB limit",
        )
    if not file_data:
        raise HTTPException(status_code=400, detail="STL file is empty")

    try:
        scene = await slicer_client.import_stl_draft(
            file_data,
            filename=file.filename,
            machine_profile=machine_profile,
            process_profile=process_profile,
            plate_type=plate_type.strip(),
            auto_orient=auto_orient,
            arrange=arrange,
            center=center,
        )
    except SlicingError as e:
        raise HTTPException(status_code=502, detail=f"STL import failed: {e}")

    token = str(scene.get("draft_token") or "")
    if not token:
        raise HTTPException(status_code=502, detail="STL import did not return a draft token")
    _stl_draft_source_path(token).write_bytes(file_data)
    _stl_draft_name_path(token).write_text(file.filename)
    return _stl_scene_with_source_url(scene)


@app.get("/api/stl-drafts/{draft_token}/source.stl")
async def get_stl_draft_source(draft_token: str):
    path = _stl_draft_source_path(draft_token)
    if not path.exists():
        raise HTTPException(status_code=404, detail="STL draft source not found")
    return Response(
        content=path.read_bytes(),
        media_type="model/stl",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.post("/api/stl-drafts/{draft_token}/layout")
async def layout_stl_draft(draft_token: str, body: StlDraftLayoutRequest):
    if slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="STL preview not available: ORCASLICER_API_URL not configured",
        )
    source_path = _stl_draft_source_path(draft_token)
    if not source_path.exists():
        raise HTTPException(
            status_code=410,
            detail="STL draft source no longer available; re-upload to continue",
        )
    try:
        scene = await slicer_client.layout_stl_draft(draft_token, body.action)
    except SlicingError as e:
        raise HTTPException(status_code=502, detail=f"STL layout failed: {e}")
    return _stl_scene_with_source_url(scene)


@app.post("/api/stl-drafts/{draft_token}/3mf", response_model=StlMaterializedProject)
async def materialize_stl_draft(
    draft_token: str,
    body: StlDraftMaterializeRequest | None = None,
):
    if slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="STL preview not available: ORCASLICER_API_URL not configured",
        )
    source_path = _stl_draft_source_path(draft_token)
    if not source_path.exists():
        raise HTTPException(
            status_code=410,
            detail="STL draft source no longer available; re-upload to continue",
        )
    payload = body or StlDraftMaterializeRequest()
    try:
        materialized = await slicer_client.materialize_stl_draft(
            draft_token,
            thumbnail_png_data_url=payload.thumbnail_png_data_url or None,
        )
        info = await parse_3mf_token_via_slicer(
            str(materialized["input_token"]),
            slicer_client,
            plate_id=1,
        )
    except SlicingError as e:
        raise HTTPException(status_code=502, detail=f"STL materialize failed: {e}")
    name_path = _stl_draft_name_path(draft_token)
    try:
        original_filename = name_path.read_text().strip()
    except OSError:
        original_filename = source_path.name
    for path in (source_path, name_path):
        try:
            path.unlink()
        except OSError as e:
            logger.warning("Failed to delete materialized STL draft %s: %s", path, e)
    source_name = original_filename.removesuffix(".stl").removesuffix(".STL") or draft_token
    return StlMaterializedProject(
        input_token=str(materialized["input_token"]),
        filename=f"{source_name}.3mf",
        info=info,
    )


def _get_cloud_client(pid: str):
    """Return (host, CloudPrinterClient) for ``pid`` when cloud mode is active, else None."""
    if not settings.bambu_cloud_enabled:
        return None
    cloud_printers = getattr(app.state, "cloud_printers", None)
    if cloud_printers is None:
        return None
    client = cloud_printers.get(pid)
    if client is None:
        return None
    host = getattr(app.state, "cloud_host", None)
    if host is None:
        return None
    return host, client


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
        state = upload_state.to_dict()
        logger.exception(
            "Background upload failed for %s on printer %s "
            "(%d/%d bytes sent): %s",
            filename,
            printer_id,
            state["bytes_sent"],
            state["total_bytes"],
            e,
        )
        upload_state.fail(str(e))


@app.post("/api/print")
async def print_file(
    file: UploadFile = None,
    printer_id: str = Form(""),
    plate_id: int = Form(0),
    job_id: str = Form(""),
    preview_id: str = Form(""),       # deprecated alias for job_id
    machine_profile: str = Form(""),
    process_profile: str = Form(""),
    filament_profiles: str = Form(""),
    plate_type: str = Form(""),
    slice_only: bool = Form(False),
    process_overrides: str = Form(""),
):
    effective_job_id = job_id or preview_id
    process_overrides_dict = _parse_process_overrides_form(process_overrides)

    # --- Fast path: print from a sliced job ---
    if effective_job_id and slice_jobs is not None:
        job = await slice_jobs.get(effective_job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        # Allow `ready` (first print or reprint after a successful auto-print)
        # and the failure terminals `failed` / `cancelled` (reprint after a
        # botched run). Anything mid-flight is rejected.
        if job.status.value not in ("ready", "failed", "cancelled"):
            raise HTTPException(
                status_code=409,
                detail=f"Job is {job.status.value}, not printable",
            )
        if not job.output_path or not Path(job.output_path).exists():
            raise HTTPException(
                status_code=410,
                detail="Sliced output is gone; re-slice the file",
            )
        pid = printer_id or job.printer_id or printer_service.default_printer_id()
        if pid is None:
            raise HTTPException(status_code=404, detail="No printers configured")

        client = printer_service.get_client(pid)
        if client is None:
            raise HTTPException(status_code=404, detail=f"Printer {pid} not found")
        try:
            client.ensure_connected()
            if not client.get_status().online:
                raise ConnectionError(f"Printer {pid} is offline")
        except ConnectionError as e:
            raise HTTPException(status_code=409, detail=str(e))

        tray_error = await validate_selected_trays(
            job.filament_profiles, pid, printer_service,
        )
        if tray_error is not None:
            raise HTTPException(status_code=409, detail=tray_error)
        logger.info(
            "Print from job id=%s filament_profiles=%s "
            "project_filament_count=%s slot_indices=%s",
            job.id, job.filament_profiles, job.project_filament_count,
            job.slot_indices,
        )
        ams_mapping, use_ams = build_ams_mapping(
            job.filament_profiles,
            project_filament_count=job.project_filament_count,
            slot_indices=job.slot_indices,
        )

        file_data_job = Path(job.output_path).read_bytes()
        upload_state = upload_tracker.create(job.filename, pid, len(file_data_job))
        asyncio.get_running_loop().run_in_executor(None, lambda: _background_submit(
            upload_state, pid, file_data_job, job.filename,
            plate_id=job.plate_id or 1, ams_mapping=ams_mapping, use_ams=use_ams,
        ))

        # Slice-job perspective: handed off to the printer, work is done.
        job.status = SliceJobStatus.READY
        job.printed = True
        await slice_jobs._store.upsert(job)

        return PrintResponse(
            status="uploading",
            file_name=job.filename,
            printer_id=pid,
            was_sliced=True,
            upload_id=upload_state.upload_id,
            estimate=PrintEstimate(**job.estimate) if job.estimate else None,
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
        info = await parse_3mf_via_slicer(
            file_data, slicer_client, plate_id=plate_id or 1,
        )
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
            used_filament_indices={f.index for f in info.filaments if f.used},
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
                process_overrides=process_overrides_dict,
            )
        except SlicingError as e:
            raise HTTPException(status_code=502, detail=f"Slicing failed: {e}")

        file_data = slice_result.content
        was_sliced = True

    # Build settings transfer info if available
    settings_transfer = None
    if slice_result and (
        slice_result.settings_transfer_status
        or slice_result.filament_transfers
        or slice_result.process_overrides_applied
    ):
        settings_transfer = SettingsTransferInfo(
            status=slice_result.settings_transfer_status,
            transferred=[
                TransferredSetting(**s) for s in slice_result.settings_transferred
            ],
            filaments=[
                FilamentTransferEntry(**f) for f in slice_result.filament_transfers
            ],
            process_overrides_applied=[
                ProcessOverrideApplied(**o)
                for o in slice_result.process_overrides_applied
            ],
        )

    # If slice_only, return the sliced file as a download
    if slice_only:
        headers = {
            "Content-Disposition": _attachment_disposition(file.filename),
        }
        estimate_header = _estimate_response_header(
            slice_result.estimate if slice_result else None
        )
        if estimate_header:
            headers["X-Print-Estimate"] = estimate_header
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

    tray_error = await validate_selected_trays(filament_payload, pid, printer_service)
    if tray_error is not None:
        raise HTTPException(status_code=409, detail=tray_error)
    ams_mapping, use_ams = build_ams_mapping(
        filament_payload,
        project_filament_count=len(info.filaments) or None,
        slot_indices=[f.index for f in info.filaments] or None,
    )
    logger.info(
        "Print submission: filament_payload=%s ams_mapping=%s use_ams=%s filament_count=%s",
        filament_payload, ams_mapping, use_ams, len(info.filaments),
    )

    fname = file.filename
    p_id = plate_id or 1

    # --- Cloud path ---
    cloud_pair = _get_cloud_client(pid)
    if cloud_pair is not None:
        cloud_host, cloud_client = cloud_pair
        # Write sliced bytes to a temp file so the plugin can upload from disk.
        import tempfile, os as _os
        suffix = Path(fname).suffix or ".3mf"
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, prefix="bambu_cloud_"
        ) as tmp:
            tmp.write(file_data)
            tmp_path = tmp.name
        try:
            project_name = Path(fname).stem
            ams_mapping_dict: dict[str, str] | None = None
            if ams_mapping:
                ams_mapping_dict = {str(i): str(v) for i, v in enumerate(ams_mapping)}
            print_params = build_print_params(
                dev_id=pid,
                project_name=project_name,
                plate_index=p_id,
                gcode_3mf_path=tmp_path,
                ams_mapping=ams_mapping_dict,
                use_ams=use_ams,
            )
            # Consume the async generator to completion (non-streaming endpoint).
            last_error: dict | None = None
            async for frame in cloud_client.submit_print(
                host=cloud_host, print_params=print_params,
            ):
                if frame.get("event") == "error":
                    last_error = frame
                    break
                if frame.get("event") == "done":
                    break
        finally:
            try:
                _os.unlink(tmp_path)
            except OSError:
                pass

        if last_error is not None:
            raise HTTPException(
                status_code=502,
                detail=last_error.get("msg", "Cloud print submission failed"),
            )
        return PrintResponse(
            status="printing",
            file_name=fname,
            printer_id=pid,
            was_sliced=was_sliced,
            settings_transfer=settings_transfer,
            estimate=slice_result.estimate if slice_result else None,
        )

    # --- LAN path ---
    # Validate printer is reachable before starting background upload.
    client = printer_service.get_client(pid)
    if client is None:
        raise HTTPException(status_code=404, detail=f"Printer {pid} not found")
    try:
        client.ensure_connected()
        if not client.get_status().online:
            raise ConnectionError(f"Printer {pid} is offline")
    except ConnectionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # Sliced output preserves the source plate id: `Metadata/plate_{N}.gcode`
    # where N is the plate_id we passed to /slice/v2. Unsliced uploads use
    # the user-supplied plate_id (or 1 when omitted).
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
        estimate=slice_result.estimate if slice_result else None,
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
    process_overrides: str = Form(""),
    copies: int = Form(1),
):
    """Slice synchronously via the job manager and return the sliced bytes.

    Kept for backward compat with iOS clients that still use the sync
    preview endpoint. Internally a job with auto_print=false; we wait
    for terminal state.
    """
    if slice_jobs is None or slicer_client is None:
        raise HTTPException(status_code=400, detail="Slicing not available")
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
    if not (1 <= copies <= 100):
        raise HTTPException(
            status_code=400,
            detail=f"copies must be between 1 and 100 (got {copies})",
        )
    process_overrides_dict = _parse_process_overrides_form(process_overrides)
    try:
        info = await parse_3mf_via_slicer(
            file_data, slicer_client, plate_id=plate_id or 1,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse 3MF: {e}")

    filament_payload, filament_error = await _resolve_slice_filament_payload(
        [f.setting_id for f in info.filaments],
        filament_profiles,
        printer_id,
        used_filament_indices={f.index for f in info.filaments if f.used},
    )
    if filament_error is not None or filament_payload is None:
        raise HTTPException(status_code=400, detail=filament_error)

    job = await slice_jobs.submit(
        file_data=file_data,
        filename=file.filename,
        machine_profile=machine_profile,
        process_profile=process_profile,
        filament_profiles=filament_payload,
        plate_id=plate_id,
        plate_type=plate_type.strip(),
        project_filament_count=len(info.filaments),
        slot_indices=[f.index for f in info.filaments] or None,
        printer_id=printer_id or None,
        auto_print=False,
        process_overrides=process_overrides_dict,
        copies=copies,
    )

    # Wait for terminal state (bounded — preview is meant to be sync-ish).
    deadline = asyncio.get_event_loop().time() + 600
    cur = None
    while asyncio.get_event_loop().time() < deadline:
        cur = await slice_jobs.get(job.id)
        if cur is None:
            raise HTTPException(status_code=500, detail="Job disappeared")
        if cur.status.is_terminal:
            break
        await asyncio.sleep(0.2)
    else:
        raise HTTPException(status_code=504, detail="Slicing timed out")

    if cur.status.value != "ready":
        raise HTTPException(
            status_code=502,
            detail=f"Slicing {cur.status.value}: {cur.error or 'no result'}",
        )

    output_bytes = Path(cur.output_path).read_bytes()
    headers = {
        "Content-Disposition": _attachment_disposition(file.filename),
        "X-Preview-Id": cur.id,           # backward compat
        "X-Job-Id": cur.id,
    }
    if cur.estimate:
        headers["X-Print-Estimate"] = base64.b64encode(
            json.dumps(cur.estimate).encode(),
        ).decode()
    if cur.settings_transfer:
        if cur.settings_transfer.get("status"):
            headers["X-Settings-Transfer-Status"] = cur.settings_transfer["status"]
        if cur.settings_transfer.get("transferred"):
            headers["X-Settings-Transferred"] = json.dumps(
                cur.settings_transfer["transferred"]
            )
        if cur.settings_transfer.get("filaments"):
            headers["X-Filament-Settings-Transferred"] = json.dumps(
                cur.settings_transfer["filaments"]
            )

    return Response(
        content=output_bytes,
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
    process_overrides: str = Form(""),
    copies: int = Form(1),
):
    """Slice and optionally print a 3MF, streaming progress via SSE.

    Implemented as a thin wrapper over the slice-job manager: creates a job,
    then tails its progress until terminal. Output bytes for slice_only /
    preview are read from the job's output blob and base64-encoded into the
    final `result` SSE event for backward compat with existing clients.
    """
    if slice_jobs is None or slicer_client is None:
        raise HTTPException(status_code=400, detail="Slicing not available")
    if not file.filename or not file.filename.lower().endswith(".3mf"):
        raise HTTPException(status_code=400, detail="File must be a .3mf file")
    if not machine_profile or not process_profile:
        raise HTTPException(
            status_code=400,
            detail="machine_profile and process_profile are required",
        )

    file_data = await file.read()
    if len(file_data) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {settings.max_file_size_mb} MB limit",
        )
    if not (1 <= copies <= 100):
        raise HTTPException(
            status_code=400,
            detail=f"copies must be between 1 and 100 (got {copies})",
        )
    process_overrides_dict = _parse_process_overrides_form(process_overrides)
    try:
        info = await parse_3mf_via_slicer(
            file_data, slicer_client, plate_id=plate_id or 1,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse 3MF: {e}")

    filament_payload, filament_error = await _resolve_slice_filament_payload(
        [f.setting_id for f in info.filaments],
        filament_profiles,
        printer_id,
        used_filament_indices={f.index for f in info.filaments if f.used},
    )
    if filament_error is not None or filament_payload is None:
        raise HTTPException(status_code=400, detail=filament_error)

    auto_print = not slice_only and not preview
    pid = printer_id or printer_service.default_printer_id() or ""
    if auto_print and not pid:
        raise HTTPException(status_code=404, detail="No printers configured")

    # For cloud printers with auto_print, slice first (auto_print=False),
    # then dispatch to CloudPrinterClient.submit_print once the job is ready.
    cloud_pair = _get_cloud_client(pid) if (auto_print and pid) else None
    job_auto_print = auto_print and cloud_pair is None

    job = await slice_jobs.submit(
        file_data=file_data,
        filename=file.filename,
        machine_profile=machine_profile,
        process_profile=process_profile,
        filament_profiles=filament_payload,
        plate_id=plate_id,
        plate_type=plate_type.strip(),
        project_filament_count=len(info.filaments),
        slot_indices=[f.index for f in info.filaments] or None,
        printer_id=pid or None,
        auto_print=job_auto_print,
        process_overrides=process_overrides_dict,
        copies=copies,
    )

    async def generate():
        last_progress = -1
        last_status = None
        while True:
            cur = await slice_jobs.get(job.id)
            if cur is None:
                yield _sse_event("error", {"error": "Job disappeared"})
                yield _sse_event("done", {})
                return

            if cur.progress != last_progress:
                last_progress = cur.progress
                yield _sse_event("progress", {"percent": cur.progress})

            if cur.status.value != last_status:
                last_status = cur.status.value
                yield _sse_event("status", {
                    "phase": cur.phase or cur.status.value,
                    "message": cur.status.value,
                })

            if cur.status.is_terminal:
                if cur.status.value == "failed":
                    yield _sse_event("error", {"error": cur.error or "Failed"})
                    yield _sse_event("done", {"job_id": cur.id})
                    return
                else:
                    payload = {}
                    if (slice_only or preview) and cur.output_path:
                        out_bytes = Path(cur.output_path).read_bytes()
                        payload["file_base64"] = base64.b64encode(out_bytes).decode()
                        payload["file_size"] = len(out_bytes)
                    if cur.estimate:
                        payload["estimate"] = cur.estimate
                    if cur.settings_transfer:
                        payload["settings_transfer"] = cur.settings_transfer
                    if preview:
                        payload["preview_id"] = cur.id  # backward-compat alias
                    yield _sse_event("result", payload)
                    if job_auto_print and cur.printed:
                        yield _sse_event("print_started", {
                            "printer_id": cur.printer_id,
                            "file_name": cur.filename,
                            "settings_transfer": cur.settings_transfer,
                            "estimate": cur.estimate,
                        })

                    # Cloud auto-print: slice is done, now submit to cloud.
                    if cloud_pair is not None and not slice_only and not preview:
                        cloud_host, cloud_client = cloud_pair
                        import tempfile, os as _os
                        suffix = Path(file.filename).suffix or ".3mf"
                        output_bytes = Path(cur.output_path).read_bytes() if cur.output_path else b""
                        with tempfile.NamedTemporaryFile(
                            delete=False, suffix=suffix, prefix="bambu_cloud_"
                        ) as tmp:
                            tmp.write(output_bytes)
                            tmp_path = tmp.name
                        try:
                            ams_map_inner, use_ams_inner = build_ams_mapping(
                                cur.filament_profiles,
                                project_filament_count=cur.project_filament_count,
                                slot_indices=cur.slot_indices,
                            )
                            ams_mapping_dict: dict[str, str] | None = None
                            if ams_map_inner:
                                ams_mapping_dict = {str(i): str(v) for i, v in enumerate(ams_map_inner)}
                            print_params = build_print_params(
                                dev_id=pid,
                                project_name=Path(file.filename).stem,
                                plate_index=cur.plate_id or 1,
                                gcode_3mf_path=tmp_path,
                                ams_mapping=ams_mapping_dict,
                                use_ams=use_ams_inner,
                            )
                            yield _sse_event("status", {"phase": "submitting", "message": "submitting"})
                            async for frame in cloud_client.submit_print(
                                host=cloud_host, print_params=print_params,
                            ):
                                ev = frame.get("event", "")
                                if ev == "progress":
                                    yield _sse_event("status", {
                                        "phase": "uploading",
                                        "message": frame.get("msg", ""),
                                    })
                                elif ev == "done":
                                    yield _sse_event("print_started", {
                                        "printer_id": pid,
                                        "file_name": cur.filename,
                                        "settings_transfer": cur.settings_transfer,
                                        "estimate": cur.estimate,
                                    })
                                    break
                                elif ev == "error":
                                    yield _sse_event("error", {
                                        "error": frame.get("msg", "Cloud print failed"),
                                    })
                                    break
                        finally:
                            try:
                                _os.unlink(tmp_path)
                            except OSError:
                                pass

                yield _sse_event("done", {"job_id": cur.id})
                return
            await asyncio.sleep(0.2)

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


# --- Async slice jobs ---

class _ClearBody(BaseModel):
    statuses: list[str] | None = None


_DEFAULT_CLEAR_STATUSES = {"ready", "failed", "cancelled"}


@app.post("/api/slice-jobs", response_model=SliceJobResponse, status_code=202)
async def create_slice_job(
    file: UploadFile | None = None,
    input_token: str = Form(""),
    source_filename: str = Form(""),
    thumbnail_png_data_url: str = Form(""),
    machine_profile: str = Form(...),
    process_profile: str = Form(...),
    filament_profiles: str = Form(...),
    plate_id: int = Form(0),
    plate_type: str = Form(""),
    printer_id: str = Form(""),
    auto_print: bool = Form(False),
    process_overrides: str = Form(""),
    copies: int = Form(1),
):
    if slice_jobs is None or slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="Slicing not available: ORCASLICER_API_URL not configured",
        )
    if not file and not input_token:
        raise HTTPException(status_code=400, detail="file or input_token is required")
    if file and input_token:
        raise HTTPException(status_code=400, detail="Provide file or input_token, not both")
    if file and (not file.filename or not file.filename.lower().endswith(".3mf")):
        raise HTTPException(status_code=400, detail="File must be a .3mf file")
    if input_token and not source_filename.lower().endswith(".3mf"):
        raise HTTPException(status_code=400, detail="source_filename must be a .3mf file")
    if not (1 <= copies <= 100):
        raise HTTPException(
            status_code=400,
            detail=f"copies must be between 1 and 100 (got {copies})",
        )

    if file is not None:
        file_data = await file.read()
        if len(file_data) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"File exceeds {settings.max_file_size_mb} MB limit",
            )
        filename = file.filename or "input.3mf"
        try:
            info = await parse_3mf_via_slicer(
                file_data, slicer_client, plate_id=plate_id or 1,
            )
            upload = await slicer_client.upload_3mf(file_data, filename=filename)
            source_token = str(upload["token"])
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Failed to parse 3MF: {e}")
    else:
        filename = source_filename
        source_token = input_token.strip()
        try:
            info = await parse_3mf_token_via_slicer(
                source_token, slicer_client, plate_id=plate_id or 1,
            )
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Failed to parse 3MF token: {e}")

    if auto_print and not printer_id:
        raise HTTPException(
            status_code=400,
            detail="printer_id is required when auto_print=true",
        )

    process_overrides_dict = _parse_process_overrides_form(process_overrides)

    # Validate + normalize filament selections the same way /api/print-stream
    # and /api/print-preview do, so missing setting_ids or unavailable AMS
    # tray slots are surfaced here instead of as opaque slicer 400s.
    filament_payload, filament_error = await _resolve_slice_filament_payload(
        [f.setting_id for f in info.filaments],
        filament_profiles,
        printer_id,
        used_filament_indices={f.index for f in info.filaments if f.used},
    )
    if filament_error is not None or filament_payload is None:
        raise HTTPException(
            status_code=400,
            detail=filament_error or "filament_profiles must be valid JSON",
        )

    # Decide recentering from the ORIGINAL file. `prepare` below rewrites the
    # project's authored printer to the target machine, so deriving this from
    # the prepared file would always read "same printer" and leave a
    # cross-printer retarget off-plate. Compute it here, while source_token
    # still carries the authored printer, and pass it through explicitly.
    auto_center = await slicer_client.should_auto_center_for_machine(
        source_token, machine_profile,
    )

    try:
        prepared = await slicer_client.prepare_3mf_token(
            source_token,
            machine_profile=machine_profile,
            process_profile=process_profile,
            plate_type=plate_type.strip(),
            process_overrides=process_overrides_dict,
            thumbnail_png_data_url=thumbnail_png_data_url or None,
        )
    except SlicingError as e:
        raise HTTPException(status_code=502, detail=f"Failed to prepare 3MF: {e}")
    prepared_file_data = prepared["content"]

    job = await slice_jobs.submit(
        file_data=prepared_file_data,
        filename=filename,
        machine_profile=machine_profile,
        process_profile=process_profile,
        filament_profiles=filament_payload,
        plate_id=plate_id,
        plate_type=plate_type.strip(),
        project_filament_count=len(info.filaments),
        slot_indices=[f.index for f in info.filaments] or None,
        printer_id=printer_id or None,
        auto_print=auto_print,
        process_overrides=process_overrides_dict,
        copies=copies,
        auto_center=auto_center,
    )
    return _slice_job_to_response(job)


@app.get("/api/slice-jobs", response_model=SliceJobListResponse)
async def list_slice_jobs():
    if slice_jobs is None:
        return SliceJobListResponse(jobs=[])
    jobs = await slice_jobs.list()
    return SliceJobListResponse(
        jobs=[_slice_job_to_response(j) for j in jobs],
    )


@app.post("/api/slice-jobs/clear", response_model=SliceJobListResponse)
async def clear_slice_jobs(body: _ClearBody | None = None):
    if slice_jobs is None:
        return SliceJobListResponse(jobs=[])
    targets = set(body.statuses) if body and body.statuses else _DEFAULT_CLEAR_STATUSES
    deleted = []
    for job in await slice_jobs.list():
        if job.status.value in targets and job.status.is_terminal:
            await slice_jobs._store.delete(job.id)
            deleted.append(_slice_job_to_response(job))
    return SliceJobListResponse(jobs=deleted)


@app.get("/api/slice-jobs/{job_id}", response_model=SliceJobResponse)
async def get_slice_job(job_id: str):
    if slice_jobs is None:
        raise HTTPException(status_code=404, detail="Slice jobs disabled")
    job = await slice_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _slice_job_to_response(job)


@app.get("/api/slice-jobs/{job_id}/thumbnail")
async def get_slice_job_thumbnail(job_id: str):
    """Return the sliced 3MF's plate thumbnail as a PNG."""
    if slice_jobs is None:
        raise HTTPException(status_code=404, detail="Slice jobs disabled")
    job = await slice_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.thumbnail:
        raise HTTPException(status_code=404, detail="No thumbnail available")
    # `thumbnail` is stored as a `data:image/png;base64,...` URL — pull the
    # bytes back out so the browser can cache the response by URL.
    prefix, _, data_b64 = job.thumbnail.partition(",")
    if not data_b64:
        raise HTTPException(status_code=500, detail="Malformed thumbnail")
    media_type = "image/png"
    if prefix.startswith("data:") and ";" in prefix:
        media_type = prefix[5:].split(";", 1)[0] or media_type
    try:
        data = base64.b64decode(data_b64)
    except (ValueError, TypeError):
        raise HTTPException(status_code=500, detail="Malformed thumbnail")
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


def _attachment_disposition(filename: str) -> str:
    """Build an RFC 5987 `Content-Disposition` so non-ASCII filenames don't
    blow up Starlette's latin-1 header encoder. Old clients fall back to the
    `_`-substituted ASCII form; modern ones read the UTF-8 `filename*` field."""
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    encoded = quote(filename, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


@app.get("/api/slice-jobs/{job_id}/input")
async def get_slice_job_input(job_id: str):
    """Download the original (pre-slice) 3MF bytes the user uploaded."""
    if slice_jobs is None:
        raise HTTPException(status_code=404, detail="Slice jobs disabled")
    job = await slice_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.input_path or not Path(job.input_path).exists():
        raise HTTPException(status_code=410, detail="Input blob is gone")
    input_bytes = Path(job.input_path).read_bytes()
    return Response(
        content=input_bytes,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": _attachment_disposition(job.filename),
            "X-Job-Id": job.id,
        },
    )


@app.get("/api/slice-jobs/{job_id}/output")
async def get_slice_job_output(job_id: str):
    """Download the sliced 3MF bytes for a job in `ready` state."""
    if slice_jobs is None:
        raise HTTPException(status_code=404, detail="Slice jobs disabled")
    job = await slice_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status.value != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Job is {job.status.value}, no output available",
        )
    if not job.output_path or not Path(job.output_path).exists():
        raise HTTPException(status_code=410, detail="Output blob is gone")

    output_bytes = Path(job.output_path).read_bytes()
    headers = {
        "Content-Disposition": _attachment_disposition(job.filename),
        "X-Job-Id": job.id,
        "X-Preview-Id": job.id,
    }
    if job.estimate:
        headers["X-Print-Estimate"] = base64.b64encode(
            json.dumps(job.estimate).encode(),
        ).decode()
    if job.settings_transfer:
        if job.settings_transfer.get("status"):
            headers["X-Settings-Transfer-Status"] = job.settings_transfer["status"]
        if job.settings_transfer.get("transferred"):
            headers["X-Settings-Transferred"] = json.dumps(
                job.settings_transfer["transferred"]
            )
        if job.settings_transfer.get("filaments"):
            headers["X-Filament-Settings-Transferred"] = json.dumps(
                job.settings_transfer["filaments"]
            )
    return Response(
        content=output_bytes,
        media_type="application/octet-stream",
        headers=headers,
    )


@app.post("/api/slice-jobs/{job_id}/cancel", response_model=SliceJobResponse)
async def cancel_slice_job(job_id: str):
    if slice_jobs is None:
        raise HTTPException(status_code=404, detail="Slice jobs disabled")
    job = await slice_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    await slice_jobs.cancel(job_id)
    job = await slice_jobs.get(job_id)
    return _slice_job_to_response(job)


@app.delete("/api/slice-jobs/{job_id}", status_code=204)
async def delete_slice_job(job_id: str):
    if slice_jobs is None:
        raise HTTPException(status_code=404, detail="Slice jobs disabled")
    job = await slice_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.status.is_terminal:
        await slice_jobs.cancel(job_id)
    # Wait briefly for cancel to settle
    for _ in range(20):
        cur = await slice_jobs.get(job_id)
        if cur is None or cur.status.is_terminal:
            break
        await asyncio.sleep(0.05)
    await slice_jobs._store.delete(job_id)


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
