"""Pydantic data models for printer state and API responses."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class PrinterState(str, Enum):
    """High-level printer state derived from the MQTT ``gcode_state`` field.

    Mapping from raw values:
        IDLE     -> idle
        RUNNING  -> printing
        PAUSE    -> paused
        FINISH   -> finished
        (other)  -> error
        (no MQTT)-> offline
    """

    offline = "offline"
    idle = "idle"
    printing = "printing"
    paused = "paused"
    finished = "finished"
    error = "error"


GCODE_STATE_MAP: dict[str, PrinterState] = {
    "IDLE": PrinterState.idle,
    "RUNNING": PrinterState.printing,
    "PAUSE": PrinterState.paused,
    "FINISH": PrinterState.finished,
}


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


class PrinterStatus(BaseModel):
    """Full status snapshot for a single printer."""

    id: str
    name: str
    machine_model: str = ""
    online: bool = False
    state: PrinterState = PrinterState.offline
    temperatures: TemperatureInfo = TemperatureInfo()
    job: PrintJob | None = None


# --- API response models ---


class HealthResponse(BaseModel):
    status: str = "ok"


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


class SettingsTransferInfo(BaseModel):
    status: str
    transferred: list[TransferredSetting] = []


class PrintResponse(BaseModel):
    status: str
    file_name: str
    printer_id: str
    was_sliced: bool = False
    settings_transfer: SettingsTransferInfo | None = None


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
