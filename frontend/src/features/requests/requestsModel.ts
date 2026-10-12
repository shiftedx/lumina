/** Pure Requests logic: status copy, the request sheet's choices, quota copy, anime seasons and countdowns. */
import type { AnimeSeason, CatalogItem, CatalogStatus, Kind, Language, MediaRequest, NewRequest, Quota, RequestState, Season } from './requestsApi';

export const KIND_LABEL: Record<Kind, string> = { movie: 'Film', show: 'Series', anime: 'Anime' };

const STATE_LABEL: Record<RequestState, string> = {
  none: '', pending: 'Requested', approved: 'Approved', processing: 'Downloading', partially_available: 'Partly in your vault',
  available: 'In your vault', declined: 'Declined', failed: 'Needs attention',
};

/** The ribbon a card or hero shows; null when nothing was asked for. `percent` only while downloading. */
export function statusBadge(status: CatalogStatus): { label: string; state: RequestState; percent: number | null } | null {
  if (status.state === 'none') return null;
  const percent = status.state === 'processing' ? Math.round(Math.max(0, Math.min(1, status.progress ?? 0)) * 100) : null;
  return { label: percent === null ? STATE_LABEL[status.state] : downloadingLabel(percent), state: status.state, percent };
}

/** Progress 1.0 while still processing: the files are down and the vault is picking them up. */
const downloadingLabel = (percent: number) => (percent >= 100 ? 'Downloaded — adding to your vault' : `${STATE_LABEL.processing} ${percent}%`);

export const stateLabel = (state: RequestState) => STATE_LABEL[state];

/** A new request is possible from nothing, after a decline, or for more seasons of a partly-available series. */
export const canAskFor = (item: Pick<CatalogItem, 'status' | 'requestable'>) => item.requestable !== false && ['none', 'declined', 'partially_available'].includes(item.status.state);
export const isSeries = (item: Pick<CatalogItem, 'media_type'>) => item.media_type === 'tv';
/** Anime series ask English dub or Japanese + subtitles; anime films and everything else do not. */
export const needsLanguage = (item: Pick<CatalogItem, 'kind' | 'media_type'>) => item.kind === 'anime' && item.media_type === 'tv';

export type SeasonMode = 'all' | 'latest' | 'pick';
/** Regular seasons, specials (season 0) last. */
export const requestableSeasons = (seasons: readonly Season[]) => [...seasons].sort((a, b) => (a.number || 1e6) - (b.number || 1e6));

export function seasonsPayload(mode: SeasonMode, picked: readonly number[], seasons: readonly Season[]): number[] | 'all' | null {
  if (mode === 'all') return 'all';
  if (mode === 'latest') {
    const latest = Math.max(0, ...seasons.map((season) => season.number));
    return latest ? [latest] : 'all';
  }
  return picked.length ? [...new Set(picked)].sort((a, b) => a - b) : null;
}

export function buildRequest(item: CatalogItem, choice: { seasons?: number[] | 'all' | null; language?: Language | null }): NewRequest {
  const ids = { ...(item.tmdb_id ? { tmdb_id: item.tmdb_id } : {}), ...(item.tvdb_id ? { tvdb_id: item.tvdb_id } : {}), ...(item.anilist_id ? { anilist_id: item.anilist_id } : {}) };
  return {
    kind: item.kind, ...ids,
    // An anime film goes to Radarr: the engine needs to hear it is a movie.
    ...(item.kind === 'anime' && item.media_type === 'movie' ? { media_type: 'movie' as const } : {}),
    ...(isSeries(item) && choice.seasons ? { seasons: choice.seasons } : {}),
    ...(needsLanguage(item) && choice.language ? { language: choice.language } : {}),
  };
}

/** What the sheet's ribbon becomes the moment the member presses Request (the server's answer then replaces it). */
export const optimisticState = (quota: Quota | null): RequestState => (quota?.auto_approve ? 'approved' : 'pending');

function period(days: number | null): string {
  if (days === 1) return 'today';
  if (days === 7) return 'this week';
  if (days === 30 || days === 31) return 'this month';
  return days ? `in ${days} days` : '';
}

export function quotaLine(quota: Quota | null): string {
  if (!quota) return '';
  if (!quota.can_request) return `You can't request ${quota.kind === 'movie' ? 'films' : quota.kind === 'show' ? 'series' : 'anime'} yet. Ask an admin.`;
  if (quota.remaining === null || quota.limit === null) return 'No request limit';
  return `${quota.remaining} of ${quota.limit} left ${period(quota.days)}`.trim();
}

export const routingLine = (quota: Quota | null, kind: Kind) => (quota?.auto_approve ? `Will be sent straight to ${kind === 'movie' ? 'Radarr' : 'Sonarr'}` : "Needs an admin's approval");

const ERROR_COPY: Record<string, string> = {
  not_allowed: "You can't request this kind of title. An admin can change that in Settings.",
  quota_exceeded: "You've reached your request limit for now.",
  already_available: 'This is already in your vault.',
  requests_disabled: 'Requests are turned off for this household.',
  tmdb_not_configured: 'Requests need a TMDB key before they can work.',
  unmapped: "Lumina couldn't match this anime to TMDB yet, so it can't be requested.",
  mapping_pending: 'Lumina is still matching this anime. Try again in a moment.',
  tmdb_unavailable: "TMDB isn't answering right now. Try again in a moment.",
  not_found: "Lumina couldn't find this title.",
  not_pending: 'Someone already decided on this request.',
  not_failed: "This request isn't stuck any more.",
  language_required: 'This one is anime: choose English dub or Japanese + subtitles, then request again.',
  anilist_unavailable: "AniList isn't answering right now. Try again in a moment.",
  anilist_id_required: "Lumina couldn't identify this anime. Try again from its page.",
};
/** Why a title can't be requested: known reasons get their own line, anything newer a calm generic one. */
export const unrequestableCopy = (reason?: string) => (reason && ERROR_COPY[reason]) || "Can't be requested yet.";
export const errorCopy = (code: string, fallback = 'Something went wrong. Try again.') => ERROR_COPY[code] ?? fallback;

// The member's last dub/sub choice, per member on this device. Storage can be missing or blocked: never throw.
const languageKey = (userId: string) => `lumina:requests:language:${userId}`;
export function rememberedLanguage(userId: string): Language | null {
  try {
    const value = window.localStorage.getItem(languageKey(userId));
    return value === 'dub' || value === 'sub' ? value : null;
  } catch {
    return null;
  }
}
export function rememberLanguage(userId: string, language: Language): void {
  try { window.localStorage.setItem(languageKey(userId), language); } catch { /* private window: the choice is simply not remembered */ }
}

export type TimelineStep = { label: string; state: 'done' | 'current' | 'upcoming' | 'stopped' };
/** Requested → Approved → Downloading xx% → In your vault, or the step where it stopped (declined, failed). */
export function timeline(request: Pick<MediaRequest, 'status' | 'progress'>): TimelineStep[] {
  if (request.status === 'declined') return [{ label: 'Requested', state: 'done' }, { label: 'Declined', state: 'stopped' }];
  if (request.status === 'failed') return [{ label: 'Requested', state: 'done' }, { label: 'Approved', state: 'done' }, { label: "Couldn't send", state: 'stopped' }];
  const percent = Math.round(Math.max(0, Math.min(1, request.progress ?? 0)) * 100);
  const third = request.status === 'processing' ? downloadingLabel(percent) : request.status === 'partially_available' ? 'Partly in your vault' : 'Downloading';
  // The step being waited on: approval, then the download, then nothing (all done).
  const at = request.status === 'available' ? 4 : request.status === 'pending' || request.status === 'none' ? 1 : 2;
  return ['Requested', 'Approved', third, 'In your vault'].map((label, index) => ({ label, state: index < at ? 'done' : index === at ? 'current' : 'upcoming' }));
}
export const isActive = (state: RequestState) => state === 'pending' || state === 'approved' || state === 'processing' || state === 'partially_available';

const SEASONS: AnimeSeason[] = ['WINTER', 'SPRING', 'SUMMER', 'FALL'];
export type SeasonYear = { season: AnimeSeason; year: number };
export const seasonOf = (date: Date): SeasonYear => ({ season: SEASONS[Math.floor(date.getMonth() / 3)], year: date.getFullYear() });
export function shiftSeason({ season, year }: SeasonYear, delta: number): SeasonYear {
  const index = SEASONS.indexOf(season) + delta;
  return { season: SEASONS[((index % 4) + 4) % 4], year: year + Math.floor(index / 4) };
}
export const seasonLabel = ({ season, year }: SeasonYear) => `${season[0]}${season.slice(1).toLowerCase()} ${year}`;

/** "in 2d 4h", "in 3h 12m", "in 12m", "airing now", or "aired". */
export function countdown(iso: string, now: number): string {
  const ms = Date.parse(iso) - now;
  if (Number.isNaN(ms)) return '';
  if (ms <= -30 * 60_000) return 'aired';
  if (ms <= 60_000) return 'airing now';
  const minutes = Math.floor(ms / 60_000);
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  if (days) return `in ${days}d ${hours}h`;
  return hours ? `in ${hours}h ${minutes % 60}m` : `in ${minutes}m`;
}

export const metaLine = (item: CatalogItem) => [item.year, item.anime?.format && item.kind === 'anime' ? formatLabel(item.anime.format) : KIND_LABEL[item.kind], item.rating ? `★ ${item.rating.toFixed(1)}` : null, item.genres.slice(0, 3).join(', ') || null].filter(Boolean).join(' · ');
export const formatLabel = (format: string) => ({ TV: 'TV', TV_SHORT: 'TV short', MOVIE: 'Film', OVA: 'OVA', ONA: 'ONA', SPECIAL: 'Special', MUSIC: 'Music' } as Record<string, string>)[format] ?? format;
/** "seasons 2–4", "seasons 2, 5", "season 3". */
export function seasonsWords(seasons: readonly number[]): string {
  const sorted = [...seasons].sort((a, b) => a - b);
  if (sorted.length === 1) return `season ${sorted[0]}`;
  const contiguous = sorted.every((value, index) => index === 0 || value === sorted[index - 1] + 1);
  return `seasons ${contiguous ? `${sorted[0]}–${sorted.at(-1)}` : sorted.join(', ')}`;
}

/** The id the catalog's title endpoint takes: AniList for anime, TMDB otherwise. */
export const catalogId = (item: Pick<CatalogItem, 'kind' | 'anilist_id' | 'tmdb_id'>) => (item.kind === 'anime' ? item.anilist_id : item.tmdb_id);
