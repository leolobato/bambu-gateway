// Mirrors app/models.py — keep field names in sync with the backend.
// No camelCase conversion: we cast JSON straight onto these types.

import type { ProcessModifications, ProcessOverrideApplied } from '@/lib/process/types';

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

// --- 3MF parse models (mirror app/models.py 3MF parse section) ---

export interface PlateObject {
  id: string;
  name: string;
}

export interface PlateInfo {
  id: number;
  name: string;
  objects: PlateObject[];
  /** Base64-encoded PNG; empty string when the 3MF has no thumbnail. */
  thumbnail: string;
  /**
   * 0-based filament indices the plate actually prints, derived from
   * per-object extruder metadata plus face-painting scans. `null` means
   * "couldn't determine — show every declared filament"; an empty array
   * means "this plate prints no filaments".
   */
  used_filament_indices: number[] | null;
}

export interface FilamentInfo {
  index: number;
  type: string;
  /** "RRGGBB" hex (no #), or "" for unset. */
  color: string;
  setting_id: string;
  /**
   * True if any object/part in the 3MF references this extruder. Files
   * commonly declare more filaments than they actually print, so the UI
   * should filter to `used: true` to avoid asking users to map slots that
   * don't matter.
   */
  used: boolean;
}

export interface PrinterInfo {
  printer_settings_id: string;
  printer_model: string;
  nozzle_diameter: string;
}

export interface PrintProfileInfo {
  print_settings_id: string;
  layer_height: string;
}

export interface ThreeMFInfo {
  plates: PlateInfo[];
  filaments: FilamentInfo[];
  print_profile: PrintProfileInfo;
  printer: PrinterInfo;
  /** When true, the file already contains G-code — slicing & filament overrides are ignored. */
  has_gcode: boolean;
  /** Bed-type label as stored in the 3MF (e.g. "Textured PEI Plate"); matches `SlicerPlateType.label`. */
  bed_type: string;
  /** Server-derived diff vs. the system process preset. Optional — older gateways omit it. */
  process_modifications?: ProcessModifications | null;
}

// --- Slicer profile shapes (returned by GET /api/slicer/*) ---

export interface SlicerMachine {
  setting_id: string;
  name: string;
  vendor: string;
  nozzle_diameter: string;
  printer_model: string;
}

export interface SlicerProcess {
  setting_id: string;
  name: string;
  vendor: string;
  /** Machine setting_ids this process is compatible with. */
  compatible_printers: string[];
  layer_height: string;
}

export interface SlicerPlateType {
  value: string;
  label: string;
}

// --- Resolve-for-machine ---

export type ResolveFilamentMatch =
  | 'unchanged'
  | 'alias'
  | 'type'
  | 'default'
  | 'first_compat'
  | 'none';

export type ResolveProcessMatch =
  | 'unchanged'
  | 'alias'
  | 'layer_height'
  | 'default'
  | 'first_compat'
  | 'none';

export type ResolvePlateTypeMatch = 'unchanged' | 'default' | 'none';

export interface ResolvedFilament {
  slot: number;
  requested: string;
  setting_id: string;
  name: string;
  alias: string;
  match: ResolveFilamentMatch;
}

export interface ResolvedProcess {
  requested: string;
  setting_id: string;
  name: string;
  alias: string;
  match: ResolveProcessMatch;
}

export interface ResolvedPlateType {
  requested: string;
  resolved: string;
  match: ResolvePlateTypeMatch;
}

export interface ResolveForMachineResponse {
  machine_id: string;
  machine_name: string;
  process: ResolvedProcess | null;
  filaments: ResolvedFilament[];
  plate_type: ResolvedPlateType | null;
}

// --- Filament matching ---

export type FilamentMatchReason = 'exact_filament_id' | 'type_fallback' | 'none';

export interface ProjectFilamentMatch {
  index: number;
  setting_id: string;
  type: string;
  color: string;
  resolved_profile: SlicerFilament | null;
  preferred_tray_slot: number | null;
  match_reason: FilamentMatchReason;
}

export interface FilamentMatchRequest {
  printer_id: string;
  filaments: FilamentInfo[];
}

export interface FilamentMatchResponse {
  printer_id: string;
  matches: ProjectFilamentMatch[];
}

// --- Upload tracker ---

export type UploadStatus = 'pending' | 'uploading' | 'completed' | 'failed' | 'cancelled';

export interface UploadState {
  upload_id: string;
  filename: string;
  printer_id: string;
  total_bytes: number;
  bytes_sent: number;
  /** 0–100 integer. */
  progress: number;
  status: UploadStatus;
  error: string | null;
}

// --- Settings transfer (returned in print SSE result) ---

export interface TransferredSetting {
  key: string;
  value: string;
  original: string | null;
}

export interface FilamentTransferEntry {
  slot: number;
  original_filament: string;
  selected_filament: string;
  /** "applied" | "filament_changed" | "no_customizations" */
  status: string;
  transferred: TransferredSetting[];
  discarded: string[];
}

export interface SettingsTransferInfo {
  status: string;
  transferred: TransferredSetting[];
  filaments: FilamentTransferEntry[];
  /** Per-key result of `process_overrides` resolution, present when overrides were submitted. */
  process_overrides_applied?: ProcessOverrideApplied[];
}

// --- Print estimate (returned by slicing/printing responses when available) ---

export interface PrintEstimate {
  total_filament_millimeters?: number | null;
  total_filament_grams?: number | null;
  model_filament_millimeters?: number | null;
  model_filament_grams?: number | null;
  prepare_seconds?: number | null;
  model_print_seconds?: number | null;
  total_seconds?: number | null;
}

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

// --- Slice jobs ---

export type SliceJobStatus =
  | 'queued'
  | 'slicing'
  | 'uploading'
  | 'ready'
  | 'failed'
  | 'cancelled';

export interface SliceJob {
  job_id: string;
  status: SliceJobStatus;
  progress: number;
  phase: string | null;
  filename: string;
  printer_id: string | null;
  auto_print: boolean;
  created_at: string;
  updated_at: string;
  estimate: PrintEstimate | null;
  settings_transfer: SettingsTransferInfo | null;
  output_size: number | null;
  error: string | null;
  /** True when `GET /api/slice-jobs/{id}/thumbnail` will return a PNG. */
  has_thumbnail: boolean;
  /** True once the sliced file has been handed off to the printer. */
  printed: boolean;
}

export interface SliceJobListResponse {
  jobs: SliceJob[];
}
