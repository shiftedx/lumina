import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { UserProfile } from '../../../types';
import { ToastProvider } from '../../../ui';
import { rememberSummary, summaryFor } from '../../gallery/titleCache';
import { movieSummary } from '../../../test/galleryFixtures';
import { docFixture } from './editorFixtures';

const api = vi.hoisted(() => ({ getTitleMetadata: vi.fn(), saveMetadataEdits: vi.fn(), undoMetadataBatch: vi.fn(), getMetadataHistory: vi.fn() }));
vi.mock('../../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../../api')>()), ...api }));
vi.mock('./EpisodesTab', () => ({
  default: ({ doc, setField, episodesRevision }: { doc: { title_id: string }; setField: (id: string, key: string, value: unknown, base: unknown) => void; episodesRevision: number }) => (
    <div><output aria-label="episodes revision">{episodesRevision}</output><button onClick={() => setField(doc.title_id, 'name', 'Renamed', 'Severance')} type="button">Rename</button></div>
  ),
}));
const { EditorPage } = await import('./EditorPage');
const { default: HistoryTab } = await import('./HistoryTab');

const user: UserProfile = { id: 'u1', username: 'o', display_name: 'Owner', role: 'admin', is_active: true, can_edit_details: true };
afterEach(() => vi.resetAllMocks());

describe('undo refreshes what it may have touched', () => {
  it('Undo from the Saved toast drops the title cache and bumps the episode table', async () => {
    api.getTitleMetadata.mockResolvedValue(docFixture('series'));
    api.saveMetadataEdits.mockResolvedValue({ titles: [docFixture('series')], conflicts: [], batch_id: 'b1' });
    api.undoMetadataBatch.mockResolvedValue({ batch_id: 'u1', restored: 1, skipped: [] });
    render(<ToastProvider><EditorPage id="t1" onBack={() => undefined} onTab={() => undefined} tab="episodes" user={user} /></ToastProvider>);
    fireEvent.click(await screen.findByRole('button', { name: 'Rename' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save 1 change' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }));
    const revision = Number(screen.getByLabelText('episodes revision').textContent);
    rememberSummary(movieSummary('other'));
    await waitFor(() => expect(api.undoMetadataBatch).toHaveBeenCalledWith('b1'));
    await waitFor(() => expect(Number(screen.getByLabelText('episodes revision').textContent)).toBe(revision + 1));
    expect(summaryFor('other')).toBeNull();
  });

  it('Undo from History uses the page-level refresh', async () => {
    api.getMetadataHistory.mockResolvedValue({ batches: [{ batch_id: 'b1', kind: 'edit', user: null, created_at: new Date().toISOString(), changes: [{ field: 'year', before: 1, after: 2, before_source: 'tmdb', after_source: 'user' }], undone: false, title_count: 1 }], next_cursor: null });
    api.undoMetadataBatch.mockResolvedValue({ batch_id: 'u1', restored: 1, skipped: [] });
    const reload = vi.fn();
    const reloadAfterUndo = vi.fn();
    render(<ToastProvider><HistoryTab doc={docFixture('movie')} draft={{}} reload={reload} reloadAfterUndo={reloadAfterUndo} user={user} {...({} as object)} episodesRevision={0} setField={vi.fn()} setItemLock={vi.fn()} togglePin={vi.fn()} discardField={vi.fn()} /></ToastProvider>);
    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(reloadAfterUndo).toHaveBeenCalledTimes(1));
    expect(reload).not.toHaveBeenCalled();
  });
});
