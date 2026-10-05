/** Test fixtures for the Requests tab, shaped exactly like the API contract. Tests only. */
import type { CatalogDetail, CatalogItem, MediaRequest, Quota, Rail } from './requestsApi';

const art = (path: string) => `https://image.tmdb.org/t/p/w500/${path}.jpg`;

export const movie: CatalogItem = {
  key: 'movie:603', kind: 'movie', tmdb_id: 603, media_type: 'movie', title: 'The Matrix', year: 1999, overview: 'A hacker learns the truth.',
  poster_url: art('matrix'), backdrop_url: art('matrix-bd'), logo_url: art('matrix-logo'), rating: 8.2, genres: ['Action', 'Science Fiction'], status: { state: 'none' },
};
export const show: CatalogItem = {
  key: 'show:1399', kind: 'show', tmdb_id: 1399, tvdb_id: 121361, media_type: 'tv', title: 'Game of Thrones', year: 2011, overview: 'Seven noble families.',
  poster_url: art('got'), backdrop_url: art('got-bd'), rating: 8.4, genres: ['Drama'], status: { state: 'processing', request_id: 'r-2', progress: 0.42 },
};
export const anime: CatalogItem = {
  key: 'anime:16498', kind: 'anime', anilist_id: 16498, tmdb_id: 1429, tvdb_id: 267440, media_type: 'tv', title: 'Attack on Titan', year: 2013,
  poster_url: 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/aot.jpg', genres: ['Action', 'Drama'], status: { state: 'none' },
  anime: { season: 'FALL', season_year: 2026, format: 'TV', episodes: 12, next_episode: { number: 4, airing_at: '2026-10-06T15:00:00Z' }, studios: ['MAPPA'], score: 85 },
};
export const animeFilm: CatalogItem = {
  key: 'anime:199', kind: 'anime', anilist_id: 199, tmdb_id: 129, media_type: 'movie', title: 'Spirited Away', year: 2001, genres: ['Fantasy'],
  poster_url: art('spirited'), status: { state: 'available', library_title_id: 'title-7' }, anime: { format: 'MOVIE', studios: ['Studio Ghibli'] },
};
export const unmapped: CatalogItem = { ...anime, key: 'anime:999', anilist_id: 999, tmdb_id: undefined, tvdb_id: undefined, title: 'Obscure OVA', requestable: false, unrequestable_reason: 'unmapped' };

export const rails: Rail[] = [
  { key: 'anime_this_season', title: 'This season in anime · Fall 2026', kind: 'anime', see_all: '/requests/anime', items: [anime, unmapped] },
  { key: 'trending', title: 'Trending', items: [movie, show, animeFilm] },
  { key: 'popular_movies', title: 'Popular films', kind: 'movie', see_all: '/requests/movies', items: [movie] },
];
export const catalogHome = { hero: [movie, show], rails };

export const showDetail: CatalogDetail = {
  ...show, status: { state: 'none' }, tagline: 'Winter is coming.', runtime: 60, certification: 'TV-MA', networks: ['HBO'], studios: [],
  seasons: [{ number: 0, name: 'Specials', episode_count: 4 }, { number: 1, name: 'Season 1', episode_count: 10 }, { number: 2, name: 'Season 2', episode_count: 10 }],
  trailers: [{ youtube_id: 'KPLWWIOCOOQ', name: 'Official Trailer', type: 'Trailer', official: true }, { youtube_id: 'abc123def45', name: 'Teaser', type: 'Teaser', official: true }],
  cast: [{ name: 'Emilia Clarke', character: 'Daenerys', profile_url: art('ec') }], crew: [{ name: 'David Benioff', job: 'Creator' }],
  recommendations: [movie], similar: [],
};
export const animeDetail: CatalogDetail = {
  ...anime, seasons: [{ number: 1, name: 'Season 1', episode_count: 12 }], trailers: [], cast: [], crew: [], networks: [], studios: ['MAPPA'],
  recommendations: [], similar: [], anime_relations: [animeFilm],
};

export const quotas: Quota[] = [
  { kind: 'movie', can_request: true, auto_approve: true, limit: null, days: null, used: 0, remaining: null },
  { kind: 'show', can_request: true, auto_approve: false, limit: 10, days: 7, used: 7, remaining: 3 },
  { kind: 'anime', can_request: true, auto_approve: false, limit: 10, days: 7, used: 7, remaining: 3 },
];

export const request = (overrides: Partial<MediaRequest> = {}): MediaRequest => ({
  id: 'r-1', kind: 'show', media_type: 'tv', key: 'show:1399', tmdb_id: 1399, title: 'Game of Thrones', year: 2011, poster_url: art('got'), seasons: [1],
  language: null, status: 'pending', requested_by: { id: 'u-1', name: 'Dana' }, followers: [], created_at: '2026-10-01T10:00:00Z', updated_at: '2026-10-01T10:00:00Z',
  ...overrides,
});
