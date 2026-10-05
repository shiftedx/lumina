import { LIBRARY_LENSES, type LibraryLens, titleLens } from '../features/gallery/libraryLens';
import type { LibraryPlace } from '../features/gallery/LibraryTabs';
import type { TitleCategory, TitleType } from '../types';

export type { LibraryLens } from '../features/gallery/libraryLens';
/** A channel page tab as the UI names it; 'live' is the API's 'streams'. */
export const CHANNEL_PAGE_TABS = ['videos', 'live', 'shorts', 'playlists', 'library'] as const;
export type ChannelPageTab = (typeof CHANNEL_PAGE_TABS)[number];

export type StreamingView = 'home' | 'live' | 'search' | 'channels';
export type StreamingProvider = 'youtube' | 'twitch' | 'kick';

/** Signed-in surfaces. 'subscriptions' is only a follow or channel page now; the list is Streaming's channels view. Music is a Library lens (/library/music) since the library gallery. */
export type Surface = 'home' | 'streaming' | 'requests' | 'subscriptions' | 'library' | 'downloads' | 'settings' | 'watch';

/** Requests: what a member can discover and ask the household's Sonarr/Radarr for. */
export type RequestKind = 'movie' | 'show' | 'anime';
export type RequestsBrowse = 'movies' | 'shows' | 'anime';
export type RequestsView = 'discover' | RequestsBrowse | 'mine' | 'manage';
const REQUESTS_VIEWS: readonly RequestsView[] = ['movies', 'shows', 'anime', 'mine', 'manage'];
export type RequestsRoute =
  | { surface: 'requests'; view: 'discover' | 'mine' | 'manage' }
  | { surface: 'requests'; view: RequestsBrowse; section?: string; genre?: string }
  | { surface: 'requests'; view: 'search'; query: string }
  | { surface: 'requests'; view: 'title'; kind: RequestKind; id: number };

/** Settings sections in sidebar order: "You" (everyone), then "Server" (vault owners). */
export const SETTINGS_SECTION_IDS = [
  'account', 'playback', 'appearance', 'discovery', 'streaming', 'downloads', 'apps', 'privacy', 'about',
  'overview', 'activity', 'library', 'media', 'requests', 'transcoding', 'ai', 'members', 'tasks', 'backups', 'diagnostics',
] as const;
export type SettingsSectionId = (typeof SETTINGS_SECTION_IDS)[number];

/** The metadata editor's sections; a tab is in-page state. */
export const EDITOR_TABS = ['details', 'people', 'artwork', 'ids', 'episodes', 'history'] as const;
export type EditorTab = (typeof EDITOR_TABS)[number];

/** Where each old Administration tab lives in Settings; any other /admin tab opens Overview. */
const ADMIN_REDIRECTS: Readonly<Record<string, SettingsSectionId>> = {
  overview: 'overview', members: 'members', tasks: 'tasks', storage: 'library', imports: 'library', media: 'media', ai: 'ai', backups: 'backups', diagnostics: 'diagnostics',
};
const ownKey = (record: object, key: string) => Object.prototype.hasOwnProperty.call(record, key);

/** One canonical, bookmarkable app location. `watch` carries exactly one media reference; title pages are a Library lens. */
export type AppRoute =
  | { surface: Exclude<Surface, 'watch' | 'streaming' | 'settings' | 'subscriptions' | 'requests'> }
  | RequestsRoute
  | { surface: 'streaming'; view: StreamingView; provider?: StreamingProvider; query?: string; rail?: string }
  | { surface: 'subscriptions'; youtubeChannelId: string; tab?: ChannelPageTab }
  | { surface: 'subscriptions'; channelUrl: string }
  | { surface: 'settings'; section?: SettingsSectionId }
  /** A member page under Settings → Members: /settings/members/<user id>. */
  | { surface: 'settings'; section: 'members'; memberId: string }
  | { surface: 'subscriptions'; channelId: string }
  | { surface: 'library'; view: LibraryLens; wall?: string }
  | { surface: 'library'; collections: 'list'; create?: 'collection' | 'smart' }
  | { surface: 'library'; collections: 'detail'; collectionId: string }
  | { surface: 'library'; titleId: string; edit: true; tab?: EditorTab }
  | { surface: 'library'; titleId: string; season?: number }
  | { surface: 'watch'; libraryId: string; startSeconds?: number }
  | { surface: 'watch'; url: string };

const PLAIN_SURFACES = ['library', 'downloads'] as const;
const PROVIDERS: readonly StreamingProvider[] = ['twitch', 'kick'];
// A lens with its own address; /library itself is the All landing.
const LENS_PATH = new RegExp(`^/library/(${LIBRARY_LENSES.join('|')})$`);
const LIBRARY_ID = /^[A-Za-z0-9_-]{1,64}$/;
const MAX_QUERY = 200;
const MAX_URL = 2048;
// Gallery wall state: the query string without '?', kept opaque here and validated by each lens.
const MAX_WALL = 1000;
const MAX_START_SECONDS = 86_400;
// canonical channel ids only; a rail key is a snapshot category key or one of the surface's own.
const YOUTUBE_CHANNEL_PATH = /^\/channel\/youtube\/(UC[0-9A-Za-z_-]{22})$/;
const RAIL_KEY = /^[a-z0-9_-]{1,40}$/;
const YOUTUBE_CHANNEL_HOSTS = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com']);

/** A channel address the resolver may send to the server: https, a YouTube host, an @handle, /c/, /user/ or /channel/ path. */
function youtubeChannelUrl(value: string | null): string | null {
  if (!value || value.length > MAX_URL) return null;
  try {
    const parsed = new URL(value);
    const first = parsed.pathname.split('/').filter(Boolean)[0] ?? '';
    const channelPath = (first.startsWith('@') && first.length > 1) || (['c', 'user', 'channel'].includes(first) && parsed.pathname.split('/').filter(Boolean).length >= 2);
    return parsed.protocol === 'https:' && YOUTUBE_CHANNEL_HOSTS.has(parsed.hostname) && channelPath ? value : null;
  } catch {
    return null;
  }
}

const railOf = (params: URLSearchParams): string | null => {
  const rail = params.get('rail');
  return rail && RAIL_KEY.test(rail) ? rail : null;
};

function safeRemoteUrl(value: string | null): string | null {
  if (!value || value.length > MAX_URL) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? value : null;
  } catch {
    return null;
  }
}

// A TMDB id (movie/show) or an AniList id (anime): a positive integer, never a path.
const CATALOG_ID = /^[1-9]\d{0,9}$/;
const REQUEST_SECTION = /^[a-z_]{1,32}$/;
// A TMDB genre id or an AniList genre name ("Slice of Life", "Sci-Fi").
const REQUEST_GENRE = /^[A-Za-z0-9][A-Za-z0-9 &'-]{0,39}$/;

/** /requests[/movies|shows|anime|mine|manage], /requests/search?q=, /requests/title/{kind}/{id}. Anything else under /requests is Discover. */
function parseRequests(path: string, params: URLSearchParams): RequestsRoute | null {
  if (path !== '/requests' && !path.startsWith('/requests/')) return null;
  const discover: RequestsRoute = { surface: 'requests', view: 'discover' };
  const view = REQUESTS_VIEWS.find((value) => path === `/requests/${value}`);
  if (view === 'movies' || view === 'shows' || view === 'anime') {
    // A rail's See all lands here with ?section= (and a genre chip adds ?genre=); the server validates the value itself.
    const section = params.get('section') ?? '';
    const genre = params.get('genre') ?? '';
    return { surface: 'requests', view, ...(REQUEST_SECTION.test(section) ? { section } : {}), ...(REQUEST_GENRE.test(genre) ? { genre } : {}) };
  }
  if (view) return { surface: 'requests', view };
  if (path === '/requests/search') {
    const query = (params.get('q') || '').trim().slice(0, MAX_QUERY);
    return query ? { surface: 'requests', view: 'search', query } : discover;
  }
  const title = /^\/requests\/title\/(movie|show|anime)\/([^/]+)$/.exec(path);
  if (title && CATALOG_ID.test(title[2]) && Number(title[2]) <= 2_147_483_647) return { surface: 'requests', view: 'title', kind: title[1] as RequestKind, id: Number(title[2]) };
  return discover;
}

/**
 * Parses a browser location into a route. Anything unknown, malformed or oversized
 * resolves to Home, so a crafted link can never start a fetch for an unsafe reference.
 */
export function parseRoute(pathname: string, search: string): AppRoute {
  const path = pathname.replace(/\/+$/, '') || '/';
  const params = new URLSearchParams(search);
  // Old addresses (Live, Explore, Subscriptions) resolve to their Streaming equivalent; the app's first address sync replaces them.
  const streamingPath = path === '/live' ? '/streaming/live' : path === '/explore' ? (params.get('q')?.trim() ? '/streaming/search' : '/streaming') : path === '/subscriptions' ? '/streaming/channels' : path;
  const streaming = /^\/streaming(?:\/(live|search|channels))?$/.exec(streamingPath);
  if (streaming) {
    const view = (streaming[1] ?? 'home') as StreamingView;
    const found = PROVIDERS.find((value) => value === params.get('provider'));
    const query = view === 'search' ? (params.get('q') || '').trim().slice(0, MAX_QUERY) : '';
    const rail = view === 'live' || view === 'home' ? railOf(params) : null;
    return { surface: 'streaming', view: view === 'search' && !query ? 'home' : view, ...(found ? { provider: found } : {}), ...(query ? { query } : {}), ...(rail && !query ? { rail } : {}) };
  }
  const requests = parseRequests(path, params);
  if (requests) return requests;
  const youtubeChannel = YOUTUBE_CHANNEL_PATH.exec(path);
  if (youtubeChannel) {
    const tab = CHANNEL_PAGE_TABS.find((value) => value === params.get('tab'));
    return tab && tab !== 'videos' ? { surface: 'subscriptions', youtubeChannelId: youtubeChannel[1], tab } : { surface: 'subscriptions', youtubeChannelId: youtubeChannel[1] };
  }
  if (path === '/channel') {
    const channelUrl = youtubeChannelUrl(params.get('url'));
    if (channelUrl) return { surface: 'subscriptions', channelUrl };
  }
  const admin = /^\/admin(?:\/([a-z]+))?$/i.exec(path);
  const tab = admin?.[1]?.toLowerCase();
  // Own keys only: `/admin/constructor` must not resolve to an Object.prototype member.
  if (admin) return { surface: 'settings', section: tab && ownKey(ADMIN_REDIRECTS, tab) ? ADMIN_REDIRECTS[tab] : 'overview' };
  const member = /^\/settings\/members\/([^/]+)$/.exec(path);
  if (member) {
    let id = '';
    try { id = decodeURIComponent(member[1]); } catch { /* malformed escape */ }
    return LIBRARY_ID.test(id) ? { surface: 'settings', section: 'members', memberId: id } : { surface: 'settings', section: 'members' };
  }
  const settings = /^\/settings(?:\/([A-Za-z]+))?$/.exec(path);
  if (settings) {
    const section = SETTINGS_SECTION_IDS.find((id) => id === settings[1]);
    return section ? { surface: 'settings', section } : { surface: 'settings' };
  }
  // Music moved into the Library: the app's first address sync replaces /music with /library/music.
  if (path === '/music') return { surface: 'library', view: 'music' };
  const plain = PLAIN_SURFACES.find((surface) => path === `/${surface}`);
  if (plain) return { surface: plain };
  const channel = /^\/subscriptions\/([A-Za-z0-9_-]{1,64})$/.exec(path);
  if (channel) return { surface: 'subscriptions', channelId: channel[1] };
  // Collections: the list, with ?new=collection|smart, and one collection by id. Unknown ids fall back to the list.
  const collections = /^\/library\/collections(?:\/([^/]+))?$/.exec(path);
  if (collections) {
    let id = '';
    try { id = decodeURIComponent(collections[1] ?? ''); } catch { /* malformed escape */ }
    if (id && LIBRARY_ID.test(id)) return { surface: 'library', collections: 'detail', collectionId: id };
    const create = params.get('new');
    return create === 'collection' || create === 'smart' ? { surface: 'library', collections: 'list', create } : { surface: 'library', collections: 'list' };
  }
  const lens = LENS_PATH.exec(path);
  if (lens) {
    const view = lens[1] as LibraryLens;
    const wall = search.replace(/^\?/, '');
    return wall && wall.length <= MAX_WALL ? { surface: 'library', view, wall } : { surface: 'library', view };
  }
  const editor = /^\/title\/([^/]+)\/edit$/.exec(path);
  if (editor) {
    let id = '';
    try { id = decodeURIComponent(editor[1]); } catch { /* malformed escape */ }
    if (LIBRARY_ID.test(id)) {
      const tab = EDITOR_TABS.find((value) => value === params.get('tab'));
      return tab ? { surface: 'library', titleId: id, edit: true, tab } : { surface: 'library', titleId: id, edit: true };
    }
  }
  const title = /^\/title\/([^/]+)$/.exec(path);
  if (title) {
    let id = '';
    try { id = decodeURIComponent(title[1]); } catch { /* malformed escape */ }
    if (LIBRARY_ID.test(id)) {
      const season = params.get('season') ?? '';
      return /^\d{1,3}$/.test(season) ? { surface: 'library', titleId: id, season: Number(season) } : { surface: 'library', titleId: id };
    }
  }
  const library = /^\/watch\/library\/([^/]+)$/.exec(path);
  if (library) {
    let id = '';
    try { id = decodeURIComponent(library[1]); } catch { /* malformed escape */ }
    if (LIBRARY_ID.test(id)) {
      const start = params.get('t') ?? '';
      return /^\d{1,5}$/.test(start) && Number(start) <= MAX_START_SECONDS ? { surface: 'watch', libraryId: id, startSeconds: Number(start) } : { surface: 'watch', libraryId: id };
    }
  }
  if (path === '/watch') {
    const url = safeRemoteUrl(params.get('url'));
    if (url) return { surface: 'watch', url };
  }
  return { surface: 'home' };
}

export function routePath(route: AppRoute): string {
  if (route.surface === 'home') return '/';
  if (route.surface === 'requests') {
    if (route.view === 'search') return `/requests/search?q=${encodeURIComponent(route.query)}`;
    if (route.view === 'title') return `/requests/title/${route.kind}/${route.id}`;
    if (route.view === 'movies' || route.view === 'shows' || route.view === 'anime') {
      const params = [route.section ? `section=${route.section}` : '', route.genre ? `genre=${encodeURIComponent(route.genre)}` : ''].filter(Boolean).join('&');
      return `/requests/${route.view}${params ? `?${params}` : ''}`;
    }
    return route.view === 'discover' ? '/requests' : `/requests/${route.view}`;
  }
  if (route.surface === 'streaming') {
    const params = [route.query ? `q=${encodeURIComponent(route.query)}` : '', route.rail ? `rail=${route.rail}` : '', route.provider && route.provider !== 'youtube' ? `provider=${route.provider}` : ''].filter(Boolean).join('&');
    return `/streaming${route.view === 'home' ? '' : `/${route.view}`}${params ? `?${params}` : ''}`;
  }
  if ('youtubeChannelId' in route) return `/channel/youtube/${route.youtubeChannelId}${route.tab && route.tab !== 'videos' ? `?tab=${route.tab}` : ''}`;
  if ('channelUrl' in route) return `/channel?url=${encodeURIComponent(route.channelUrl)}`;
  if (route.surface === 'settings') return 'memberId' in route ? `/settings/members/${encodeURIComponent(route.memberId)}` : route.section ? `/settings/${route.section}` : '/settings';
  if (route.surface === 'watch') {
    if ('libraryId' in route) return `/watch/library/${encodeURIComponent(route.libraryId)}${route.startSeconds !== undefined ? `?t=${route.startSeconds}` : ''}`;
    return `/watch?url=${encodeURIComponent(route.url)}`;
  }
  if ('channelId' in route) return `/subscriptions/${route.channelId}`;
  if (route.surface === 'library' && 'collections' in route) {
    if (route.collections === 'detail') return `/library/collections/${encodeURIComponent(route.collectionId)}`;
    return route.create ? `/library/collections?new=${route.create}` : '/library/collections';
  }
  if (route.surface === 'library' && 'edit' in route) return `/title/${encodeURIComponent(route.titleId)}/edit${route.tab ? `?tab=${route.tab}` : ''}`;
  if (route.surface === 'library' && 'titleId' in route) return `/title/${encodeURIComponent(route.titleId)}${route.season !== undefined ? `?season=${route.season}` : ''}`;
  if (route.surface === 'library' && 'view' in route) return `/library/${route.view}${route.wall ? `?${route.wall}` : ''}`;
  return `/${route.surface}`;
}

/** Identifies the page a route shows. A new key means a new page: the shell moves focus to its heading. Tabs, lenses, walls, rails, queries and Settings sections are in-page state and keep the key. */
export function routeFocusKey(route: AppRoute | null): string {
  if (!route) return '';
  if (route.surface === 'subscriptions') {
    if ('youtubeChannelId' in route) return `subscriptions:youtube:${route.youtubeChannelId}`;
    if ('channelUrl' in route) return `subscriptions:url:${route.channelUrl}`;
    if ('channelId' in route) return `subscriptions:${route.channelId}`;
  }
  if (route.surface === 'requests') return route.view === 'title' ? `requests:title:${route.kind}:${route.id}` : 'requests';
  if (route.surface === 'library' && 'edit' in route) return `library:edit:${route.titleId}`;
  if (route.surface === 'library' && 'titleId' in route) return `library:title:${route.titleId}`;
  if (route.surface === 'watch') return `watch:${'libraryId' in route ? route.libraryId : route.url}`;
  return route.surface;
}

/**
 * Back from a title, album or artist page: the place it was opened from with that lens's
 * last wall; for a deep link, the title's own lens by type and category; else the All landing.
 */
export function libraryBackRoute(type: TitleType | null, category: TitleCategory | null | undefined, lastLens: LibraryLens | 'all' | null, lastWalls: Partial<Record<LibraryPlace, string>>): AppRoute {
  const lens = lastLens ?? titleLens(type, category);
  if (!lens || lens === 'all') return { surface: 'library' };
  const wall = lastWalls[lens];
  return wall ? { surface: 'library', view: lens, wall } : { surface: 'library', view: lens };
}
