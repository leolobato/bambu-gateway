"""Pydantic data models for printer state and API responses."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class PrinterState(str, Enum):
    """High-level printer state derived from MQTT fields.

    Uses ``gcode_state``, ``stg_cur``, and ``layer_num`` for granular
    state derivation (see :func:`preparation_stages.determine_state`).
    """

    offline = "offline"
    idle = "idle"
    preparing = "preparing"
    printing = "printing"
    paused = "paused"
    finished = "finished"
    cancelled = "cancelled"
    error = "error"


class SpeedLevel(int, Enum):
    """Print speed levels supported by Bambu Lab printers."""

    silent = 1
    standard = 2
    sport = 3
    ludicrous = 4


class AMSType(str, Enum):
    """AMS hardware type, detected from ``hw_ver`` in MQTT data."""

    standard = "standard"  # AMS08 — original AMS
    lite = "lite"  # AMS_F1 — AMS Lite (no humidity sensor)
    pro = "pro"  # N3F05 — AMS 2 Pro
    ht = "ht"  # N3S05 — AMS HT (high temperature)

    @classmethod
    def from_hw_version(cls, hw_version: str) -> "AMSType":
        if hw_version.startswith("AMS_F1"):
            return cls.lite
        if hw_version.startswith("N3F"):
            return cls.pro
        if hw_version.startswith("N3S"):
            return cls.ht
        return cls.standard

    @property
    def supports_drying(self) -> bool:
        return self not in (AMSType.standard, AMSType.lite)

    @property
    def has_humidity_sensor(self) -> bool:
        return self not in (AMSType.lite,)

    @property
    def max_drying_temp(self) -> int:
        if self == AMSType.pro:
            return 65
        if self == AMSType.ht:
            return 85
        return 55

    @property
    def display_name(self) -> str:
        if self == AMSType.lite:
            return "AMS Lite"
        if self == AMSType.pro:
            return "AMS 2 Pro"
        if self == AMSType.ht:
            return "AMS HT"
        return "AMS"


class TemperatureInfo(BaseModel):
    """Current and target temperatures for nozzle and bed."""

    nozzle_temp: float = 0.0
    nozzle_target: float = 0.0
    bed_temp: float = 0.0
    bed_target: float = 0.0


class PrintJob(BaseModel):
    """Information about the current/last print job."""

    file_name: str = ""
    progress: int = 0
    remaining_minutes: int = 0
    current_layer: int = 0
    total_layers: int = 0


class HMSCode(BaseModel):
    """A Bambu Health Monitoring System error code."""
    attr: str  # hex, e.g. "0300_2000_0001_0001"
    code: str  # severity/category hex


class PrinterStatus(BaseModel):
    """Full status snapshot for a single printer."""

    id: str
    name: str
    machine_model: str = ""
    online: bool = False
    state: PrinterState = PrinterState.offline
    stg_cur: int = -1
    stage_name: str | None = None
    stage_category: str | None = None
    speed_level: int = 0
    active_tray: int | None = None
    temperatures: TemperatureInfo = TemperatureInfo()
    job: PrintJob | None = None
    hms_codes: list[HMSCode] = []


# --- API response models ---


class HealthResponse(BaseModel):
    status: str = "ok"


class CommandResponse(BaseModel):
    """Generic response for printer control commands."""

    status: str = "ok"
    printer_id: str
    command: str


class PrinterListResponse(BaseModel):
    printers: list[PrinterStatus]


class PrinterDetailResponse(BaseModel):
    printer: PrinterStatus


# --- AMS models ---


class SlicerFilament(BaseModel):
    """A filament profile from the OrcaSlicer CLI API."""

    name: str
    filament_id: str
    setting_id: str


class AMSTray(BaseModel):
    """A single AMS tray with all fields reported by the printer."""

    model_config = {"extra": "allow"}

    slot: int
    ams_id: int
    tray_id: int
    tray_type: str = ""
    tray_color: str = ""
    tray_sub_brands: str = ""
    filament_id: str = ""
    tray_uuid: str = ""
    tag_uid: str = ""
    nozzle_temp_min: str = ""
    nozzle_temp_max: str = ""
    bed_temp: str = ""
    remain: int = -1
    tray_weight: str = ""
    matched_filament: SlicerFilament | None = None


class AMSUnit(BaseModel):
    """A single AMS unit with its trays and environmental data."""

    id: int
    humidity: int = -1
    temperature: float = 0.0
    tray_count: int = 0
    hw_version: str = ""
    ams_type: AMSType | None = None
    supports_drying: bool = False
    max_drying_temp: int = 55
    dry_time_remaining: int = 0


class AMSResponse(BaseModel):
    printer_id: str
    trays: list[AMSTray]
    units: list[AMSUnit] = []
    vt_tray: AMSTray | None = None


# --- Filament matching models ---


class FilamentMatchReason(str, Enum):
    exact_filament_id = "exact_filament_id"
    type_fallback = "type_fallback"
    none = "none"


class ProjectFilamentMatch(BaseModel):
    index: int
    setting_id: str = ""
    type: str = ""
    color: str = ""
    resolved_profile: SlicerFilament | None = None
    preferred_tray_slot: int | None = None
    match_reason: FilamentMatchReason = FilamentMatchReason.none


class FilamentMatchRequest(BaseModel):
    printer_id: str = ""
    filaments: list["FilamentInfo"]


class FilamentMatchResponse(BaseModel):
    printer_id: str
    matches: list[ProjectFilamentMatch]


# --- 3MF parse result models ---


class PlateObject(BaseModel):
    id: str
    name: str


class PlateInfo(BaseModel):
    id: int
    name: str = ""
    objects: list[PlateObject] = []
    thumbnail: str = ""


class FilamentInfo(BaseModel):
    index: int
    type: str = ""
    color: str = ""
    setting_id: str = ""


class PrinterInfo(BaseModel):
    printer_settings_id: str = ""
    printer_model: str = ""
    nozzle_diameter: str = ""


class PrintProfileInfo(BaseModel):
    print_settings_id: str = ""
    layer_height: str = ""


class ThreeMFInfo(BaseModel):
    plates: list[PlateInfo] = []
    filaments: list[FilamentInfo] = []
    print_profile: PrintProfileInfo = PrintProfileInfo()
    printer: PrinterInfo = PrinterInfo()
    has_gcode: bool = False


# --- API response models ---


class TransferredSetting(BaseModel):
    key: str
    value: str
    original: str | None = None


class FilamentTransferEntry(BaseModel):
    slot: int
    original_filament: str
    selected_filament: str
    status: str  # "applied", "filament_changed", "no_customizations"
    transferred: list[TransferredSetting] = []
    discarded: list[str] = []


class SettingsTransferInfo(BaseModel):
    status: str
    transferred: list[TransferredSetting] = []
    filaments: list[FilamentTransferEntry] = []


class PrintResponse(BaseModel):
    status: str
    file_name: str
    printer_id: str
    was_sliced: bool = False
    settings_transfer: SettingsTransferInfo | None = None
    upload_id: str | None = None


# --- Settings API models ---


class PrinterConfigInput(BaseModel):
    """Input model for creating/updating a printer config."""

    serial: str = ""
    ip: str
    access_code: str = ""
    name: str = ""
    machine_model: str = ""


class PrinterConfigResponse(BaseModel):
    """Single printer config (access_code omitted)."""

    serial: str
    ip: str
    name: str
    machine_model: str = ""


class PrinterConfigListResponse(BaseModel):
    """List of printer configs."""

    printers: list[PrinterConfigResponse]


# --- Command request models ---


class SpeedRequest(BaseModel):
    """Request body for setting print speed."""

    level: SpeedLevel


class StartDryingRequest(BaseModel):
    """Request body for starting AMS filament drying."""

    temperature: int = 55
    duration_minutes: int = 480


# --- Push / Live Activity models ---


class CapabilitiesResponse(BaseModel):
    push: bool
    live_activities: bool


class DeviceRegisterRequest(BaseModel):
    id: str
    name: str = ""
    device_token: str
    live_activity_start_token: str | None = None
    subscribed_printers: list[str] = ["*"]


class DeviceRegisterResponse(BaseModel):
    status: str = "ok"


class ActivityRegisterRequest(BaseModel):
    printer_id: str
    activity_update_token: str


class ActivityRegisterResponse(BaseModel):
    status: str = "ok"


class DeviceInfo(BaseModel):
    """Sanitized device record for the web UI — never includes raw tokens."""

    id: str
    name: str
    has_device_token: bool
    has_live_activity_start_token: bool
    active_activity_count: int
    subscribed_printers: list[str]
    registered_at: str
    last_seen_at: str


class DeviceListResponse(BaseModel):
    devices: list[DeviceInfo]


class TestPushResponse(BaseModel):
    status: str  # "ok" | "failed"
    detail: str = ""
