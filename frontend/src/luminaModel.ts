import type { DownloadJob, LibraryItem, LocalSearchMatch, PreviewResponse, SearchHistoryEntry, SearchSource, YouTubeSearchResult } from './types';

export const SOURCE_LABELS: Record<SearchSource, string> = { youtube: 'YouTube', soundcloud: 'SoundCloud', twitch: 'Twitch', kick: 'Kick' };

// Mirrors the server's per-member cap (backend/app/services/search_history.py MAX_ENTRIES).
export const MAX_SEARCH_HISTORY = 50;

/** Move (or add) `entry` to the front of the member's history, deduped case-insensitively, capped at MAX_SEARCH_HISTORY. */
export function withHistoryEntry(entry: SearchHistoryEntry, current: SearchHistoryEntry[]): SearchHistoryEntry[] {
  const key = entry.query.toLowerCase();
  return [entry, ...current.filter((existing) => existing.query.toLowerCase() !== key)].slice(0, MAX_SEARCH_HISTORY);
}

export function readString(record: Record<string, unknown> | null | undefined, key: string): string | null {
  const value = record?.[key];
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

export function readNumber(record: Record<string, unknown> | null | undefined, key: string): number | null {
  const value = record?.[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function libraryThumbnail(item: LibraryItem): string | null {
  return item.artwork_url || (item.id ? `/api/library/${encodeURIComponent(item.id)}/artwork` : null);
}

export function isAudioItem(item: LibraryItem): boolean {
  if (item.kind) return item.kind === 'audio' || item.kind === 'track';
  const metadata = item.metadata_json || {};
  const vcodec = readString(metadata, 'vcodec');
  const mediaKind = readString(metadata, 'media_kind');
  return mediaKind === 'audio' || vcodec === 'none' || /audio|music/i.test(item.extractor || '') && !readNumber(metadata, 'height');
}

export const EXTERNAL_LIBRARY = 'external_library';

const pad2 = (value: number) => String(value).padStart(2, '0');

/** Readable secondary line from grouping metadata; never invents what the importer did not find. */
export function libraryByline(item: LibraryItem): string | null {
  const metadata = item.metadata_json || {};
  const season = readNumber(metadata, 'season_number');
  const episode = readNumber(metadata, 'episode_number');
  const year = readNumber(metadata, 'release_year');
  const end = readNumber(metadata, 'episode_number_end');
  if (item.kind === 'episode') return [item.uploader, season != null && episode != null ? `S${pad2(season)}E${pad2(episode)}${end != null ? `–${pad2(end)}` : ''}` : null].filter(Boolean).join(' · ') || null;
  if (item.kind === 'movie') return ['Movie', year].filter(Boolean).join(' · ');
  if (item.kind === 'track') return [item.uploader, item.playlist_name].filter(Boolean).join(' · ') || null;
  return item.uploader || null;
}

/** One honest status label for a library card: availability first, then origin. */
export function libraryBadge(item: LibraryItem): string {
  if (item.media_state === 'offline') return 'Location offline';
  if (item.status === 'missing') return item.media_state === 'quarantined' ? 'Deleted · restorable' : 'File missing';
  if (item.extractor === EXTERNAL_LIBRARY) return readString(item.metadata_json, 'lumina_import_kind') === 'unclassified' ? 'Unclassified · Read-only' : 'External library · Read-only';
  return 'In your vault';
}

/** Mirrors the server gate (owner or admin, managed file); the server still decides. */
export function canDeleteLibraryFile(item: LibraryItem, user: { id: string; role: string } | null | undefined): boolean {
  if (!user || item.extractor === EXTERNAL_LIBRARY || item.status === 'missing') return false;
  if (item.media_state && item.media_state !== 'available') return false;
  return user.role === 'admin' || item.user_id === user.id;
}

export function youtubeVideoId(value: { id?: string | null; webpage_url?: string | null; remote_id?: string | null }): string | null {
  if (value.id && /^[\w-]{6,}$/.test(value.id)) return value.id;
  if (value.remote_id && /^[\w-]{6,}$/.test(value.remote_id)) return value.remote_id;
  const url = value.webpage_url;
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (parsed.hostname === 'youtu.be') return parsed.pathname.split('/').filter(Boolean)[0] || null;
    if (parsed.hostname.includes('youtube.com')) return parsed.searchParams.get('v');
  } catch {
    return null;
  }
  return null;
}

export function resultFromPreview(preview: PreviewResponse, fallback?: YouTubeSearchResult): YouTubeSearchResult {
  // Label from the inspected provider so a pasted Kick/Twitch link is never shown as YouTube.
  const provider = preview.capabilities?.provider;
  // Any other site is generic web media: never relabelled as a first-class provider.
  const generic = provider === 'generic' || provider === 'unknown';
  const source: SearchSource | undefined = provider && provider in SOURCE_LABELS ? provider as SearchSource : generic ? undefined : fallback?.source || 'youtube';
  return {
    id: readString(preview.raw, 'id') || fallback?.id || null,
    title: preview.title || fallback?.title || 'Untitled video',
    uploader: readString(preview.raw, 'uploader') || readString(preview.raw, 'channel') || fallback?.uploader || null,
    duration: readNumber(preview.raw, 'duration') || fallback?.duration || null,
    thumbnail: readString(preview.raw, 'thumbnail') || fallback?.thumbnail || null,
    artwork_url: preview.artwork_url || fallback?.artwork_url || null,
    webpage_url: preview.webpage_url || fallback?.webpage_url || null,
    view_count: readNumber(preview.raw, 'view_count') || fallback?.view_count || null,
    published_at: readString(preview.raw, 'upload_date') || fallback?.published_at || null,
    source,
    source_label: source ? SOURCE_LABELS[source] : 'Web',
    capabilities: preview.capabilities || null,
  };
}

export function isUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

export function jobProgress(job: DownloadJob): number {
  if (job.status === 'completed') return 100;
  if (typeof job.progress === 'number') return Math.max(0, Math.min(100, job.progress));
  if (job.downloaded_bytes && job.total_bytes) return Math.max(0, Math.min(100, (job.downloaded_bytes / job.total_bytes) * 100));
  return 0;
}

export function remoteItemIsSaved(item: YouTubeSearchResult, library: LibraryItem[]): boolean {
  const remoteId = item.id || youtubeVideoId(item);
  const sourceUrl = item.webpage_url;
  return library.some((saved) => {
    if (remoteId && (saved.remote_id === remoteId || readString(saved.metadata_json, 'id') === remoteId)) return true;
    const savedUrl = saved.webpage_url || readString(saved.metadata_json, 'webpage_url');
    return Boolean(sourceUrl && savedUrl && sourceUrl === savedUrl);
  });
}

export function displayInitials(value: string): string {
  return value.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join('') || 'L';
}
