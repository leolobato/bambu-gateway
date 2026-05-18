import { ApiError } from './client';
import type {
  StlDraftScene,
  StlLayoutAction,
  StlMaterializedProject,
} from './types';

export interface MaterializeStlDraftArgs {
  thumbnailPngDataUrl?: string | null;
}

export interface CreateStlDraftArgs {
  file: File;
  machineProfile: string;
  processProfile: string;
  plateType?: string;
  autoOrient?: boolean;
  arrange?: boolean;
  center?: boolean;
}

async function detailFromResponse(res: Response): Promise<string> {
  let detail = res.statusText;
  try {
    const body = (await res.json()) as { detail?: string };
    if (body && typeof body.detail === 'string') detail = body.detail;
  } catch {
    // body wasn't JSON
  }
  return detail;
}

export async function createStlDraft(args: CreateStlDraftArgs): Promise<StlDraftScene> {
  const fd = new FormData();
  fd.append('file', args.file);
  fd.append('machine_profile', args.machineProfile);
  fd.append('process_profile', args.processProfile);
  if (args.plateType) fd.append('plate_type', args.plateType);
  fd.append('auto_orient', args.autoOrient ? 'true' : 'false');
  fd.append('arrange', args.arrange === false ? 'false' : 'true');
  fd.append('center', args.center === false ? 'false' : 'true');

  const res = await fetch('/api/stl-drafts', { method: 'POST', body: fd });
  if (!res.ok) {
    throw new ApiError(res.status, await detailFromResponse(res));
  }
  return (await res.json()) as StlDraftScene;
}

export async function layoutStlDraft(
  draftToken: string,
  action: StlLayoutAction,
): Promise<StlDraftScene> {
  const res = await fetch(`/api/stl-drafts/${encodeURIComponent(draftToken)}/layout`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  });
  if (!res.ok) {
    throw new ApiError(res.status, await detailFromResponse(res));
  }
  return (await res.json()) as StlDraftScene;
}

export async function materializeStlDraft(
  draftToken: string,
  args: MaterializeStlDraftArgs = {},
): Promise<StlMaterializedProject> {
  const res = await fetch(`/api/stl-drafts/${encodeURIComponent(draftToken)}/3mf`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ thumbnail_png_data_url: args.thumbnailPngDataUrl ?? '' }),
  });
  if (!res.ok) {
    throw new ApiError(res.status, await detailFromResponse(res));
  }
  return (await res.json()) as StlMaterializedProject;
}
