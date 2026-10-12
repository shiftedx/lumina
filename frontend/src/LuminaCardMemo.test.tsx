import { render, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useStableCallback } from './LuminaApp';
import { DownloadsSurface } from './features/downloads/DownloadsSurface';
import { jobProgress } from './luminaModel';
import type { DownloadJob } from './types';

// jobProgress is invoked exactly once per JobRow render, so wrapping them in call-through spies gives a per-card
// render probe without instrumenting production components.
vi.mock('./luminaModel', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./luminaModel')>();
  return {
    ...actual,
    jobProgress: vi.fn(actual.jobProgress),
  };
});

const noop = vi.fn();

function runningJob(id: string, progress: number): DownloadJob {
  return { id, status: 'running', progress, title: `Download ${id}`, created_at: '2026-01-01T00:00:00Z' } as DownloadJob;
}

function renderDownloads(jobs: DownloadJob[], onCancel = noop, onRetry = noop) {
  return render(
    <DownloadsSurface
      jobs={jobs}
      loadState="ready"
      onCancel={onCancel}
      onClear={noop}
      onRetry={onRetry}
      onRetryLoad={noop}
      onSignIn={noop}
      problem={null}
      retryingLoad={false}
    />,
  );
}

describe('memoized list rows re-render in isolation', () => {
  it('re-renders only the download row whose data changed', () => {
    const jobs = [runningJob('job-1', 10), runningJob('job-2', 20), runningJob('job-3', 30)];
    const { rerender } = renderDownloads(jobs);

    vi.mocked(jobProgress).mockClear();
    const updated = [jobs[0], { ...jobs[1], progress: 55 }, jobs[2]];
    rerender(
      <DownloadsSurface jobs={updated} loadState="ready" onCancel={noop} onClear={noop} onRetry={noop} onRetryLoad={noop} onSignIn={noop} problem={null} retryingLoad={false} />,
    );

    const reRendered = vi.mocked(jobProgress).mock.calls.map(([job]) => job.id);
    expect(reRendered).toEqual(['job-2']);
  });
});

describe('useStableCallback', () => {
  // LuminaApp wraps render-fresh callbacks (e.g. acquisition.queue) so memoized cards keep their props.
  it('keeps one identity across renders while calling the latest callback', () => {
    const first = vi.fn();
    const latest = vi.fn();
    const { result, rerender } = renderHook(({ callback }) => useStableCallback(callback), { initialProps: { callback: first } });
    const stable = result.current;

    rerender({ callback: latest });
    expect(result.current).toBe(stable);
    result.current();
    expect(latest).toHaveBeenCalledTimes(1);
    expect(first).not.toHaveBeenCalled();
  });
});
