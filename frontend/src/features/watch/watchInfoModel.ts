/** The web-video watch column's rules: which media gets it, its kicker, meta and byline link. */
import { EXTERNAL_LIBRARY, SOURCE_LABELS, readString } from '../../luminaModel';
import type { LibraryItem, MediaLifecycle, SearchSource, YouTubeSearchResult } from '../../types';
import { formatCompactNumber, formatDuration } from '../../utils';
import { youtubeChannelId } from '../channels/channelMention';
import type { LiveBadgeState } from '../gallery/LiveBadge';
import { formatPublished } from '../media/MediaCards';
import type { WatchSelection } from './WatchSurface';

/** Remote sources and saved items with no Media title (YouTube, Twitch VODs, SoundCloud, recordings). */
export const isWebVideo = (selection: WatchSelection): boolean => selection.kind === 'remote' || !selection.item.title_id;

export function watchProvider(raw: Record<string, unknown>, remote: YouTubeSearchResult | null, local: LibraryItem | null): string {
  const candidates = [remote?.source, remote?.capabilities?.provider, readString(raw, 'extractor_key'), readString(raw, 'extractor'), local?.extractor];
  for (const candidate of candidates) {
    const key = candidate?.toLowerCase().split(':')[0];
    if (key && key !== EXTERNAL_LIBRARY && Object.prototype.hasOwnProperty.call(SOURCE_LABELS, key)) return SOURCE_LABELS[key as SearchSource];
  }
  return 'Web';
}

export function watchBadge(lifecycle: MediaLifecycle | null | undefined, ended: boolean): LiveBadgeState | null {
  if (ended && (lifecycle === 'live' || lifecycle === 'post_live' || lifecycle === 'completed_live')) return 'ended';
  if (lifecycle === 'live') return 'live';
  if (lifecycle === 'upcoming') return 'upcoming';
  return lifecycle === 'post_live' || lifecycle === 'completed_live' ? 'ended' : null;
}

export const spanLabel = (seconds: number): string => {
  const minutes = Math.floor(Math.max(0, seconds) / 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes}m`;
};
const clock = (at: Date) => at.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

export type WatchMetaInput = {
  state: LiveBadgeState | null;
  published?: string | null;
  views?: number | null;
  duration?: number | null;
  viewers?: number | null;
  /** When the viewer count was last seen in the live snapshot, if it has since left it. */
  viewersAsOf?: number | null;
  /** Unix seconds the stream started (release_timestamp). */
  startedAt?: number | null;
  startsAt?: string | null;
};

/** The meta line: VOD date, views and length; live viewers and start; the upcoming start; the ended span. */
export function watchMeta(input: WatchMetaInput, now: Date = new Date()): string {
  if (input.state === 'live') {
    const viewers = input.viewers != null ? `${input.viewers.toLocaleString()} watching${input.viewersAsOf ? ` (as of ${clock(new Date(input.viewersAsOf))})` : ''}` : null;
    const started = input.startedAt && !input.viewersAsOf ? `Started ${spanLabel(now.getTime() / 1000 - input.startedAt)} ago` : null;
    return [viewers, started].filter(Boolean).join(' · ');
  }
  if (input.state === 'upcoming') {
    const at = input.startsAt ? new Date(input.startsAt) : null;
    return at && !Number.isNaN(at.getTime()) ? `Starts ${at.toLocaleDateString(undefined, { weekday: 'short' })} ${clock(at)}` : 'Starts soon';
  }
  if (input.state === 'ended') {
    const span = input.duration ?? (input.startedAt ? now.getTime() / 1000 - input.startedAt : null);
    return span ? `Ended · streamed ${spanLabel(span)}` : 'Ended';
  }
  const published = formatPublished(input.published ?? null);
  const date = published && /^\d{8}$/.test(input.published ?? '') ? new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(Number(input.published!.slice(0, 4)), Number(input.published!.slice(4, 6)) - 1, Number(input.published!.slice(6, 8)))) : published;
  return [date, input.views ? `${formatCompactNumber(input.views)} views` : null, input.duration ? formatDuration(input.duration) : null].filter(Boolean).join(' · ');
}

/** The byline's channel page id: a YouTube channel id from the preview or the entry; null elsewhere. */
export function bylineChannelId(raw: Record<string, unknown>, remote: YouTubeSearchResult | null, provider: string): string | null {
  if (provider !== 'YouTube') return null;
  return youtubeChannelId({ channel_id: readString(raw, 'channel_id'), uploader_id: readString(raw, 'uploader_id') ?? remote?.uploader_id, channel_url: readString(raw, 'channel_url'), uploader_url: readString(raw, 'uploader_url') ?? remote?.uploader_url });
}
