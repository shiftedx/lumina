import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { HistoryBatch, UserProfile } from '../../../types';
import { ToastProvider } from '../../../ui';
import { docFixture } from './editorFixtures';

const api = vi.hoisted(() => ({ getMetadataHistory: vi.fn(), undoMetadataBatch: vi.fn() }));
vi.mock('../../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../../api')>()), ...api }));
const { default: HistoryTab, ago } = await import('./HistoryTab');

const user: UserProfile = { id: 'u1', username: 'o', display_name: 'Owner', role: 'admin', is_active: true, can_edit_details: true };
const batch = (patch: Partial<HistoryBatch> = {}): HistoryBatch => ({
  batch_id: 'b1', kind: 'edit', user: { id: 'u1', display_name: 'Alexandria' }, created_at: new Date(Date.now() - 3 * 60_000).toISOString(),
  changes: [
    { field: 'overview', before: 'Old', after: 'New', before_source: 'tmdb', after_source: 'user' },
    { field: 'year', before: 2020, after: null, before_source: 'tmdb', after_source: 'user' },
  ], undone: false, title_count: 1, ...patch,
});
const reload = vi.fn();
const props = { doc: docFixture('movie'), draft: {}, user, reload } as unknown as Parameters<typeof HistoryTab>[0];
const show = () => render(<ToastProvider><HistoryTab {...props} /></ToastProvider>);
afterEach(() => vi.resetAllMocks());

describe('HistoryTab', () => {
  it('lists batches newest first as who, time and count', async () => {
    api.getMetadataHistory.mockResolvedValue({ batches: [batch(), batch({ batch_id: 'b2', changes: [batch().changes[0]!], user: null })], next_cursor: null });
    show();
    expect(await screen.findByText('Alexandria · 3 min ago · 2 changes')).toBeTruthy();
    expect(screen.getByText(/^Former member · .* · 1 change$/)).toBeTruthy();
  });

  it('expands a batch into labelled before and after, clears as (cleared)', async () => {
    api.getMetadataHistory.mockResolvedValue({ batches: [batch()], next_cursor: null });
    const { container } = show();
    await screen.findByText(/Alexandria/);
    const text = container.querySelector('details')!.textContent!;
    expect(text).toContain('Overview');
    expect(text).toContain('Old → New');
    expect(text).toContain('2020 → (cleared)');
  });

  it('reads people, provider id and image changes as names, not [object Object]', async () => {
    api.getMetadataHistory.mockResolvedValue({ batches: [batch({ changes: [
      { field: 'people', before: [], after: [{ name: 'Ada', role: 'Director' }, { name: 'Bo' }], before_source: 'tmdb', after_source: 'user' },
      { field: 'provider_ids', before: { Tmdb: '1' }, after: { Tmdb: '2' }, before_source: 'tmdb', after_source: 'user' },
      { field: 'images.Backdrop.1', before: null, after: { upload: 'abc', tag: 'abc' }, before_source: 'tmdb', after_source: 'user' },
    ] })], next_cursor: null });
    const { container } = show();
    await screen.findByText(/Alexandria/);
    const text = container.querySelector('details')!.textContent!;
    expect(text).not.toContain('[object');
    expect(text).toContain('(cleared) → Ada (Director), Bo');
    expect(text).toContain('Tmdb: 1 → Tmdb: 2');
    expect(text).toContain('Provider IDs');
    expect(text).toContain('Backdrop');
    expect(text).toContain('(cleared) → abc');
  });

  it('undoes a batch, reports skips, and refetches', async () => {
    api.getMetadataHistory.mockResolvedValue({ batches: [batch()], next_cursor: null });
    api.undoMetadataBatch.mockResolvedValue({ batch_id: 'u', restored: 0, skipped: [{ title_id: 't', field: 'a', reason: 'changed_since' }, { title_id: 't', field: 'b', reason: 'changed_since' }] });
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }));
    expect(await screen.findByText('2 fields changed since, so they were left as they are.')).toBeTruthy();
    expect(api.undoMetadataBatch).toHaveBeenCalledWith('b1');
    expect(reload).toHaveBeenCalled();
    expect(api.getMetadataHistory).toHaveBeenCalledTimes(2);
  });

  it('an undone batch reads Undone and has no Undo button', async () => {
    api.getMetadataHistory.mockResolvedValue({ batches: [batch({ undone: true })], next_cursor: null });
    show();
    await screen.findByText('Undone');
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
  });

  it('wraps long values with the word-wrap class and does not cut them', async () => {
    const long = 'x'.repeat(400);
    api.getMetadataHistory.mockResolvedValue({ batches: [batch({ changes: [{ field: 'overview', before: long, after: 'y', before_source: null, after_source: 'user' }] })], next_cursor: null });
    const { container } = show();
    await screen.findByText(/Alexandria/);
    expect(container.querySelector('.ed-history-value')!.textContent).toContain(long);
  });

  it('loads older batches with next_cursor and hides the button at the end', async () => {
    api.getMetadataHistory.mockResolvedValueOnce({ batches: [batch()], next_cursor: 'c1' }).mockResolvedValueOnce({ batches: [batch({ batch_id: 'b9', kind: 'image', changes: [] })], next_cursor: null });
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Show older' }));
    await screen.findByText(/Artwork/);
    expect(api.getMetadataHistory).toHaveBeenLastCalledWith('t1', 'c1');
    expect(screen.queryByRole('button', { name: 'Show older' })).toBeNull();
  });

  it('shows the empty state and an error with Try again', async () => {
    api.getMetadataHistory.mockResolvedValueOnce({ batches: [], next_cursor: null });
    const first = show();
    expect(await screen.findByText('No edits yet.')).toBeTruthy();
    first.unmount();
    api.getMetadataHistory.mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce({ batches: [batch()], next_cursor: null });
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    expect(await screen.findByText(/Alexandria/)).toBeTruthy();
  });

  it('names batches of other kinds sensibly', async () => {
    const kinds = ['image', 'item_lock', 'revert', 'undo', 'lock', 'bulk'] as const;
    api.getMetadataHistory.mockResolvedValue({ batches: kinds.map((kind, i) => batch({ batch_id: `k${i}`, kind, changes: [] })), next_cursor: null });
    const { container } = show();
    await screen.findAllByText(/Alexandria/);
    const text = container.textContent!;
    for (const label of ['Artwork', 'Lock this item', 'Reverted to source', 'Undo', 'Locked fields', 'Bulk edit']) expect(text).toContain(label);
  });

  it('ago words', () => {
    const now = Date.parse('2026-10-02T12:00:00Z');
    const at = (ms: number) => new Date(now - ms).toISOString();
    expect(ago(at(5_000), now)).toBe('just now');
    expect(ago(at(3 * 60_000), now)).toBe('3 min ago');
    expect(ago(at(2 * 3_600_000), now)).toBe('2 h ago');
    expect(ago(at(4 * 86_400_000), now)).toBe('4 days ago');
    expect(ago(at(40 * 86_400_000), now)).toBe(new Date(now - 40 * 86_400_000).toLocaleDateString());
  });
});
