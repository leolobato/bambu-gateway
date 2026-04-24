// Mirrors app/models.py — keep field names in sync with the backend.
// No camelCase conversion: we cast JSON straight onto these types.

export type PrinterState =
  | 'offline'
  | 'idle'
  | 'preparing'
  | 'printing'
  | 'paused'
  | 'finished'
  | 'cancelled'
  | 'error';

export type SpeedLevel = 1 | 2 | 3 | 4; // silent / standard / sport / ludicrous

export type AMSTypeName = 'standard' | 'lite' | 'pro' | 'ht';

export interface TemperatureInfo {
  nozzle_temp: number;
  nozzle_target: number;
  bed_temp: number;
  bed_target: number;
}

export interface PrintJob {
  file_name: string;
  progress: number;
  remaining_minutes: number;
  current_layer: number;
  total_layers: number;
}

export interface HMSCode {
  attr: string;
  code: string;
}

export interface ChamberLightInfo {
  supported: boolean;
  on: boolean | null;
}

export interface CameraInfo {
  ip: string;
  access_code: string;
  transport: string;
  chamber_light: ChamberLightInfo | null;
}

export interface PrinterStatus {
  id: string;
  name: string;
  machine_model: string;
  online: boolean;
  state: PrinterState;
  stg_cur: number;
  stage_name: string | null;
  stage_category: string | null;
  speed_level: number;          // 0 = unknown; otherwise SpeedLevel
  active_tray: number | null;
  temperatures: TemperatureInfo;
  job: PrintJob | null;
  hms_codes: HMSCode[];
  print_error: number;
  error_message: string | null;
  camera: CameraInfo | null;
}

export interface PrinterListResponse {
  printers: PrinterStatus[];
}

export interface SlicerFilament {
  name: string;
  filament_id: string;
  setting_id: string;
}

export interface AMSTray {
  slot: number;
  ams_id: number;
  tray_id: number;
  tray_type: string;
  tray_color: string;            // "RRGGBBAA" or ""
  tray_sub_brands: string;
  filament_id: string;
  tray_uuid: string;
  tag_uid: string;
  nozzle_temp_min: string;
  nozzle_temp_max: string;
  bed_temp: string;
  remain: number;                // -1 if unknown
  tray_weight: string;
  matched_filament: SlicerFilament | null;
}

export interface AMSUnit {
  id: number;
  humidity: number;              // -1 if unknown
  temperature: number;
  tray_count: number;
  hw_version: string;
  ams_type: AMSTypeName | null;
  supports_drying: boolean;
  max_drying_temp: number;
  dry_time_remaining: number;
}

export interface AMSResponse {
  printer_id: string;
  trays: AMSTray[];
  units: AMSUnit[];
  vt_tray: AMSTray | null;
}
