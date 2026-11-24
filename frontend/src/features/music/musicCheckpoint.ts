import type { MusicQueueEntry } from './musicSharedSession';
import type { MusicTrack } from './musicTypes';

type StoredQueue = {
  queue: MusicTrack[];
  index: number;
  position: number;
  revision?: number | null;
  repeat?: unknown;
  shuffle?: unknown;
  dirty?: unknown;
  entries?: MusicQueueEntry[];
  entryIndex?: number | null;
  unavailablePolicy?: 'skip' | 'wait';
  localOutput?: boolean;
};

const key = (owner: string) => `lumina:music:queue:${owner}`;

function validTrack(value: unknown): value is MusicTrack {
  if (!value || typeof value !== 'object') return false;
  const track = value as Partial<MusicTrack>;
  return typeof track.id === 'string' && /^[a-zA-Z0-9_-]{1,64}$/.test(track.id)
    && track.audio_url === `/api/music/tracks/${track.id}/audio`
    && typeof track.title === 'string' && typeof track.artist === 'string'
    && typeof track.album_artist === 'string' && typeof track.album === 'string' && Array.isArray(track.genres);
}

function validEntry(entry: MusicQueueEntry): boolean {
  return !!entry && (entry.kind === 'track' ? typeof entry.item_id === 'string' : entry.kind === 'acquisition' && typeof entry.request_id === 'string')
    && (!entry.tracks || Array.isArray(entry.tracks) && entry.tracks.every(validTrack));
}

export function readMusicCheckpoint(owner: string): StoredQueue | null {
  try {
    const saved = JSON.parse(localStorage.getItem(key(owner)) || 'null') as Partial<StoredQueue> | null;
    if (!Array.isArray(saved?.queue) || !saved.queue.length || saved.queue.length > 5000 || !saved.queue.every(validTrack)) return null;
    const entries = Array.isArray(saved.entries) && saved.entries.length <= 5000 && saved.entries.every(validEntry) ? saved.entries : undefined;
    return {
      ...saved,
      queue: saved.queue,
      index: Math.max(0, Math.min(Number.isInteger(saved.index) ? Number(saved.index) : 0, saved.queue.length - 1)),
      position: typeof saved.position === 'number' && Number.isFinite(saved.position) && saved.position >= 0 ? saved.position : 0,
      entries,
      entryIndex: entries && Number.isInteger(saved.entryIndex) && entries[Number(saved.entryIndex)] ? saved.entryIndex : null,
    };
  } catch { return null; }
}

export function storeMusicCheckpoint(owner: string, value: StoredQueue): void {
  try { localStorage.setItem(key(owner), JSON.stringify(value)); } catch { /* storage may be full or unavailable */ }
}

export function saveOfflineResumePosition(owner: string, snapshot: NonNullable<ReturnType<typeof readMusicCheckpoint>>, position: number): void {
  const latest = readMusicCheckpoint(owner);
  if (!latest || latest.revision !== snapshot.revision || latest.index !== snapshot.index
      || latest.entryIndex !== snapshot.entryIndex || latest.localOutput === false
      || latest.queue.length !== snapshot.queue.length
      || latest.queue.some((track, index) => track.id !== snapshot.queue[index].id)
      || !Number.isFinite(position) || position < 0 || latest.position === position) return;
  storeMusicCheckpoint(owner, { ...latest, position, dirty: true });
}
