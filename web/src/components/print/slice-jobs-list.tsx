import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  Download,
  FileDown,
  Info,
  Loader2,
  Printer,
  Trash2,
  X,
} from 'lucide-react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { PrintEstimationCard } from '@/components/print/print-estimation-card';
import { hasPrintEstimate } from '@/lib/print-estimate';
import {
  cancelSliceJob,
  clearSliceJobs,
  deleteSliceJob,
  listSliceJobs,
  sliceJobInputUrl,
  sliceJobOutputUrl,
  sliceJobThumbnailUrl,
} from '@/lib/api/slice-jobs';
import { printFromJob } from '@/lib/api/print';
import { listPrinters } from '@/lib/api/printers';
import { usePrinterContext } from '@/lib/printer-context';
import type { PrinterStatus, SliceJob, SliceJobStatus } from '@/lib/api/types';
import { cn } from '@/lib/utils';

const TERMINAL_STATUSES: SliceJobStatus[] = [
  'ready',
  'printing',
  'failed',
  'cancelled',
];

function isTerminal(status: SliceJobStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

// Bambu's `subtask_name` may include or omit the `.gcode.3mf` / `.3mf`
// suffix and is sometimes case-mangled. Mirror the gateway's normalizer
// in `app/live_activity_thumbnail.py::_normalize_filename`.
function normalizeFilename(name: string): string {
  return name
    .toLowerCase()
    .replace(/^.*[\\\/]/, '')
    .replace(/\.gcode\.3mf$/, '')
    .replace(/\.3mf$/, '');
}

function filenamesMatch(a: string | undefined, b: string): boolean {
  if (!a) return false;
  return normalizeFilename(a) === normalizeFilename(b);
}

const STATUS_LABEL: Record<SliceJobStatus, string> = {
  queued: 'Queued',
  slicing: 'Slicing',
  uploading: 'Uploading',
  printing: 'Printing',
  ready: 'Ready',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

const STATUS_CLASSES: Record<SliceJobStatus, string> = {
  queued: 'bg-bg-1 text-text-1 border border-line',
  slicing: 'bg-accent/15 text-accent border border-accent/40',
  uploading: 'bg-accent/15 text-accent border border-accent/40',
  printing: 'bg-success/15 text-success border border-success/40',
  ready: 'bg-success/15 text-success border border-success/40',
  failed: 'bg-danger/15 text-danger border border-danger/40',
  cancelled: 'bg-bg-1 text-text-1/70 border border-line line-through',
};

function formatRelativeTime(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const seconds = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function SliceJobsList() {
  const queryClient = useQueryClient();
  const { activePrinterId } = usePrinterContext();

  const jobsQuery = useQuery({
    queryKey: ['slice-jobs'],
    queryFn: listSliceJobs,
    // Poll fast while there's work in flight, slow when everything is settled.
    refetchInterval: (q) => {
      const data = q.state.data;
      if (!data || data.some((j) => !isTerminal(j.status))) return 2000;
      return 30_000;
    },
  });

  // Resolve printer ids → names + live status so rows can show "Printer A"
  // instead of a serial and the reprint heuristic can tell when a `printing`
  // slice job is actually a stale leftover of a long-completed print.
  const printersQuery = useQuery({
    queryKey: ['printers'],
    queryFn: listPrinters,
    refetchInterval: 4_000,
  });

  const printerNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const p of printersQuery.data?.printers ?? []) {
      map.set(p.id, p.name || p.id);
    }
    return map;
  }, [printersQuery.data]);

  const printerStatusById = useMemo(() => {
    const map = new Map<string, PrinterStatus>();
    for (const p of printersQuery.data?.printers ?? []) {
      map.set(p.id, p);
    }
    return map;
  }, [printersQuery.data]);

  const cancelMut = useMutation({
    mutationFn: (jobId: string) => cancelSliceJob(jobId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['slice-jobs'] }),
    onError: (e: Error) => toast.error(`Couldn't cancel job: ${e.message}`),
  });

  const deleteMut = useMutation({
    mutationFn: (jobId: string) => deleteSliceJob(jobId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['slice-jobs'] }),
    onError: (e: Error) => toast.error(`Couldn't delete job: ${e.message}`),
  });

  const printMut = useMutation({
    mutationFn: ({ jobId, printerId }: { jobId: string; printerId?: string }) =>
      printFromJob(jobId, printerId),
    onSuccess: (res) => {
      toast.success(`Print started: ${res.file_name}`);
      queryClient.invalidateQueries({ queryKey: ['slice-jobs'] });
    },
    onError: (e: Error) => toast.error(`Couldn't start print: ${e.message}`),
  });

  const clearMut = useMutation({
    mutationFn: () => clearSliceJobs(),
    onSuccess: (jobs) => {
      toast.success(`Cleared ${jobs.length} job${jobs.length === 1 ? '' : 's'}`);
      queryClient.invalidateQueries({ queryKey: ['slice-jobs'] });
    },
    onError: (e: Error) => toast.error(`Couldn't clear jobs: ${e.message}`),
  });

  const clearFailedMut = useMutation({
    mutationFn: () => clearSliceJobs(['failed']),
    onSuccess: (jobs) => {
      toast.success(`Cleared ${jobs.length} failed job${jobs.length === 1 ? '' : 's'}`);
      queryClient.invalidateQueries({ queryKey: ['slice-jobs'] });
    },
    onError: (e: Error) => toast.error(`Couldn't clear failed jobs: ${e.message}`),
  });

  const jobs = jobsQuery.data ?? [];
  const sortedJobs = useMemo(
    () =>
      [...jobs].sort(
        (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
      ),
    [jobs],
  );
  const terminalCount = sortedJobs.filter((j) => isTerminal(j.status)).length;
  const failedCount = sortedJobs.filter((j) => j.status === 'failed').length;

  return (
    <Card className="p-4 bg-card border border-line flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-[14px] font-semibold text-text-0">Slice jobs</h2>
        <div className="flex items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            onClick={() => clearFailedMut.mutate()}
            disabled={failedCount === 0 || clearFailedMut.isPending}
            className="h-auto py-1 px-2 text-text-1 text-[12px] font-semibold hover:text-danger"
          >
            {clearFailedMut.isPending ? (
              <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" aria-hidden />
            ) : null}
            Clear failed{failedCount > 0 ? ` (${failedCount})` : ''}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => clearMut.mutate()}
            disabled={terminalCount === 0 || clearMut.isPending}
            className="h-auto py-1 px-2 text-text-1 text-[12px] font-semibold hover:text-text-0"
          >
            {clearMut.isPending ? (
              <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" aria-hidden />
            ) : null}
            Clear completed{terminalCount > 0 ? ` (${terminalCount})` : ''}
          </Button>
        </div>
      </div>

      {jobsQuery.isLoading ? (
        <p className="text-[13px] text-text-1">Loading…</p>
      ) : sortedJobs.length === 0 ? (
        <p className="text-[13px] text-text-1">
          No slice jobs yet. Submit a 3MF above to get started.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {sortedJobs.map((job) => (
            <SliceJobRow
              key={job.job_id}
              job={job}
              printerName={
                job.printer_id ? printerNameById.get(job.printer_id) ?? job.printer_id : null
              }
              printerStatus={
                job.printer_id ? printerStatusById.get(job.printer_id) ?? null : null
              }
              onCancel={() => cancelMut.mutate(job.job_id)}
              onDelete={() => deleteMut.mutate(job.job_id)}
              onPrint={() =>
                printMut.mutate({
                  jobId: job.job_id,
                  printerId: job.printer_id ?? activePrinterId ?? undefined,
                })
              }
              isCancelling={cancelMut.isPending && cancelMut.variables === job.job_id}
              isDeleting={deleteMut.isPending && deleteMut.variables === job.job_id}
              isPrinting={printMut.isPending && printMut.variables?.jobId === job.job_id}
            />
          ))}
        </ul>
      )}
    </Card>
  );
}

function SliceJobRow({
  job,
  printerName,
  printerStatus,
  onCancel,
  onDelete,
  onPrint,
  isCancelling,
  isDeleting,
  isPrinting,
}: {
  job: SliceJob;
  printerName: string | null;
  printerStatus: PrinterStatus | null;
  onCancel: () => void;
  onDelete: () => void;
  onPrint: () => void;
  isCancelling: boolean;
  isDeleting: boolean;
  isPrinting: boolean;
}) {
  const terminal = isTerminal(job.status);
  const showProgress =
    job.status === 'slicing' ||
    job.status === 'uploading' ||
    job.status === 'queued';
  // The slice-job state machine has no FINISHED status, so a `printing`
  // row may be either live on the printer or a stale leftover from a
  // long-completed print. Decide which by cross-referencing the printer's
  // current state: if the printer isn't running this slice's file, the
  // previous print is over and a Reprint is safe to offer.
  const hasOutput = (job.output_size ?? 0) > 0;
  const looksFinished =
    job.status === 'printing' &&
    printerStatus != null &&
    printerStatus.online &&
    (
      printerStatus.state !== 'printing' ||
      !filenamesMatch(printerStatus.job?.file_name, job.filename)
    );
  const isReprintable =
    job.status === 'failed' ||
    job.status === 'cancelled' ||
    looksFinished;
  const canPrint = (job.status === 'ready' || isReprintable) && hasOutput;
  const canDownload = job.status === 'ready' || job.status === 'printing';
  const rowBusy = isCancelling || isDeleting || isPrinting;

  const showThumbnail = job.has_thumbnail;
  const showSummary = hasPrintEstimate(job.estimate);

  return (
    <li className="rounded-lg border border-line bg-surface-0 p-3 flex flex-col gap-2">
      <div className="flex items-start gap-3">
        {showThumbnail && (
          <img
            src={sliceJobThumbnailUrl(job.job_id)}
            alt=""
            aria-hidden
            className="h-14 w-14 flex-shrink-0 rounded-md border border-line bg-bg-1 object-contain"
            loading="lazy"
          />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className="text-[14px] font-semibold text-text-0 truncate"
              title={job.filename}
            >
              {job.filename}
            </span>
            <span
              className={cn(
                'rounded-full px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap',
                STATUS_CLASSES[job.status],
              )}
            >
              {STATUS_LABEL[job.status]}
              {showProgress ? ` ${job.progress}%` : ''}
            </span>
            {job.auto_print && (
              <span className="rounded-full px-2 py-0.5 text-[11px] font-semibold bg-bg-1 text-text-1 border border-line">
                Auto-print
              </span>
            )}
          </div>
          <div className="text-[12px] text-text-1 mt-0.5 truncate">
            {[printerName, formatRelativeTime(job.created_at)]
              .filter(Boolean)
              .join(' · ')}
          </div>
          {showProgress && job.phase && (
            <div
              className="text-[12px] text-text-1 mt-0.5 truncate"
              title={job.phase}
            >
              {job.phase}
            </div>
          )}
          {job.error && (
            <div className="text-[12px] text-danger mt-1 break-words">
              {job.error}
            </div>
          )}
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          {showSummary && (
            <Popover>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  aria-label={`Show summary for ${job.filename}`}
                  title="Summary"
                  className="h-10 w-10 text-text-1 hover:text-text-0"
                >
                  <Info className="h-4 w-4" aria-hidden />
                </Button>
              </PopoverTrigger>
              <PopoverContent
                align="end"
                className="w-[320px] border-line bg-bg-1 p-0"
              >
                <PrintEstimationCard
                  estimate={job.estimate}
                  className="border-0 bg-transparent shadow-none p-3"
                />
              </PopoverContent>
            </Popover>
          )}
          {canPrint && (
            <Button
              type="button"
              size="icon"
              variant="ghost"
              onClick={onPrint}
              disabled={rowBusy}
              aria-label={`${isReprintable ? 'Reprint' : 'Print'} ${job.filename}`}
              title={isReprintable ? 'Reprint' : 'Print'}
              className="h-10 w-10 text-accent hover:text-accent"
            >
              {isPrinting ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <Printer className="h-4 w-4" aria-hidden />
              )}
            </Button>
          )}
          <a
            href={sliceJobInputUrl(job.job_id)}
            download
            aria-label={`Download original 3MF for ${job.filename}`}
            title="Download original 3MF"
            className={cn(
              'inline-flex h-10 w-10 items-center justify-center rounded-md text-text-1 hover:text-text-0',
              rowBusy && 'pointer-events-none opacity-50',
            )}
            tabIndex={rowBusy ? -1 : 0}
          >
            <FileDown className="h-4 w-4" aria-hidden />
          </a>
          {canDownload && (
            <a
              href={sliceJobOutputUrl(job.job_id)}
              download
              aria-label={`Download sliced 3MF for ${job.filename}`}
              title="Download sliced 3MF"
              className={cn(
                'inline-flex h-10 w-10 items-center justify-center rounded-md text-text-1 hover:text-text-0',
                rowBusy && 'pointer-events-none opacity-50',
              )}
              tabIndex={rowBusy ? -1 : 0}
            >
              <Download className="h-4 w-4" aria-hidden />
            </a>
          )}
          {!terminal && (
            <Button
              type="button"
              size="icon"
              variant="ghost"
              onClick={onCancel}
              disabled={rowBusy}
              aria-label={`Cancel slice job for ${job.filename}`}
              title="Cancel"
              className="h-10 w-10 text-danger hover:text-danger"
            >
              {isCancelling ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <X className="h-4 w-4" aria-hidden />
              )}
            </Button>
          )}
          <DeleteAction
            filename={job.filename}
            onConfirm={onDelete}
            disabled={rowBusy}
            isDeleting={isDeleting}
          />
        </div>
      </div>
      {showProgress && (
        <Progress
          value={Math.max(0, Math.min(100, job.progress))}
          aria-label={`Slicing progress for ${job.filename}`}
          className="h-2 bg-bg-1 [&>div]:bg-gradient-to-r [&>div]:from-accent-strong [&>div]:to-accent"
        />
      )}
    </li>
  );
}

function DeleteAction({
  filename,
  onConfirm,
  disabled,
  isDeleting,
}: {
  filename: string;
  onConfirm: () => void;
  disabled: boolean;
  isDeleting: boolean;
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          disabled={disabled}
          aria-label={`Delete slice job for ${filename}`}
          title="Delete"
          className="h-10 w-10 text-text-1 hover:text-danger"
        >
          {isDeleting ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          ) : (
            <Trash2 className="h-4 w-4" aria-hidden />
          )}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete this slice job?</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="font-semibold">{filename}</span> and its sliced 3MF
            will be permanently removed. This can't be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>Delete</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
