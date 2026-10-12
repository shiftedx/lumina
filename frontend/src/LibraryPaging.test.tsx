import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/react';

import type { DownloadJob } from './types';
import { DownloadsSurface } from './features/downloads/DownloadsSurface';

const noop = vi.fn();

describe('DownloadsSurface load more', () => {
  const job = { id: 'job-1', source_url: 'https://example.test/1', status: 'completed' } as DownloadJob;
  const downloadsProps = {
    onCancel: noop,
    onRetry: noop,
    onClear: noop,
    loadState: 'ready' as const,
    problem: null,
    onRetryLoad: noop,
    onSignIn: noop,
    retryingLoad: false,
  };

  it('reveals a load-more control that pages the jobs feed', () => {
    const onLoadMoreJobs = vi.fn();
    const { getByRole } = render(<DownloadsSurface {...downloadsProps} jobs={[job]} hasMoreJobs loadingMoreJobs={false} loadMoreJobsError={null} onLoadMoreJobs={onLoadMoreJobs} />);

    fireEvent.click(getByRole('button', { name: /show more/i }));
    expect(onLoadMoreJobs).toHaveBeenCalled();
  });
});
