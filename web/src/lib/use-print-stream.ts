import { useCallback, useRef, useState } from 'react';
import type { PrintEstimate, SettingsTransferInfo } from './api/types';

export interface PrintStreamStatus {
  phase: string;
  message: string;
  upload_id?: string;
}

export interface PrintStreamProgress {
  percent?: number;
  status_line?: string;
  [key: string]: unknown;
}

export interface PrintStreamUploadProgress {
  percent: number;
  bytes_sent: number;
  total_bytes: number;
}

export interface PrintStreamResult {
  file_base64: string;
  settings_transfer?: SettingsTransferInfo;
  estimate?: PrintEstimate | null;
  preview_id?: string;
  [key: string]: unknown;
}

export interface PrintStreamPrintStarted {
  printer_id: string;
  file_name: string;
  settings_transfer?: SettingsTransferInfo;
  estimate?: PrintEstimate | null;
}

export interface PrintStreamHandlers {
  onStatus?: (s: PrintStreamStatus) => void;
  onProgress?: (p: PrintStreamProgress) => void;
  onResult?: (r: PrintStreamResult) => void;
  onUploadProgress?: (u: PrintStreamUploadProgress) => void;
  onPrintStarted?: (p: PrintStreamPrintStarted) => void;
  onError?: (e: { error: string }) => void;
  onDone?: () => void;
}

export interface StartPrintStreamArgs {
  file: File;
  printerId?: string;
  plateId: number;
  machineProfile: string;
  processProfile: string;
  /** JSON-encoded mapping of project-filament-index → {profile_setting_id, tray_slot}; pass empty object to skip. */
  filamentProfiles: Record<string, { profile_setting_id: string; tray_slot: number }>;
  plateType?: string;
  /** True = only slice + return preview_id (no upload). */
  preview?: boolean;
}

/**
 * One-shot SSE consumer for /api/print-stream. Returns `start(args, handlers)`
 * and a stable `cancel()` that aborts the in-flight stream.
 *
 * The hook does NOT manage caller state — handlers fire as events arrive,
 * the caller keeps its own state machine.
 */
export function usePrintStream() {
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const start = useCallback(
    async (args: StartPrintStreamArgs, handlers: PrintStreamHandlers) => {
      cancel(); // cancel any in-flight stream
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setStreaming(true);

      const fd = new FormData();
      fd.append('file', args.file);
      if (args.printerId) fd.append('printer_id', args.printerId);
      fd.append('plate_id', String(args.plateId));
      fd.append('machine_profile', args.machineProfile);
      fd.append('process_profile', args.processProfile);
      fd.append('filament_profiles', JSON.stringify(args.filamentProfiles));
      if (args.plateType) fd.append('plate_type', args.plateType);
      if (args.preview) fd.append('preview', 'true');

      try {
        const res = await fetch('/api/print-stream', {
          method: 'POST',
          body: fd,
          signal: ctrl.signal,
        });
        if (!res.ok) {
          let detail = res.statusText;
          try {
            const body = (await res.json()) as { detail?: string };
            if (body?.detail) detail = body.detail;
          } catch {
            // not JSON
          }
          handlers.onError?.({ error: detail });
          handlers.onDone?.();
          return;
        }
        if (!res.body) {
          handlers.onError?.({ error: 'No response body' });
          handlers.onDone?.();
          return;
        }
        await consumeSse(res.body, handlers);
      } catch (err) {
        if ((err as { name?: string }).name === 'AbortError') {
          // Caller-initiated abort — don't surface as error.
          return;
        }
        handlers.onError?.({ error: (err as Error).message });
        handlers.onDone?.();
      } finally {
        if (abortRef.current === ctrl) abortRef.current = null;
        setStreaming(false);
      }
    },
    [cancel],
  );

  return { start, cancel, streaming };
}

/**
 * Parse SSE frames from a streaming response body and dispatch to handlers.
 * Each frame ends with a blank line (\n\n). Lines beginning with "event:"
 * set the type; lines beginning with "data:" accumulate; the blank line
 * triggers a dispatch.
 */
async function consumeSse(stream: ReadableStream<Uint8Array>, handlers: PrintStreamHandlers) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let eventType: string | null = null;
  let dataLines: string[] = [];

  function dispatch() {
    if (!eventType || dataLines.length === 0) {
      eventType = null;
      dataLines = [];
      return;
    }
    let payload: unknown = {};
    try {
      payload = JSON.parse(dataLines.join('\n'));
    } catch {
      payload = { raw: dataLines.join('\n') };
    }
    switch (eventType) {
      case 'status':           handlers.onStatus?.(payload as PrintStreamStatus); break;
      case 'progress':         handlers.onProgress?.(payload as PrintStreamProgress); break;
      case 'result':           handlers.onResult?.(payload as PrintStreamResult); break;
      case 'upload_progress':  handlers.onUploadProgress?.(payload as PrintStreamUploadProgress); break;
      case 'print_started':    handlers.onPrintStarted?.(payload as PrintStreamPrintStarted); break;
      case 'error':            handlers.onError?.(payload as { error: string }); break;
      case 'done':             handlers.onDone?.(); break;
      // unknown event types are ignored
    }
    eventType = null;
    dataLines = [];
  }

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newlineIdx: number;
    while ((newlineIdx = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, newlineIdx).replace(/\r$/, '');
      buffer = buffer.slice(newlineIdx + 1);
      if (line === '') {
        dispatch();
      } else if (line.startsWith('event: ')) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        dataLines.push(line.slice(6));
      }
      // ignore comment lines (":") and other prefixes
    }
  }
  // Flush any final frame without trailing newline.
  if (buffer.length > 0) {
    const line = buffer.replace(/\r$/, '');
    if (line.startsWith('data: ')) dataLines.push(line.slice(6));
    else if (line.startsWith('event: ')) eventType = line.slice(7).trim();
  }
  dispatch();
}
