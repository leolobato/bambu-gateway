export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
    public code?: string,
    message?: string,
  ) {
    super(message ?? `API ${status}: ${detail}`);
    this.name = 'ApiError';
  }
}

export async function fetchJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    let code: string | undefined;
    try {
      const body = (await res.json()) as { detail?: string; code?: string };
      if (body && typeof body.detail === 'string') detail = body.detail;
      if (body && typeof body.code === 'string') code = body.code;
    } catch {
      // body wasn't JSON; keep statusText
    }
    throw new ApiError(res.status, detail, code);
  }
  return (await res.json()) as T;
}
