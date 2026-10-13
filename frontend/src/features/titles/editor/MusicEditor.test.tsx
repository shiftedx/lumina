import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { FieldState, TitleMetadataDoc, UserProfile } from '../../../types';
import { ToastProvider } from '../../../ui';
import { docFixture, fieldState } from './editorFixtures';
import { FIELD_META, tabsFor } from './editorModel';

const api = vi.hoisted(() => ({ getTitleMetadata: vi.fn(), saveMetadataEdits: vi.fn(), getMetadataVocabulary: vi.fn() }));
vi.mock('../../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../../api')>()), ...api }));
const { EditorPage } = await import('./EditorPage');

const owner: UserProfile = { id: 'u1', username: 'owner', display_name: 'Owner', role: 'admin', is_active: true, can_edit_details: true };
const musicBrainzId = '12345678-1234-1234-1234-123456789abc';

function musicDoc(type: 'album' | 'artist' = 'album'): TitleMetadataDoc {
  const album = type === 'album';
  const fields: Record<string, FieldState> = {
    name: fieldState(album ? 'Night Walks' : 'The Night Walkers', 'musicbrainz'),
    sort_name: fieldState(album ? 'Night Walks' : 'The Night Walkers', 'musicbrainz'),
    country: fieldState('US', 'musicbrainz'),
    genres: fieldState(['Ambient'], 'musicbrainz'),
    styles: fieldState(['Drone'], 'musicbrainz'),
    mood: fieldState(['Quiet'], 'musicbrainz'),
    tags: fieldState(['Night'], 'nfo'),
    overview: fieldState('A quiet record for late walks.', 'musicbrainz'),
    credits: fieldState([{ name: 'Composer One', role: 'Composer', instrument: null }], 'musicbrainz'),
    added_at: fieldState('2026-01-01T00:00:00', 'path'),
    provider_ids: fieldState({ MusicBrainz: musicBrainzId }, 'musicbrainz'),
  };
  if (album) Object.assign(fields, {
    year: fieldState(2024, 'musicbrainz'),
    original_year: fieldState(2022, 'musicbrainz'),
    premiered: fieldState('2024-04-12', 'musicbrainz'),
    release_type: fieldState('album', 'musicbrainz'),
    edition: fieldState('Deluxe', 'musicbrainz'),
    label: fieldState('Lumina Records', 'musicbrainz'),
    catalogue_number: fieldState('LUM-024', 'musicbrainz'),
  });
  else Object.assign(fields, {
    origin: fieldState('Chicago', 'musicbrainz'),
    active_since: fieldState('2010', 'musicbrainz'),
    active_until: fieldState(null, 'musicbrainz'),
  });
  return docFixture(type, {
    name: album ? 'Night Walks' : 'The Night Walkers',
    fields,
  });
}

function open(doc: TitleMetadataDoc, tab: 'details' | 'ids' = 'details') {
  api.getTitleMetadata.mockResolvedValue(doc);
  api.getMetadataVocabulary.mockResolvedValue([]);
  render(<ToastProvider><EditorPage id={doc.title_id} onBack={() => undefined} onTab={() => undefined} tab={tab} user={owner} /></ToastProvider>);
}

afterEach(() => vi.resetAllMocks());

describe('music metadata editor', () => {
  it('shows album and artist fields in compact, guided groups', async () => {
    open(musicDoc());
    expect(await screen.findByRole('textbox', { name: 'Edition' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Release identity' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Release details' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Listening context' })).toBeTruthy();
    expect(screen.getByRole('combobox', { name: 'Release type' })).toBeTruthy();
    expect([...screen.getByRole('combobox', { name: 'Release type' }).querySelectorAll('option')].map((option) => option.value)).toEqual(['', 'album', 'single', 'ep', 'compilation', 'live', 'soundtrack']);
    expect(screen.queryByRole('textbox', { name: 'Active since' })).toBeNull();

    expect(FIELD_META.original_year.label).toBe('Original release year');
    expect(FIELD_META.catalogue_number.label).toBe('Catalogue number');
  });

  it('shows the artist-only activity fields and no video-only controls', async () => {
    open(musicDoc('artist'));
    expect(await screen.findByRole('textbox', { name: 'Origin' })).toBeTruthy();
    expect(screen.getByRole('textbox', { name: 'Active since' })).toBeTruthy();
    expect(screen.queryByRole('combobox', { name: 'Release type' })).toBeNull();
    expect(screen.queryByRole('textbox', { name: 'Overview' })).toBeTruthy();
  });

  it('keeps music to Details, Artwork, IDs, and History while film and TV tabs remain unchanged', () => {
    expect(tabsFor('album').map((tab) => tab.value)).toEqual(['details', 'artwork', 'ids', 'history']);
    expect(tabsFor('artist').map((tab) => tab.value)).toEqual(['details', 'artwork', 'ids', 'history']);
    expect(tabsFor('movie').map((tab) => tab.value)).toEqual(['details', 'people', 'artwork', 'ids', 'history']);
    expect(tabsFor('series').map((tab) => tab.value)).toEqual(['details', 'people', 'artwork', 'ids', 'episodes', 'history']);
  });

  it('edits and saves the MusicBrainz UUID without changing film/TV ID handling', async () => {
    const doc = musicDoc();
    open(doc, 'ids');
    const input = await screen.findByRole('textbox', { name: 'MusicBrainz UUID' });
    const next = 'abcdefab-cdef-abcd-efab-cdefabcdefab';
    await userEvent.clear(input);
    await userEvent.type(input, next);
    expect(screen.getByRole('link', { name: 'Open MusicBrainz UUID' }).getAttribute('href')).toBe(`https://musicbrainz.org/release/${next}`);
    api.saveMetadataEdits.mockResolvedValue({ batch_id: 'music-batch', titles: [doc], conflicts: [] });
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }));
    expect(api.saveMetadataEdits).toHaveBeenCalledWith([{ title_id: 't1', changes: { provider_ids: { value: { MusicBrainz: next }, base: { MusicBrainz: musicBrainzId } } } }]);
  });

  it('saves structured collector credits with their optimistic base', async () => {
    const doc = musicDoc();
    open(doc);
    const name = await screen.findByRole('textbox', { name: 'Name 1' });
    await userEvent.clear(name);
    await userEvent.type(name, 'Composer Two');
    await userEvent.type(screen.getByRole('textbox', { name: 'Instrument 1' }), 'Piano');
    api.saveMetadataEdits.mockResolvedValue({ batch_id: 'credit-batch', titles: [doc], conflicts: [] });
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }));
    expect(api.saveMetadataEdits).toHaveBeenCalledWith([{ title_id: 't1', changes: {
      credits: { value: [{ name: 'Composer Two', role: 'Composer', instrument: 'Piano' }], base: doc.fields.credits.value },
    } }]);
  });

  it('keeps TMDB, IMDb, and TVDB IDs and links for a movie editor', async () => {
    const movie = docFixture('movie');
    movie.fields.provider_ids = fieldState({ Tmdb: '438631', Imdb: 'tt1160419', Tvdb: '77' });
    open(movie, 'ids');
    expect((await screen.findByRole('textbox', { name: 'TMDB' }) as HTMLInputElement).value).toBe('438631');
    expect(screen.getByRole('link', { name: 'Open TMDB' }).getAttribute('href')).toBe('https://www.themoviedb.org/movie/438631');
    expect(screen.getByRole('link', { name: 'Open TVDB' }).getAttribute('href')).toBe('https://thetvdb.com/dereferrer/movie/77');
  });
});
