/** Explore's model: result groups, the live teaser, popular rails, channel targets. */
import type { LiveSnapshot, PopularSnapshot, RemoteEntry } from '../../types';
import { channelPagePath, youtubeChannelId } from '../channels/channelMention';

export type GroupKey = 'channels' | 'live' | 'videos' | 'shorts' | 'playlists';
export const GROUP_ORDER: ReadonlyArray<readonly [GroupKey, string, string]> = [
  ['channels', 'Channels', 'Channels'], ['live', 'Live now', 'Live'], ['videos', 'Videos', 'Videos'], ['shorts', 'Shorts', 'Shorts'], ['playlists', 'Playlists', 'Playlists'],
];
export const LIVE_TEASER = 16;
export const LIBRARY_GROUP_MAX = 12;

export function exploreGroups(results: readonly RemoteEntry[]): Record<GroupKey, RemoteEntry[]> {
  const groups: Record<GroupKey, RemoteEntry[]> = { channels: [], live: [], videos: [], shorts: [], playlists: [] };
  for (const item of results) {
    if (item.kind === 'channel') groups.channels.push(item);
    else if (item.kind === 'live' || item.capabilities?.lifecycle === 'live') groups.live.push(item);
    else if (item.kind === 'short') groups.shorts.push(item);
    else if (item.kind === 'playlist') groups.playlists.push(item);
    else groups.videos.push(item);
  }
  return groups;
}

/** Followed live entries first, then the backend-ranked live entries, each once. */
export function liveTeaser(snapshot: LiveSnapshot | null, visible: (item: RemoteEntry) => boolean = () => true): RemoteEntry[] {
  const seen = new Set<string | null | undefined>();
  const teaser: RemoteEntry[] = [];
  for (const item of [...(snapshot?.hero ?? []), ...(snapshot?.items ?? [])]) {
    if (item.capabilities?.lifecycle !== 'live' || !visible(item)) continue;
    const key = item.webpage_url || item.id;
    if (seen.has(key)) continue;
    seen.add(key);
    teaser.push(item);
    if (teaser.length === LIVE_TEASER) break;
  }
  return teaser;
}

/** One rail per ready (or stale, with its notice) popular category, in snapshot order. */
export function popularRails(popular: PopularSnapshot | null): Array<{ key: string; label: string; entries: RemoteEntry[] }> {
  return (popular?.categories ?? [])
    .filter((category) => category.state === 'ready' || category.state === 'stale')
    .map((category) => ({ key: `popular-${category.key}`, label: category.label, entries: (popular?.items ?? []).filter((item) => item.category_keys?.includes(category.key)) }))
    .filter((rail) => rail.entries.length);
}

export function popularNotice(popular: PopularSnapshot | null): string | null {
  if (!popular?.items.length) return null;
  if (popular.state === 'partial') return 'Showing a partial feed while more categories warm up.';
  return popular.stale ? 'Showing the last-known feed while discovery refreshes.' : null;
}

export function exploreKicker(popular: PopularSnapshot | null): string {
  const at = popular?.refreshed_at ? new Date(popular.refreshed_at) : null;
  const time = at && !Number.isNaN(at.getTime()) ? at.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : null;
  return time ? `Popular across the web · updated ${time}` : 'Popular across the web';
}

/** Where a channel result goes: its page by id, a handle through the resolver, else null (the watch flow). */
export function channelTarget(item: RemoteEntry): { href: string; id: string | null } | null {
  const id = youtubeChannelId({ channel_id: item.id, uploader_id: item.uploader_id, uploader_url: item.uploader_url, channel_url: item.webpage_url });
  if (id) return { href: channelPagePath(id), id };
  const url = item.webpage_url || item.uploader_url;
  if (item.source === 'youtube' && url && /^https:\/\/(?:www\.|m\.)?youtube\.com\/(?:@|c\/|user\/)/.test(url)) return { href: `/channel?url=${encodeURIComponent(url)}`, id: null };
  return null;
}
