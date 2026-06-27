import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { fetchJson, ApiError } from './client';
import {
  adaptCatalogue, adaptLayout,
  type RawCatalogue, type RawLayout,
} from './process-options';
import type { ProcessLayout, ProcessOptionsCatalogue } from '@/lib/process/types';

export async function fetchFilamentOptions(): Promise<ProcessOptionsCatalogue> {
  const raw = await fetchJson<RawCatalogue>('/api/slicer/options/filament');
  return adaptCatalogue(raw);
}

export async function fetchFilamentLayout(): Promise<ProcessLayout> {
  const raw = await fetchJson<RawLayout>('/api/slicer/options/filament/layout');
  return adaptLayout(raw);
}

export async function fetchFilamentBaseline(settingId: string): Promise<Record<string, string>> {
  return fetchJson<Record<string, string>>(
    `/api/slicer/filaments/${encodeURIComponent(settingId)}`,
  );
}

const RETRYABLE_503_CODES = new Set(['options_not_loaded', 'options_layout_not_loaded']);

function shouldRetry(failureCount: number, error: Error): boolean {
  if (failureCount >= 1) return false;
  if (!(error instanceof ApiError)) return false;
  if (error.status !== 503) return false;
  return !!error.code && RETRYABLE_503_CODES.has(error.code);
}

export function useFilamentOptions(): UseQueryResult<ProcessOptionsCatalogue, Error> {
  return useQuery({
    queryKey: ['filament-options', 'catalogue'],
    queryFn: fetchFilamentOptions,
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
    retry: shouldRetry,
    retryDelay: 1500,
  });
}

export function useFilamentLayout(): UseQueryResult<ProcessLayout, Error> {
  return useQuery({
    queryKey: ['filament-options', 'layout'],
    queryFn: fetchFilamentLayout,
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
    retry: shouldRetry,
    retryDelay: 1500,
  });
}

export function useFilamentBaseline(
  settingId: string | undefined,
): UseQueryResult<Record<string, string>, Error> {
  return useQuery({
    queryKey: ['filament-options', 'baseline', settingId ?? ''],
    queryFn: () => fetchFilamentBaseline(settingId!),
    enabled: !!settingId,
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
  });
}
