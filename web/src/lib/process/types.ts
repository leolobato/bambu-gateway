export type ProcessOptionType =
  | 'coBool' | 'coBools'
  | 'coInt'  | 'coInts'
  | 'coFloat' | 'coFloats'
  | 'coPercent' | 'coPercents'
  | 'coFloatOrPercent' | 'coFloatsOrPercents'
  | 'coString' | 'coStrings'
  | 'coEnum'
  | 'coPoint' | 'coPoints' | 'coPoint3'
  | 'coNone';

export type ProcessOptionGuiType =
  | '' | 'color' | 'slider' | 'i_enum_open' | 'f_enum_open'
  | 'select_open' | 'legend' | 'one_string';

export interface ProcessOption {
  key: string;
  label: string;
  category: string;
  tooltip: string;
  type: ProcessOptionType;
  sidetext: string;
  default: string;
  min: number | null;
  max: number | null;
  enumValues: string[] | null;
  enumLabels: string[] | null;
  mode: 'simple' | 'advanced' | 'develop';
  guiType: ProcessOptionGuiType;
  nullable: boolean;
  readonly: boolean;
}

export interface ProcessOptionsCatalogue {
  version: string;
  options: Record<string, ProcessOption>;
}

export interface ProcessLayout {
  version: string;
  /** Accepted but not consulted client-side (allowlist deprecated upstream). */
  allowlistRevision: string;
  pages: ProcessPage[];
}

export interface ProcessPage {
  label: string;
  optgroups: ProcessOptgroup[];
}

export interface ProcessOptgroup {
  label: string;
  /** Option keys; metadata via the catalogue. */
  options: string[];
}

export interface ProcessModifications {
  processSettingId: string;
  modifiedKeys: string[];
  values: Record<string, string>;
}

export interface ProcessOverrideApplied {
  key: string;
  value: string;
  previous: string;
}
