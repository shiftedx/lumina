import { isUrl } from '../../luminaModel';
import { SETTINGS_SECTIONS, visibleSections } from '../settings/registry';
import type { LibraryItem, LocalSearchMatch, LocalSearchResponse, SearchHistoryEntry, SourceAutomation, TitleSummary, UserProfile, YouTubeSearchResult } from '../../types';
import type { PaletteAction } from './actions';

export type PaletteGroupId = 'link' | 'recent' | 'actions' | 'movies' | 'shows' | 'anime' | 'music' | 'moments' | 'videos' | 'channels' | 'youtube' | 'goto' | 'everything';
export type PaletteOption =
  | { id: string; kind: 'recent'; label: string; entryId: string }
  | { id: string; kind: 'action'; label: string; action: PaletteAction }
  | { id: string; kind: 'title'; label: string; meta: string; title: TitleSummary }
  | { id: string; kind: 'moment'; label: string; meta: string; match: LocalSearchMatch }
  | { id: string; kind: 'video'; label: string; meta: string; item: LibraryItem }
  | { id: string; kind: 'channel'; label: string; meta: string; channel: SourceAutomation }
  | { id: string; kind: 'youtube'; label: string; meta: string; result: YouTubeSearchResult }
  | { id: string; kind: 'goto'; label: string; meta: string; path: string }
  | { id: string; kind: 'link'; label: string; url: string }
  | { id: string; kind: 'everything'; label: string; query: string };
export interface PaletteGroup { id: PaletteGroupId; label: string; options: PaletteOption[] }
export interface PaletteInput {
  query: string;
  mode: 'search' | 'link';
  user: UserProfile;
  history: SearchHistoryEntry[];
  local: LocalSearchResponse | null;
  remote: YouTubeSearchResult[];
  channels: SourceAutomation[];
  /** Already filtered by `when` and the query (actions.ts); the empty query passes the pinned five. */
  actions: PaletteAction[];
}

export const GROUP_LABELS: Record<PaletteGroupId, string> = {
  link: 'Link', recent: 'Recent searches', actions: 'Actions', movies: 'Movies', shows: 'Shows', anime: 'Anime', music: 'Music',
  moments: 'Moments', videos: 'Your videos', channels: 'Channels', youtube: 'YouTube', goto: 'Go to', everything: '',
};
const CAPS = { recent: 6, actions: 5, title: 4, moments: 3, videos: 4, channels: 4, youtube: 5, goto: 5 } as const;
const MAX_QUERY = 200;
// `path` goes through `parseRoute`, so a query string is allowed: Library Channels is the YouTube lens's `?view=channels` wall (routes.ts, ChannelsView).
const SURFACES: Array<{ label: string; path: string; keywords: string[]; meta?: string }> = [
  { label: 'Home', path: '/', keywords: [] },
  { label: 'Library', path: '/library', keywords: ['movies', 'shows', 'anime', 'music'] },
  { label: 'Settings', path: '/settings', keywords: ['preferences'] },
];
// One row per destination: these pages are reached by their Actions row (actions.ts `path`), never also by Go to.
// Streaming, Live now, Your channels, saved channels and Downloads are therefore not in SURFACES; these sections are skipped below.
// paletteModel.test.ts ties this list to PALETTE_ACTIONS, so an action added with a path fails until it is listed here.
export const ACTION_DESTINATIONS = new Set(['/downloads', '/settings/playback', '/streaming', '/streaming/live', '/streaming/channels', '/library/youtube?view=channels', '/settings/discovery', '/settings/library']);

export const cleanQuery = (value: string): string => value.trim().slice(0, MAX_QUERY);

/** Every query word must start some word of the label or a keyword. */
export function matchesQuery(label: string, query: string, keywords: readonly string[] = []): boolean {
  const words = [...label.toLowerCase().split(/[^\p{L}\p{N}]+/u), ...keywords.flatMap((word) => word.toLowerCase().split(/[^\p{L}\p{N}]+/u))].filter(Boolean);
  return query.toLowerCase().split(/\s+/).filter(Boolean).every((part) => words.some((word) => word.startsWith(part)));
}

function titleGroup(title: TitleSummary): 'movies' | 'shows' | 'anime' | 'music' | null {
  if (title.type === 'album' || title.type === 'artist') return 'music';
  if (title.category === 'movies' || title.category === 'shows' || title.category === 'anime') return title.category;
  return null;
}

export function buildPalette(input: PaletteInput): PaletteGroup[] {
  const query = cleanQuery(input.query);
  const groups: PaletteGroup[] = [];
  const push = (id: PaletteGroupId, options: PaletteOption[]) => { if (options.length) groups.push({ id, label: GROUP_LABELS[id], options }); };

  if (input.mode === 'link') {
    if (query && isUrl(query)) push('link', [{ id: 'link', kind: 'link', label: 'Open link', url: query }]);
    return groups;
  }
  if (query && isUrl(query)) {
    push('link', [{ id: 'link', kind: 'link', label: 'Open link', url: query }]);
    groups.push({ id: 'everything', label: '', options: [{ id: 'everything', kind: 'everything', label: `Search everything for “${query}”`, query }] });
    return groups;
  }
  if (!query) push('recent', input.history.slice(0, CAPS.recent).map((entry) => ({ id: `recent:${entry.id}`, kind: 'recent', label: entry.query, entryId: entry.id })));
  push('actions', input.actions.slice(0, CAPS.actions).map((action) => ({ id: `action:${action.id}`, kind: 'action', label: action.label, action })));

  if (query.length >= 2 && input.local) {
    const byGroup = { movies: [] as PaletteOption[], shows: [] as PaletteOption[], anime: [] as PaletteOption[], music: [] as PaletteOption[] };
    const moments: PaletteOption[] = [];
    const videos: PaletteOption[] = [];
    for (const match of input.local.matches ?? []) {
      if (match.kind === 'title' && match.media_title) {
        const group = titleGroup(match.media_title);
        if (group) byGroup[group].push({ id: `title:${match.media_title.id}`, kind: 'title', label: match.title, meta: match.subtitle, title: match.media_title });
      } else if (match.kind === 'moment' && match.item) {
        moments.push({ id: `moment:${match.id}`, kind: 'moment', label: match.title, meta: match.subtitle, match });
      } else if (match.kind === 'library' && match.item) {
        videos.push({ id: `video:${match.item.id}`, kind: 'video', label: match.title, meta: match.subtitle, item: match.item });
      }
    }
    for (const id of ['movies', 'shows', 'anime', 'music'] as const) push(id, byGroup[id].slice(0, CAPS.title));
    push('moments', moments.slice(0, CAPS.moments));
    push('videos', videos.slice(0, CAPS.videos));
  }
  if (query.length >= 1) {
    push('channels', input.channels.filter((channel) => matchesQuery(channel.label, query)).slice(0, CAPS.channels)
      .map((channel) => ({ id: `channel:${channel.id}`, kind: 'channel', label: channel.label, meta: 'Channel you follow', channel })));
  }
  if (query.length >= 2) {
    push('youtube', input.remote.slice(0, CAPS.youtube).map((result) => ({ id: `youtube:${result.id ?? result.webpage_url}`, kind: 'youtube', label: result.title ?? '', meta: result.uploader || 'YouTube', result })));
  }
  if (query.length >= 1) {
    const sections = visibleSections(input.user, SETTINGS_SECTIONS).map((section) => ({ label: section.label, path: `/settings/${section.id}`, keywords: [section.summary ?? '', 'settings', ...(section.aliases ?? [])] }))
      .filter((place) => !ACTION_DESTINATIONS.has(place.path));
    push('goto', [...SURFACES, ...sections].filter((place) => matchesQuery(place.label, query, place.keywords)).slice(0, CAPS.goto)
      .map((place) => ({ id: `goto:${place.path}`, kind: 'goto', label: place.label, meta: (place.path.startsWith('/settings/') ? 'Settings' : 'Go to'), path: place.path })));
  }
  if (query) groups.push({ id: 'everything', label: '', options: [{ id: 'everything', kind: 'everything', label: `Search everything for “${query}”`, query }] });
  return groups;
}

export const flatOptions = (groups: PaletteGroup[]): PaletteOption[] => groups.flatMap((group) => group.options);

export type Move = 'next' | 'prev' | 'first' | 'last' | 'pageDown' | 'pageUp';
/** Arrows wrap across groups; page keys move 5 and clamp; Ctrl+Home/End go to the ends. */
export function moveActive(options: readonly { id: string }[], activeId: string | null, move: Move): string | null {
  if (!options.length) return null;
  const index = Math.max(0, options.findIndex((option) => option.id === activeId));
  const last = options.length - 1;
  const next = move === 'next' ? (index + 1) % options.length
    : move === 'prev' ? (index - 1 + options.length) % options.length
      : move === 'first' ? 0
        : move === 'last' ? last
          : move === 'pageDown' ? Math.min(last, index + 5) : Math.max(0, index - 5);
  return options[next].id;
}

/** The active row is kept by id when results re-render, so a late group never moves the highlight. */
export function keepActive(options: readonly { id: string }[], activeId: string | null): string | null {
  if (!options.length) return null;
  return options.some((option) => option.id === activeId) ? activeId : options[0].id;
}
