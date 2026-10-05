/**
 * Pure helpers for remote (web) media cards. Ships the art, colour and provider
 * helpers; C2 adds the marker and label functions below them.
 */
import { SOURCE_LABELS } from '../../luminaModel';
import type { MediaLifecycle, PreviewEntry, RemoteEntry, SearchSource, TitleArt } from '../../types';
import { formatCompactNumber, formatDuration } from '../../utils';
import { fallbackColour, type PosterMarker } from './galleryModel';
import { liveBadgeState } from './LiveBadge';

/** Artwork in URL mode: only Lumina's own proxy URLs, so the browser never contacts a provider. */
export const remoteArt = (url: string | null | undefined): TitleArt | null => (url && url.startsWith('/api/') ? { url, widths: [] } : null);

/** The slot and typographic-card colour: the palette colour of the entry's address. */
export const remoteColour = (item: Pick<RemoteEntry, 'webpage_url' | 'id'>): { colour: string; fromPalette: true } => ({
  colour: fallbackColour(item.webpage_url || item.id || ''),
  fromPalette: true,
});

/** The provider as a search source: the entry's own, else its capabilities', else YouTube. */
export function remoteProvider(item: RemoteEntry | PreviewEntry): SearchSource {
  const provider = ('source' in item ? item.source : undefined) || item.capabilities?.provider;
  return provider && Object.prototype.hasOwnProperty.call(SOURCE_LABELS, provider) ? (provider as SearchSource) : 'youtube';
}

export const isRemoteEnded = (lifecycle: MediaLifecycle | null | undefined): boolean => lifecycle === 'post_live' || lifecycle === 'completed_live';

/** `sizes` for a rail or wall still: the spec 1.3 card widths. */
export const REMOTE_CARD_SIZES = '(max-width: 599px) min(72vw, 300px), (max-width: 1023px) 280px, 320px';

const lengthOf = (item: RemoteEntry): number | null => item.progress?.duration_seconds ?? item.duration ?? null;

/** Whole minutes left in a started, unfinished entry; null otherwise. */
export function remainingMinutes(item: RemoteEntry): number | null {
  const progress = item.progress;
  const length = lengthOf(item);
  if (!progress || progress.completed || progress.position_seconds <= 0 || !length || length <= progress.position_seconds) return null;
  return Math.ceil((length - progress.position_seconds) / 60);
}

/** A triangle only for the member's own unwatched copy; a bar for any progress; nothing on live, upcoming or ended. */
export function remoteMarker(item: RemoteEntry, ended = false): PosterMarker {
  if (liveBadgeState(item.capabilities?.lifecycle, ended)) return { kind: 'none' };
  const progress = item.progress;
  const length = lengthOf(item);
  if (progress && !progress.completed && progress.position_seconds > 0 && length) {
    return { kind: 'progress', percent: Math.min(100, Math.max(4, (progress.position_seconds / length) * 100)) };
  }
  if (item.saved_item_id && !progress) return { kind: 'unwatched' };
  return { kind: 'none' };
}

/** The label on the art's bottom scrim: viewers when live, SHORT, else the length. */
export function remoteScrim(item: RemoteEntry, ended = false): string | null {
  const state = liveBadgeState(item.capabilities?.lifecycle, ended);
  if (state === 'live') return item.view_count ? `${formatCompactNumber(item.view_count)} watching` : null;
  if (state === 'upcoming' || ended) return null;
  if (item.kind === 'short') return 'SHORT';
  if (item.kind === 'playlist') return 'PLAYLIST';
  return item.duration ? formatDuration(item.duration) : null;
}

/** "3 days ago" for a server timestamp; null when unknown. */
export function relativeAge(iso: string | null | undefined, now: Date = new Date()): string | null {
  const at = iso ? new Date(iso).getTime() : Number.NaN;
  if (Number.isNaN(at)) return null;
  const seconds = Math.round((at - now.getTime()) / 1000);
  const format = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  for (const [unit, size] of [['year', 31_536_000], ['month', 2_592_000], ['week', 604_800], ['day', 86_400], ['hour', 3_600], ['minute', 60]] as const) {
    if (Math.abs(seconds) >= size) return format.format(Math.round(seconds / size), unit);
  }
  return format.format(0, 'minute');
}

/** The caption's label line: `Channel · 3 days ago · 1.2M views`, then the member's state. */
export function remoteLine(item: RemoteEntry, now: Date = new Date()): string {
  const lifecycle = item.capabilities?.lifecycle;
  if (lifecycle === 'live' || lifecycle === 'upcoming') return item.uploader || '';
  const left = remainingMinutes(item);
  const state = item.progress?.completed ? 'Watched' : item.saved_item_id && !item.progress ? 'In your library' : null;
  return [
    item.uploader || null,
    left !== null ? `${left} min left` : relativeAge(item.published_at, now),
    item.view_count ? `${formatCompactNumber(item.view_count)} views` : null,
    state,
  ].filter(Boolean).join(' · ');
}

/** The card's accessible name: "Title, Channel, live, 12,400 watching" and the other forms. */
export function remoteLabel(item: RemoteEntry, ended = false): string {
  const parts: Array<string | null> = [item.title || 'Untitled video', item.uploader || null];
  const state = liveBadgeState(item.capabilities?.lifecycle, ended);
  if (state === 'live') parts.push('live', item.view_count ? `${item.view_count.toLocaleString()} watching` : null);
  else if (state === 'upcoming') {
    const start = item.capabilities?.scheduled_start ? new Date(item.capabilities.scheduled_start) : null;
    parts.push(start && !Number.isNaN(start.getTime()) ? `upcoming at ${start.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}` : 'upcoming');
  } else if (state === 'ended') parts.push('ended');
  else {
    const left = remainingMinutes(item);
    if (item.progress?.completed) parts.push('watched');
    else if (left !== null) parts.push('in progress', `${left} minute${left === 1 ? '' : 's'} left`);
    else if (item.saved_item_id && !item.progress) parts.push('in your library', 'unwatched');
  }
  return parts.filter(Boolean).join(', ');
}
