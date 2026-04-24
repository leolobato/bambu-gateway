import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

export interface UseDropZoneOptions {
  /** File extension to accept, lowercase, including the dot. */
  accept: string;
  /** Called once per accepted drop. */
  onFile: (file: File) => void;
  /** When false, the hook is inert (no listeners attached). Default true. */
  enabled?: boolean;
}

/**
 * Subscribe to document-level drag-and-drop. Returns `dragging: true`
 * whenever the user is mid-drag over the page so a tinted overlay can
 * be shown. Multi-file drops take only the first matching file.
 */
export function useDropZone({ accept, onFile, enabled = true }: UseDropZoneOptions) {
  const [dragging, setDragging] = useState(false);
  // Use a ref for the depth counter so listener identity stays stable across re-renders.
  const depthRef = useRef(0);

  useEffect(() => {
    if (!enabled) return;

    function onDragEnter(e: DragEvent) {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depthRef.current += 1;
      setDragging(true);
    }

    function onDragOver(e: DragEvent) {
      if (!hasFiles(e)) return;
      // Required to allow `drop` to fire.
      e.preventDefault();
    }

    function onDragLeave(e: DragEvent) {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depthRef.current = Math.max(0, depthRef.current - 1);
      if (depthRef.current === 0) setDragging(false);
    }

    function onDrop(e: DragEvent) {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depthRef.current = 0;
      setDragging(false);

      const files = Array.from(e.dataTransfer?.files ?? []);
      const match = files.find((f) => f.name.toLowerCase().endsWith(accept));
      if (!match) {
        toast.error(`Drop a ${accept} file.`);
        return;
      }
      onFile(match);
    }

    document.addEventListener('dragenter', onDragEnter);
    document.addEventListener('dragover', onDragOver);
    document.addEventListener('dragleave', onDragLeave);
    document.addEventListener('drop', onDrop);
    return () => {
      document.removeEventListener('dragenter', onDragEnter);
      document.removeEventListener('dragover', onDragOver);
      document.removeEventListener('dragleave', onDragLeave);
      document.removeEventListener('drop', onDrop);
      depthRef.current = 0;
      setDragging(false);
    };
  }, [accept, onFile, enabled]);

  return { dragging };
}

/** True when the drag carries at least one file (vs text, URL, etc.). */
function hasFiles(e: DragEvent): boolean {
  const types = e.dataTransfer?.types;
  if (!types) return false;
  for (let i = 0; i < types.length; i++) {
    if (types[i] === 'Files') return true;
  }
  return false;
}
