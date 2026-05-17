import { afterEach, describe, expect, test, vi } from 'vitest';
import {
  createStlDraft,
  layoutStlDraft,
  materializeStlDraft,
} from './stl-drafts';

function mockFetch(response: Response) {
  const fn = vi.fn().mockResolvedValue(response);
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('stl draft api', () => {
  test('createStlDraft posts multipart STL options', async () => {
    const fetchMock = mockFetch(Response.json({
      draft_token: 'draft1234',
      source_filename: 'part.stl',
      source_url: '/api/stl-drafts/draft1234/source.stl',
      bed: { width: 180, depth: 180, printable_area: [] },
      objects: [],
      warnings: [],
      actions: ['center'],
    }));
    const file = new File(['solid part\nendsolid part\n'], 'part.stl', { type: 'model/stl' });

    const scene = await createStlDraft({
      file,
      machineProfile: 'GM020',
      processProfile: 'GP000',
      plateType: 'textured_pei_plate',
      autoOrient: true,
      arrange: false,
      center: true,
    });

    expect(scene.draft_token).toBe('draft1234');
    expect(fetchMock).toHaveBeenCalledWith('/api/stl-drafts', {
      method: 'POST',
      body: expect.any(FormData),
    });
    const body = fetchMock.mock.calls[0][1]!.body as FormData;
    expect(body.get('file')).toBe(file);
    expect(body.get('machine_profile')).toBe('GM020');
    expect(body.get('process_profile')).toBe('GP000');
    expect(body.get('auto_orient')).toBe('true');
    expect(body.get('arrange')).toBe('false');
  });

  test('layoutStlDraft posts action json', async () => {
    const fetchMock = mockFetch(Response.json({
      draft_token: 'draft1234',
      source_filename: 'part.stl',
      source_url: '/api/stl-drafts/draft1234/source.stl',
      bed: { width: 180, depth: 180, printable_area: [] },
      objects: [],
      warnings: [],
      actions: ['center'],
    }));

    await layoutStlDraft('draft1234', 'center');

    expect(fetchMock).toHaveBeenCalledWith('/api/stl-drafts/draft1234/layout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'center' }),
    });
  });

  test('layoutStlDraft supports X-axis rotation actions', async () => {
    const fetchMock = mockFetch(Response.json({
      draft_token: 'draft1234',
      source_filename: 'part.stl',
      source_url: '/api/stl-drafts/draft1234/source.stl',
      bed: { width: 180, depth: 180, printable_area: [] },
      objects: [],
      warnings: [],
      actions: ['rotate_x_90'],
    }));

    await layoutStlDraft('draft1234', 'rotate_x_90');

    expect(fetchMock).toHaveBeenCalledWith('/api/stl-drafts/draft1234/layout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'rotate_x_90' }),
    });
  });

  test('materializeStlDraft returns 3mf blob', async () => {
    mockFetch(new Response('3mf-bytes', { status: 200 }));

    const blob = await materializeStlDraft('draft1234');

    expect(await blob.text()).toBe('3mf-bytes');
  });
});
