import { chooseSelect, selectLabels } from '../../test/selectChoice';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { TitleMetadataDoc, TitlePage, TitleSummary } from '../../types';

const api = vi.hoisted(() => ({
  listTitles: vi.fn(),
  getTitleMetadata: vi.fn(),
  getMetadataVocabulary: vi.fn(),
  bulkEditMetadata: vi.fn(),
  saveMetadataEdits: vi.fn(),
  undoMetadataBatch: vi.fn(),
  uploadMusicArtwork: vi.fn(),
}));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));
const { MusicCuration } = await import('./MusicCuration');

const owner = { id: 'u1', role: 'admin', can_edit_details: true };
const viewer = { id: 'u2', role: 'viewer', can_edit_details: false };

function title(id: string, type: 'album' | 'artist' = 'album', patch: Partial<TitleSummary> = {}): TitleSummary {
  return {
    id, type, name: type === 'album' ? `Album ${id}` : `Artist ${id}`, year: 2024, artist_name: type === 'album' ? 'Artist A' : null,
    genres: [], added_at: '2026-01-01T00:00:00Z', poster_url: `/api/titles/${id}/image/Primary`,
    user_data: { played: false, is_favorite: false, position_seconds: 0 }, ...patch,
  };
}

function page(items: TitleSummary[], next_cursor: string | null = null, total = items.length): TitlePage {
  return { items, next_cursor, total, start_index: 0 };
}

function metadata(subject: TitleSummary, tag = `tag-${subject.id}`): TitleMetadataDoc {
  return {
    title_id: subject.id, type: subject.type, name: subject.name, locked: false, parent: null,
    fields: {
      name: { value: subject.name, source: 'musicbrainz', locked: false, kept: null },
      genres: { value: ['Ambient'], source: 'musicbrainz', locked: false, kept: null },
      year: { value: subject.year, source: 'musicbrainz', locked: false, kept: null },
    },
    images: [{ type: 'Primary', index: 0, url: `/api/current/${subject.id}`, tag, origin: 'tmdb', source: 'musicbrainz', locked: false, width: 800, height: 800 }],
    can_identify: false, tmdb_configured: false, history_count: 0,
  };
}

function renderPage(items = [title('one')], user = owner) {
  api.listTitles.mockResolvedValue(page(items));
  api.getMetadataVocabulary.mockResolvedValue([]);
  render(<MusicCuration onOpenTitle={vi.fn()} user={user} />);
}

afterEach(() => vi.resetAllMocks());

describe('MusicCuration', () => {
  it('keeps selected IDs when loading another cursor page and labels search as loaded-only', async () => {
    const first = title('one');
    const second = title('two');
    api.listTitles.mockImplementation(({ cursor }: { cursor?: string | null }) => Promise.resolve(cursor ? page([second], null, 2) : page([first], 'next', 2)));
    api.getMetadataVocabulary.mockResolvedValue([]);
    render(<MusicCuration onOpenTitle={vi.fn()} user={owner} />);

    await screen.findByText('Album one');
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Album one' }));
    expect(screen.getByText(/1 title selected across loaded pages/)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Load more' }));
    expect(await screen.findByText('Album two')).toBeTruthy();
    expect((screen.getByRole('checkbox', { name: 'Select Album one' }) as HTMLInputElement).checked).toBe(true);
    expect(api.listTitles).toHaveBeenNthCalledWith(2, { type: 'album', sort: 'name', cursor: 'next', limit: 60 });
    expect(screen.getByText(/Searches titles loaded so far/)).toBeTruthy();
  });

  it('waits for an explicit preview before saving, reports skips, and offers undo', async () => {
    renderPage([title('one'), title('two')]);
    await screen.findByText('Album one');
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Album one' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Album two' }));
    const genreInput = screen.getByRole('group', { name: 'Genres values' }).querySelector('input');
    expect(genreInput).toBeTruthy();
    await userEvent.type(genreInput!, 'Jazz{Enter}');
    expect(api.bulkEditMetadata).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Preview changes' }));
    expect(api.bulkEditMetadata).not.toHaveBeenCalled();
    expect(screen.getByText('Add Genres: Jazz')).toBeTruthy();

    api.bulkEditMetadata.mockResolvedValue({ batch_id: 'batch-1', applied: 1, skipped: [{ title_id: 'two', reason: 'locked_item' }] });
    api.undoMetadataBatch.mockResolvedValue({ batch_id: 'undo-1', restored: 1, skipped: [] });
    await userEvent.click(screen.getByRole('button', { name: 'Apply changes' }));
    expect(api.bulkEditMetadata).toHaveBeenCalledWith({ title_ids: ['one', 'two'], ops: [{ op: 'add', field: 'genres', values: ['Jazz'] }] });
    expect(await screen.findByText('Updated 1 title.')).toBeTruthy();
    expect(screen.getByText('1 skipped')).toBeTruthy();
    expect(screen.getByText(/Item is locked/)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(api.undoMetadataBatch).toHaveBeenCalledWith('batch-1');
    expect(await screen.findByText('Undone 1 title.')).toBeTruthy();
  });

  it('uses the metadata editor batch for compatible album-only fields after preview', async () => {
    const album = title('album-1');
    renderPage([album]);
    await screen.findByText('Album album-1');
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Album album-1' }));
    chooseSelect(screen.getByRole('combobox', { name: 'Field' }), 'Label');
    await userEvent.type(screen.getByRole('textbox', { name: 'New label' }), 'Night Records');
    api.getTitleMetadata.mockResolvedValue(metadata(album));
    await userEvent.click(screen.getByRole('button', { name: 'Preview changes' }));
    expect(api.saveMetadataEdits).not.toHaveBeenCalled();
    api.saveMetadataEdits.mockResolvedValue({ batch_id: 'edit-1', titles: [metadata(album)], conflicts: [] });
    expect(screen.getByText('Set Label: Night Records')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Apply changes' }));
    expect(api.saveMetadataEdits).toHaveBeenCalledWith([{ title_id: 'album-1', changes: { label: { value: 'Night Records', base: null } } }]);
  });

  it('reads current image tags before applying the artwork upload and shows before/after context', async () => {
    const album = title('album-1');
    renderPage([album]);
    await screen.findByText('Album album-1');
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Album album-1' }));
    const file = new File(['image bytes'], 'cover.jpg', { type: 'image/jpeg' });
    await userEvent.upload(screen.getByLabelText('Album cover image'), file);
    api.getTitleMetadata.mockResolvedValue(metadata(album, 'current-cover-tag'));
    await userEvent.click(screen.getByRole('button', { name: 'Preview artwork upload' }));
    expect(api.uploadMusicArtwork).not.toHaveBeenCalled();
    expect(await screen.findByText('Current')).toBeTruthy();
    api.uploadMusicArtwork.mockResolvedValue({ batch_id: 'art-batch', applied: 1, skipped: [] });
    await userEvent.click(screen.getByRole('button', { name: 'Apply artwork' }));
    expect(api.uploadMusicArtwork).toHaveBeenCalledWith(['album-1'], file, { 'album-1': 'current-cover-tag' }, 'Primary');
  });

  it('keeps members read-only and hides bulk controls', async () => {
    renderPage([title('one')], viewer);
    expect(await screen.findByText('Album one')).toBeTruthy();
    expect(screen.queryByRole('checkbox', { name: 'Select Album one' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Select all loaded' })).toBeNull();
    expect(screen.getByText(/detail editing is not enabled/)).toBeTruthy();
  });

  it('hides album-only bulk fields on the artist view', async () => {
    renderPage([title('artist-1', 'artist')]);
    await screen.findByText('Artist artist-1');
    await userEvent.click(screen.getByRole('radio', { name: 'Artists' }));
    await screen.findByRole('checkbox', { name: 'Select Artist artist-1' });
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Artist artist-1' }));
    const field = screen.getByRole('combobox', { name: 'Field' });
    expect(selectLabels(field)).toEqual(['Genres', 'Tags', 'Styles', 'Mood']);
  });
});
