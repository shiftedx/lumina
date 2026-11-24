import type { AppEvent } from './types';

export type CollectionResource = 'jobs' | 'library';
export type CollectionRequestToken = { generation: number; request: number; resource: CollectionResource };

export class CollectionRequestGate {
  private generation = 0;
  private requests: Record<CollectionResource, number> = { jobs: 0, library: 0 };

  beginSession(): number {
    this.generation += 1;
    return this.generation;
  }

  invalidateSession(): void {
    this.generation += 1;
  }

  isSessionCurrent(generation: number): boolean {
    return generation === this.generation;
  }

  currentSession(): number {
    return this.generation;
  }

  begin(resource: CollectionResource): CollectionRequestToken {
    const request = this.requests[resource] + 1;
    this.requests[resource] = request;
    return { generation: this.generation, request, resource };
  }

  invalidate(resource: CollectionResource): void {
    this.requests[resource] += 1;
  }

  isCurrent(token: CollectionRequestToken): boolean {
    return token.generation === this.generation && token.request === this.requests[token.resource];
  }
}

export const SSE_PROGRESS_COALESCE_INTERVAL_MS = 250;

/** Hints (a library change this member may or may not see) coalesce into one page refetch per this window. */
export const LIBRARY_HINT_REFETCH_MS = 1500;

/** The content-free broadcast a member with library limits receives in place of a shared item's event. */
export const isLibraryHint = (event: AppEvent): boolean => {
  const payload = event.payload && typeof event.payload === 'object' ? (event.payload as { broadcast?: unknown; item?: unknown }) : null;
  return (event.type === 'library_item_upserted' || event.type === 'library_item_missing') && payload?.broadcast === true && payload.item === undefined;
};

const PROGRESS_EVENT_TYPES: ReadonlySet<AppEvent['type']> = new Set(['job_progress', 'job_postprocess']);

function eventJobId(event: AppEvent): string | null {
  const payload = event.payload && typeof event.payload === 'object' ? (event.payload as { job?: { id?: unknown } }) : null;
  const id = payload?.job?.id;
  return typeof id === 'string' && id ? id : null;
}

export type WorkspaceEventCoalescer = {
  /** Buffer a progress event or apply a terminal/list-shaped event immediately. */
  handle: (event: AppEvent) => void;
  /** Apply any buffered progress now and clear the pending timer. */
  flush: () => void;
  /** Drop buffered progress without applying and clear the pending timer. */
  cancel: () => void;
  /** Number of jobs with buffered progress awaiting a flush. */
  pendingCount: () => number;
};

/**
 * Groups realtime events so high-frequency progress redraws collapse into one
 * batched apply per {@link SSE_PROGRESS_COALESCE_INTERVAL_MS}. Terminal and
 * list-shaped events apply immediately; a terminal event for a job first
 * replays that job's buffered progress so a late flush can never overwrite the
 * terminal state. Other jobs' buffered progress is left untouched. The timer is
 * injectable so the coalescing window is deterministic under test.
 */
export function createWorkspaceEventCoalescer(
  apply: (events: AppEvent[]) => void,
  options: { intervalMs?: number; schedule?: (callback: () => void, delayMs: number) => () => void } = {},
): WorkspaceEventCoalescer {
  const intervalMs = options.intervalMs ?? SSE_PROGRESS_COALESCE_INTERVAL_MS;
  const schedule = options.schedule
    ?? ((callback, delayMs) => {
      const handle = setTimeout(callback, delayMs);
      return () => clearTimeout(handle);
    });
  const buffer = new Map<string, AppEvent>();
  let cancelTimer: (() => void) | null = null;

  function flushBuffer() {
    if (buffer.size === 0) return;
    const events = [...buffer.values()];
    buffer.clear();
    apply(events);
  }

  function scheduleFlush() {
    if (cancelTimer) return;
    cancelTimer = schedule(() => {
      cancelTimer = null;
      flushBuffer();
    }, intervalMs);
  }

  function clearTimer() {
    if (cancelTimer) cancelTimer();
    cancelTimer = null;
  }

  return {
    handle(event) {
      const jobId = eventJobId(event);
      if (PROGRESS_EVENT_TYPES.has(event.type)) {
        if (!jobId) {
          apply([event]);
          return;
        }
        buffer.set(jobId, event);
        scheduleFlush();
        return;
      }
      if (jobId && buffer.has(jobId)) {
        const pending = buffer.get(jobId) as AppEvent;
        buffer.delete(jobId);
        apply([pending, event]);
        return;
      }
      apply([event]);
    },
    flush() {
      clearTimer();
      flushBuffer();
    },
    cancel() {
      clearTimer();
      buffer.clear();
    },
    pendingCount() {
      return buffer.size;
    },
  };
}
