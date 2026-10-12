/** The YouTube channel page's pure rules: tabs, kicker, sort, failures, paging. */
import { ApiRequestError } from '../../api';
import type { ChannelPageTab } from '../../app/routes';
import type { ChannelPageResponse, ChannelTab, LibraryChannelResponse, RemoteEntry } from '../../types';
import { formatCompactNumber } from '../../utils';

export const API_TAB: Readonly<Record<Exclude<ChannelPageTab, 'library'>, ChannelTab>> = { videos: 'videos', live: 'streams', shorts: 'shorts', playlists: 'playlists' };
export const UI_TAB: Readonly<Record<ChannelTab, Exclude<ChannelPageTab, 'library'>>> = { videos: 'videos', streams: 'live', shorts: 'shorts', playlists: 'playlists' };
export const TAB_LABELS: Readonly<Record<Exclude<ChannelPageTab, 'library'>, string>> = { videos: 'Videos', live: 'Live', shorts: 'Shorts', playlists: 'Playlists' };
export const EMPTY_TAB: Readonly<Record<Exclude<ChannelPageTab, 'library'>, string>> = {
  videos: 'No videos on this channel.', live: 'No live streams on this channel.', shorts: 'No shorts on this channel.', playlists: 'No playlists on this channel.',
};
export type ChannelSort = 'latest' | 'popular';
export const PAGE_FIRST = 60;
export const PAGE_MAX = 120;

/** Tabs the header lists, in the spec's order, plus "In your library (n)" when the member saved from this channel. */
export function pageTabs(data: ChannelPageResponse | null, libraryCount: number): Array<[ChannelPageTab, string]> {
  const listed = data ? (['videos', 'streams', 'shorts', 'playlists'] as const).filter((tab) => data.channel.tabs.includes(tab)).map((tab) => UI_TAB[tab]) : (['videos'] as const);
  const tabs: Array<[ChannelPageTab, string]> = listed.map((tab) => [tab, TAB_LABELS[tab]]);
  if (!tabs.length) tabs.push(['videos', TAB_LABELS.videos]);
  if (libraryCount > 0) tabs.push(['library', `In your library (${libraryCount})`]);
  return tabs;
}

/** `YOUTUBE · @handle · 1.2M subscribers · 845 videos`, unknown parts omitted; stale adds "Updated 42 min ago". */
export function channelKicker(data: ChannelPageResponse, now: Date = new Date()): string {
  const { channel } = data;
  const minutes = Math.max(1, Math.round((now.getTime() - new Date(data.fetched_at).getTime()) / 60_000));
  const age = minutes < 60 ? `${minutes} min` : minutes < 1440 ? `${Math.round(minutes / 60)} h` : `${Math.round(minutes / 1440)} d`;
  return [
    'YOUTUBE', channel.handle || null,
    channel.follower_count ? `${formatCompactNumber(channel.follower_count)} subscribers` : null,
    channel.video_count ? `${channel.video_count.toLocaleString()} videos` : null,
    data.stale ? `Updated ${age} ago` : null,
  ].filter(Boolean).join(' · ');
}

export function sortEntries(entries: readonly RemoteEntry[], sort: ChannelSort): RemoteEntry[] {
  return sort === 'popular' ? [...entries].sort((a, b) => (b.view_count ?? -1) - (a.view_count ?? -1)) : [...entries];
}

export function channelFailure(error: unknown): 'unavailable' | 'unreachable' {
  return error instanceof ApiRequestError && error.status === 404 ? 'unavailable' : 'unreachable';
}

/** The member's saved videos from this channel: by its UC id first, else by the uploader name the header shows. */
export function libraryChannelFor(channels: readonly LibraryChannelResponse[], id: string, name: string | null): LibraryChannelResponse | null {
  return channels.find((channel) => channel.channel_id === id) ?? (name ? channels.find((channel) => channel.uploader === name) : undefined) ?? null;
}

export const pageLimitNote = (limit: number, hasMore: boolean): string | null => (limit >= PAGE_MAX && hasMore ? 'Showing the latest 120 · Open on YouTube for more' : null);
