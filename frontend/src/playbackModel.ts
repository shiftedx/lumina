export const PLAYBACK_CHECKPOINT_INTERVAL_SECONDS = 15;

export type RemoteSourceIdentityInput = {
  id?: string | null;
  source?: string | null;
  webpage_url?: string | null;
};

/** Keep this normalization mirrored by the backend stream-cache identity helper. */
export function canonicalRemoteSourceUrl(value: string): string {
  try {
    const url = new URL(value);
    url.hash = '';
    url.hostname = url.hostname.toLowerCase();
    const host = url.hostname.toLowerCase().replace(/^www\./, '');
    const youtube = host === 'youtu.be' || host === 'youtube.com' || host.endsWith('.youtube.com');
    for (const key of [...url.searchParams.keys()]) {
      if (key.toLowerCase().startsWith('utm_') || (youtube && ['feature', 'si'].includes(key.toLowerCase()))) url.searchParams.delete(key);
    }
    url.searchParams.sort();
    return url.toString();
  } catch {
    return value.trim();
  }
}

function youtubeIdFromUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase().replace(/^www\./, '');
    if (host === 'youtu.be') return url.pathname.split('/').filter(Boolean)[0] || null;
    if (host === 'youtube.com' || host.endsWith('.youtube.com')) return url.searchParams.get('v');
  } catch {
    return null;
  }
  return null;
}

export function remoteSourceIdentity(item: RemoteSourceIdentityInput, raw: Record<string, unknown> = {}): string | null {
  const sourceUrl = typeof item.webpage_url === 'string' ? item.webpage_url.trim() : '';
  const rawId = typeof raw.id === 'string' && raw.id.trim() ? raw.id.trim() : null;
  const itemId = item.id?.trim() || null;
  const source = (typeof raw.extractor === 'string' ? raw.extractor : typeof raw.extractor_key === 'string' ? raw.extractor_key : item.source)?.trim().toLowerCase() || null;
  const youtubeId = youtubeIdFromUrl(sourceUrl) || ((source === 'youtube' || source === 'youtube:tab') ? (rawId || itemId) : null);
  if (youtubeId) return `youtube:${youtubeId}`;
  return sourceUrl ? `url:${canonicalRemoteSourceUrl(sourceUrl)}` : null;
}

export type CapturedChatIdentityInput = {
  extractor?: string | null;
  remote_id?: string | null;
  webpage_url?: string | null;
};

/**
 * Chat identity under which a local Library item's own captured timed chat
 * asset is filed. A live recording publishes its Library item under the same
 * provider-scoped identity as the chat it captured (`youtube:<id>` /
 * `twitch:<stream_id>`, see the backend's recording_library_info), so completed
 * playback of the recording keys the member's own captured chat. Twitch needs
 * the provider-scoped form explicitly: a live stream id is not its later VOD id
 * and never matches a `url:`-keyed identity (issue #110).
 */
export function capturedChatIdentity(item: CapturedChatIdentityInput): string | null {
  const extractor = item.extractor?.trim().toLowerCase() || null;
  const provider = extractor ? extractor.split(':')[0] : null;
  const remoteId = item.remote_id?.trim() || null;
  if (provider === 'twitch' && remoteId) return `twitch:${remoteId}`;
  return remoteSourceIdentity({ id: remoteId, source: extractor, webpage_url: item.webpage_url });
}

/**
 * Builds the visible autoplay queue from stable source identities. The current
 * video and alternate URL forms of the same source are removed together so the
 * item marked "Plays next" is always the item that actually advances.
 */
export function distinctRemoteSources<T extends RemoteSourceIdentityInput>(
  items: T[],
  currentIdentity: string | null,
  limit = Number.POSITIVE_INFINITY,
): T[] {
  const seen = new Set<string>();
  if (currentIdentity) seen.add(currentIdentity);
  const queue: T[] = [];
  for (const item of items) {
    const identity = remoteSourceIdentity(item)
      || (item.id ? `id:${item.source || 'unknown'}:${item.id}` : null);
    if (identity && seen.has(identity)) continue;
    if (identity) seen.add(identity);
    queue.push(item);
    if (queue.length >= limit) break;
  }
  return queue;
}

export function shouldSavePlaybackCheckpoint({
  lastSavedPosition,
  position,
  force = false,
}: {
  lastSavedPosition: number;
  position: number;
  force?: boolean;
}): boolean {
  if (force) return true;
  if (position <= 0) return false;
  return Math.abs(position - lastSavedPosition) >= PLAYBACK_CHECKPOINT_INTERVAL_SECONDS;
}

/** Where a Play starts: `position`, or the beginning when it is at or past the end (a stale position, the credits).
 * The player and the early start both use it, so the server hands the early session back to the player. */
export function startPoint(position: number, duration: number | null | undefined): number {
  return position > 0 && position < (duration ?? Number.POSITIVE_INFINITY) - 1 ? position : 0;
}
