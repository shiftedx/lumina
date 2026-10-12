import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '../../../ui';

const api = vi.hoisted(() => ({ previewMetadataRefresh: vi.fn(), refreshTitleMetadata: vi.fn() }));
vi.mock('../../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../../api')>()), ...api }));
const { ApiRequestError } = await import('../../../api');
const { PreviewRefreshDialog } = await import('./PreviewRefreshDialog');

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function close() { this.removeAttribute('open'); };
});
afterEach(() => vi.resetAllMocks());
const show = (onClose = () => undefined) => render(<ToastProvider><PreviewRefreshDialog onClose={onClose} titleId="t1" /></ToastProvider>);

describe('PreviewRefreshDialog', () => {
  it('lists each field with its outcome and refreshes on request', async () => {
    const outcomes = ['update', 'same', 'kept_edit', 'kept_lock', 'kept_higher_source', 'new'] as const;
    api.previewMetadataRefresh.mockResolvedValue({ tmdb_id: 1, match: {}, images: [], fields: outcomes.map((outcome, i) => ({ field: i % 2 ? 'overview' : 'name', current: `c${i}`, incoming: `i${i}`, outcome })) });
    api.refreshTitleMetadata.mockResolvedValue({});
    const onClose = vi.fn();
    show(onClose);
    for (const word of ['Will update', 'Same', 'Kept: your edit', 'Kept: locked', 'Kept: NFO file wins', 'Will add']) expect(await screen.findByText(word)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Refresh now' }));
    expect(api.refreshTitleMetadata).toHaveBeenCalledWith('t1');
    expect(await screen.findByText('Refreshing details in the background.')).toBeTruthy();
    expect(onClose).toHaveBeenCalled();
  });
  it('Close closes', async () => {
    api.previewMetadataRefresh.mockReturnValue(new Promise(() => undefined));
    const onClose = vi.fn();
    show(onClose);
    expect(screen.getByRole('status').textContent).toBe('Loading the preview');
    await userEvent.click(screen.getAllByRole('button', { name: 'Close' }).at(-1) as HTMLElement);
    expect(onClose).toHaveBeenCalled();
  });
  it.each([
    [new ApiRequestError('tmdb_not_configured', 409), 'Add a TMDB key in Settings → Server → Metadata to preview a refresh.'],
    [new ApiRequestError('title_locked', 409), 'This title is locked, so a refresh would change nothing. Turn off Lock this item first.'],
    [new ApiRequestError('no_match', 404), 'This title has no TMDB match yet. Use Fix match first.'],
    [new ApiRequestError('rate_limited', 429), 'You can preview 6 times a minute. Wait a moment.'],
    [new ApiRequestError('bad_gateway', 502), 'TMDB did not answer. Try again.'],
  ])('explains a failure: %#', async (failure, copy) => {
    api.previewMetadataRefresh.mockRejectedValue(failure);
    show();
    expect((await screen.findByRole('alert')).textContent).toBe(copy);
  });
});
