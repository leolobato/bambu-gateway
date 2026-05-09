import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { fetchJson, ApiError } from './client';
import type {
  ProcessLayout,
  ProcessOption,
  ProcessOptionsCatalogue,
} from '@/lib/process/types';

/* ------------------------------------------------------------------ */
/* Wire shapes (snake_case from the slicer) and adapters to camelCase. */
/* ------------------------------------------------------------------ */

interface RawProcessOption {
  key: string;
  label: string;
  category: string;
  tooltip: string;
  type: ProcessOption['type'];
  sidetext: string;
  default: string;
  min: number | null;
  max: number | null;
  enum_values: string[] | null;
  enum_labels: string[] | null;
  mode: ProcessOption['mode'];
  gui_type: ProcessOption['guiType'];
  nullable: boolean;
  readonly: boolean;
}

interface RawCatalogue {
  version: string;
  options: Record<string, RawProcessOption>;
}

interface RawLayout {
  version: string;
  allowlist_revision: string;
  pages: { label: string; optgroups: { label: string; options: string[] }[] }[];
}

function adaptOption(raw: RawProcessOption): ProcessOption {
  return {
    key: raw.key,
    label: raw.label,
    category: raw.category,
    tooltip: raw.tooltip,
    type: raw.type,
    sidetext: raw.sidetext,
    default: raw.default,
    min: raw.min,
    max: raw.max,
    enumValues: raw.enum_values,
    enumLabels: raw.enum_labels,
    mode: raw.mode,
    guiType: raw.gui_type,
    nullable: raw.nullable,
    readonly: raw.readonly,
  };
}

function adaptCatalogue(raw: RawCatalogue): ProcessOptionsCatalogue {
  const options: Record<string, ProcessOption> = {};
  for (const [k, v] of Object.entries(raw.options)) options[k] = adaptOption(v);
  return { version: raw.version, options };
}

function adaptLayout(raw: RawLayout): ProcessLayout {
  return {
    version: raw.version,
    allowlistRevision: raw.allowlist_revision,
    pages: raw.pages.map((p) => ({
      label: p.label,
      optgroups: p.optgroups.map((g) => ({ label: g.label, options: g.options })),
    })),
  };
}

/* ------------------------------------------------------------------ */
/* Fetchers                                                            */
/* ------------------------------------------------------------------ */

export async function fetchProcessOptions(): Promise<ProcessOptionsCatalogue> {
  const raw = await fetchJson<RawCatalogue>('/api/slicer/options/process');
  return adaptCatalogue(raw);
}

export async function fetchProcessLayout(): Promise<ProcessLayout> {
  const raw = await fetchJson<RawLayout>('/api/slicer/options/process/layout');
  return adaptLayout(raw);
}

export async function fetchProcessProfile(settingId: string): Promise<Record<string, string>> {
  return fetchJson<Record<string, string>>(
    `/api/slicer/processes/${encodeURIComponent(settingId)}`,
  );
}

/* ------------------------------------------------------------------ */
/* Hooks (TanStack Query)                                              */
/* ------------------------------------------------------------------ */

const RETRYABLE_503_CODES = new Set(['options_not_loaded', 'options_layout_not_loaded']);

function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 1) return false;
  if (!(error instanceof ApiError)) return false;
  if (error.status !== 503) return false;
  return !!error.code && RETRYABLE_503_CODES.has(error.code);
}

export function useProcessOptions(): UseQueryResult<ProcessOptionsCatalogue, Error> {
  return useQuery({
    queryKey: ['process-options', 'catalogue'],
    queryFn: fetchProcessOptions,
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
    retry: shouldRetry,
    retryDelay: 1500,
  });
}

export function useProcessLayout(): UseQueryResult<ProcessLayout, Error> {
  return useQuery({
    queryKey: ['process-options', 'layout'],
    queryFn: fetchProcessLayout,
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
    retry: shouldRetry,
    retryDelay: 1500,
  });
}

export function useProcessProfile(
  settingId: string | undefined,
): UseQueryResult<Record<string, string>, Error> {
  return useQuery({
    queryKey: ['process-options', 'profile', settingId ?? ''],
    queryFn: () => fetchProcessProfile(settingId!),
    enabled: !!settingId,
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
  });
}
