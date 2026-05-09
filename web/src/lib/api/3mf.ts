import { ApiError } from './client';
import type { ThreeMFInfo } from './types';

// Wire shape from the gateway: process_modifications uses snake_case field
// names. The TS domain interface uses camelCase (processSettingId,
// modifiedKeys), so we adapt here rather than polluting the domain type.
interface ThreeMFInfoWire extends Omit<ThreeMFInfo, 'process_modifications'> {
  process_modifications?: {
    process_setting_id: string;
    modified_keys: string[];
    values: Record<string, string>;
  } | null;
}

function adaptThreeMFInfo(raw: ThreeMFInfoWire): ThreeMFInfo {
  if (!raw.process_modifications) {
    return { ...raw, process_modifications: raw.process_modifications ?? null } as ThreeMFInfo;
  }
  const pm = raw.process_modifications;
  return {
    ...raw,
    process_modifications: {
      processSettingId: pm.process_setting_id,
      modifiedKeys: pm.modified_keys,
      values: pm.values,
    },
  };
}

/**
 * POST /api/parse-3mf with a multipart `file` field.
 * `fetchJson` is not used here because the body is `FormData`, not JSON,
 * and we need the request to set its own Content-Type with the boundary.
 */
export async function parse3mf(file: File): Promise<ThreeMFInfo> {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch('/api/parse-3mf', { method: 'POST', body: fd });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      // body wasn't JSON
    }
    throw new ApiError(res.status, detail);
  }
  const raw = (await res.json()) as ThreeMFInfoWire;
  return adaptThreeMFInfo(raw);
}
