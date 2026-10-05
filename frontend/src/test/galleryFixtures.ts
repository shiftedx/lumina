/** Typed gallery fixtures shared by the Vitest suites and the gallery Playwright mocks. */
import type { AlbumTrack, KeyScenes, LibraryItem, LibrarySections, TitleArt, TitleDetail, TitleFacets, TitlePage, TitleSummary, TitleUserData } from '../types';

export const FIXTURE_AT = '2026-09-01T12:00:00';
export const SIG = 'AAAAAAAAAAAAAAAAAAAAAA';
export const PREVIEW = 'data:image/webp;base64,UklGRg==';
const KEY = (id: string, type: string) => `${(id + type).replace(/[^0-9a-f]/g, '').padEnd(64, '0').slice(0, 64)}`;

export function titleArt(id: string, type: 'Primary' | 'Backdrop' | 'Logo', patch: Partial<TitleArt> = {}, episode = false): TitleArt {
  const widths = type === 'Backdrop' ? [960, 1920] : type === 'Logo' ? [600] : episode ? [400] : [240, 480];
  return {
    url: `/api/titles/${id}/images/${type}?tag=0123456789abcdef`, rendition: `/api/art/${SIG}/${id}/${type}/${KEY(id, type)}-{w}.webp`, widths,
    width: null, height: null, preview: type === 'Logo' ? null : PREVIEW, dominant: type === 'Logo' ? null : '#2a3b4c', accent: type === 'Logo' ? null : '#c08a4b', ...patch,
  };
}

export const userData = (patch: Partial<TitleUserData> = {}): TitleUserData => ({ played: false, is_favorite: false, position_seconds: 0, ...patch });

export function movieSummary(id = 'movie-1', patch: Partial<TitleSummary> = {}): TitleSummary {
  return {
    id, type: 'movie', name: 'Northern Lantern', year: 2019, genres: ['Adventure'], runtime_seconds: 6720, play_item_id: `item-${id}`,
    poster_url: `/api/titles/${id}/images/Primary?tag=0123456789abcdef`, backdrop_url: `/api/titles/${id}/images/Backdrop?tag=0123456789abcdef`,
    poster: titleArt(id, 'Primary'), backdrop: { ...titleArt(id, 'Backdrop'), preview: null }, added_at: FIXTURE_AT, user_data: userData(), ...patch,
  };
}

export function seriesSummary(id = 'series-1', patch: Partial<TitleSummary> = {}): TitleSummary {
  return movieSummary(id, { type: 'series', name: 'Harbor Lights', year: 2021, genres: ['Drama'], runtime_seconds: null, play_item_id: null, user_data: userData({ unplayed_count: 4 }), ...patch });
}

export function episodeSummary(season: number, index: number, patch: Partial<TitleSummary> = {}): TitleSummary {
  const id = `ep-${season}-${index}`;
  return {
    ...movieSummary(id), type: 'episode', name: `Episode ${index}`, year: null, genres: [], runtime_seconds: 2640, index_number: index, season_number: season,
    parent_id: `season-${season}`, series_id: 'series-1', series_name: 'Harbor Lights', overview: 'The keepers argue about the lamp.',
    poster: titleArt(id, 'Primary', {}, true), backdrop: null, backdrop_url: null, ...patch,
  };
}

export function titleDetail(summary: TitleSummary, patch: Partial<TitleDetail> = {}): TitleDetail {
  return {
    ...summary, backdrop: summary.backdrop ? { ...summary.backdrop, preview: PREVIEW } : summary.backdrop, studios: [], provider_ids: {}, people: [], versions: [],
    extras: [], children: [], has_recap: false, logo: titleArt(summary.id, 'Logo'), episode_count: summary.type === 'series' ? 24 : null, best_height: 2160, ...patch,
  };
}

export function titlePage(items: TitleSummary[], patch: Partial<TitlePage> = {}): TitlePage {
  return { items, next_cursor: null, total: items.length, letters: null, start_index: 0, ...patch };
}

export const facets: TitleFacets = {
  genres: [{ name: 'Adventure', count: 12 }, { name: 'Drama', count: 30 }], years: { min: 1954, max: 2026 }, resolutions: [{ value: '4k', count: 8 }, { value: '1080p', count: 34 }],
};

export const keyScenes: KeyScenes = {
  available: true, title_id: 'movie-1', item_id: 'item-movie-1',
  scenes: [{ start_ms: 761_000, quote: 'Carry it until the ice sings.', caption: 'The grandfather explains the lamp.' }],
};

// ---- Library gallery builders ----

/** A 1:1 cover's art: the square rendition widths. */
export const squareArt = (id: string, patch: Partial<TitleArt> = {}): TitleArt => titleArt(id, 'Primary', { widths: [240, 480, 960], ...patch });

export function albumSummary(id = 'album-1', patch: Partial<TitleSummary> = {}): TitleSummary {
  return movieSummary(id, {
    type: 'album', name: 'Album One', year: 2019, genres: ['Folk'], runtime_seconds: null, play_item_id: null, parent_id: 'artist-1',
    artist_name: 'Artist A', child_count: 12, category: null, poster: squareArt(id), backdrop: null, backdrop_url: null, ...patch,
  });
}

export function artistSummary(id = 'artist-1', patch: Partial<TitleSummary> = {}): TitleSummary {
  return albumSummary(id, { type: 'artist', name: 'Artist A', year: null, genres: [], parent_id: null, artist_name: null, child_count: 4, ...patch });
}

export function albumTrack(number: number, patch: Partial<AlbumTrack> = {}): AlbumTrack {
  return { item_id: `track-${number}`, disc: 1, number, name: `Song ${number}`, artist: null, duration_seconds: 180 + number, user_data: userData(), ...patch };
}

export function albumDetail(summary: TitleSummary = albumSummary(), tracks: AlbumTrack[] = [1, 2, 3].map((number) => albumTrack(number)), patch: Partial<TitleDetail> = {}): TitleDetail {
  return titleDetail(summary, { logo: null, episode_count: null, best_height: null, tracks, ...patch });
}

export const librarySections = (patch: Partial<LibrarySections> = {}): LibrarySections => ({
  movies: 1735, shows: 812, anime: 29, albums: 110, artists: 108, saved_audio: 6, youtube: 318, recordings: 12, deleted: 0, ...patch,
});

/** A saved YouTube video; pass `kind` for a recording or saved audio. */
export function stillItem(id = 'video-1', patch: Partial<LibraryItem> = {}): LibraryItem {
  return {
    id, title: 'Harbor walk at dawn', kind: 'video', uploader: 'Chan', duration: 761, extractor: 'youtube', status: 'available', visibility: 'shared',
    downloaded_at: FIXTURE_AT, created_at: FIXTURE_AT, updated_at: FIXTURE_AT, progress: null, ...patch,
  };
}
