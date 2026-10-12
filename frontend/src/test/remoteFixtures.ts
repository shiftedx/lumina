/** Typed remote-media fixtures shared by the live and YouTube Vitest suites. Append only. */
import type { ChannelPageResponse, LibraryChannelResponse, LiveSnapshot, MediaLifecycle, MediaSourceCapabilities, RemoteEntry, SearchSource } from '../types';

/** Local time; suites call vi.setSystemTime(FIXED_NOW). A Tuesday. */
export const FIXED_NOW = new Date('2026-09-29T20:42:00');
export const CHANNEL_ID = 'UCabcdefghijklmnopqrstuv';
const DAY = 86_400_000;

export function capabilities(provider: SearchSource, lifecycle: MediaLifecycle, patch: Partial<MediaSourceCapabilities> = {}): MediaSourceCapabilities {
  return {
    provider, lifecycle, can_play: lifecycle !== 'upcoming', can_acquire: lifecycle === 'vod',
    chat: { live: lifecycle === 'live' && provider === 'youtube' ? 'available' : 'unavailable', replay: 'unavailable' }, ...patch,
  };
}

export function remoteEntry(id = 'v1', patch: Partial<RemoteEntry> = {}): RemoteEntry {
  return {
    id, title: 'Harbor walk at dawn', uploader: 'Chan', uploader_id: CHANNEL_ID, uploader_url: `https://www.youtube.com/channel/${CHANNEL_ID}`,
    duration: 761, view_count: 1_200_000, published_at: new Date(FIXED_NOW.getTime() - 3 * DAY).toISOString(),
    webpage_url: `https://www.youtube.com/watch?v=${id}`, artwork_url: `/api/artwork/remote/${id}`, source: 'youtube', source_label: 'YouTube',
    kind: 'video', capabilities: capabilities('youtube', 'vod'), saved_item_id: null, progress: null, ...patch,
  };
}

export function liveEntry(id: string, patch: Partial<RemoteEntry> = {}): RemoteEntry {
  const provider = patch.source ?? 'youtube';
  return remoteEntry(id, {
    title: 'Night market walk', kind: 'live', duration: null, published_at: null, view_count: 12_400, category_keys: ['gaming'],
    capabilities: capabilities(provider, 'live', { can_record: true }), ...patch,
  });
}

export function upcomingEntry(id: string, startsAt: string | null, patch: Partial<RemoteEntry> = {}): RemoteEntry {
  return remoteEntry(id, {
    title: 'Premiere tonight', kind: 'video', duration: null, view_count: null,
    capabilities: capabilities(patch.source ?? 'youtube', 'upcoming', { can_schedule: true, scheduled_start: startsAt }), ...patch,
  });
}

export function endedEntry(id: string, patch: Partial<RemoteEntry> = {}): RemoteEntry {
  return remoteEntry(id, { title: 'Last night\'s stream', kind: 'video', view_count: 800, capabilities: capabilities(patch.source ?? 'youtube', 'post_live'), ...patch });
}

export function liveSnapshot(patch: Partial<LiveSnapshot> = {}): LiveSnapshot {
  const at = FIXED_NOW.toISOString();
  return {
    items: [
      liveEntry('g1', { view_count: 30_000 }), liveEntry('g2', { view_count: 900 }),
      liveEntry('g3', { source: 'twitch', source_label: 'Twitch', webpage_url: 'https://www.twitch.tv/g3', uploader_id: null, uploader_url: null, view_count: 5_400 }),
      liveEntry('m1', { category_keys: ['music'], view_count: 2_000 }), liveEntry('m2', { category_keys: ['music'], view_count: 150 }),
    ],
    categories: [{ key: 'gaming', label: 'Gaming', state: 'ready' }, { key: 'music', label: 'Music', state: 'ready' }],
    hero: [], state: 'ready', refreshing: false, stale: false, twitch_available: true, followed_unavailable: [],
    refreshed_at: at, last_success_at: at, ...patch,
  };
}

export function channelPage(patch: Partial<ChannelPageResponse> = {}): ChannelPageResponse {
  return {
    channel: {
      id: CHANNEL_ID, name: 'Harbor Films', handle: '@harborfilms', url: `https://www.youtube.com/channel/${CHANNEL_ID}`,
      avatar_url: '/api/artwork/remote/avatar', banner_url: '/api/artwork/remote/banner', follower_count: 1_200_000, video_count: 845,
      description: 'Films about harbors and the people who keep them.\nShop: https://example.test/shop', verified: true,
      tabs: ['videos', 'streams', 'shorts', 'playlists'], follow_id: null, live: null,
    },
    tab: 'videos',
    entries: Array.from({ length: 60 }, (_, index) => remoteEntry(`c${String(index).padStart(3, '0')}`, { title: `Harbor film ${index + 1}`, uploader: 'Harbor Films', view_count: 1_000 * (index + 1) })),
    has_more: true, restricted: false, fetched_at: FIXED_NOW.toISOString(), stale: false, ...patch,
  };
}

export function libraryChannel(key = 'ch-1', patch: Partial<LibraryChannelResponse> = {}): LibraryChannelResponse {
  return {
    key, extractor: 'youtube', name: 'Harbor Films', uploader: 'Harbor Films', count: 24, unwatched_count: 3, newest_item_id: 'video-1',
    newest_at: new Date(FIXED_NOW.getTime() - 3 * DAY).toISOString(), channel_id: CHANNEL_ID, avatar_url: null, ...patch,
  };
}
