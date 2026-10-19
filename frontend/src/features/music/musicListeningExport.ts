import { ApiRequestError, requestJson } from '../../api';
import type { MusicEngineState } from './musicEngine';
import type { MusicTrack } from './musicTypes';

export type ListeningEvent = { id: string; owner_id: string; item_id: string; started_at: number; listened_seconds: number; account_generations: Record<string, string> };
type Sample = { track: string; index: number; position: number; playing: boolean; at: number; duration: number };

/** Counts clocked playback, never position jumps. Repeat starts a new logical play; pause/rewind keep the current one. */
export class ListeningTracker {
  current: ListeningEvent | null = null;
  private previous: Sample | null = null;
  constructor(private readonly owner: string, private readonly uuid: () => string = () => crypto.randomUUID(), private readonly accounts: Record<string, string> = {}) {}
  snapshot(): { event: ListeningEvent | null; sample: Sample | null } { return { event: this.current, sample: this.previous }; }
  restore(event: ListeningEvent, sample: Sample): void { this.current = event; this.previous = { ...sample, playing: false }; }
  observe(track: MusicTrack | undefined, playback: MusicEngineState, now: number): ListeningEvent[] {
    const completed: ListeningEvent[] = [];
    const previous = this.previous;
    const repeated = previous && previous.track === track?.id && previous.duration > 30 && previous.position >= previous.duration - 1 && playback.position < 1;
    if (this.current && (!track || this.current.item_id !== track.id || previous?.index !== playback.index || repeated)) {
      completed.push({ ...this.current }); this.current = null;
    }
    if (track && playback.playing && !this.current) {
      this.current = { id: this.uuid(), owner_id: this.owner, item_id: track.id, started_at: Math.floor(now / 1000), listened_seconds: 0, account_generations: { ...this.accounts } };
    }
    if (this.current && previous && previous.track === track?.id && previous.index === playback.index && previous.playing && !repeated) {
      const elapsed = (now - previous.at) / 1000;
      const played = playback.position - previous.position;
      if (elapsed >= 0 && played > 0 && played <= elapsed + 0.5) this.current.listened_seconds += Math.min(played, elapsed);
    }
    this.previous = track ? { track: track.id, index: playback.index, position: playback.position, playing: playback.playing, at: now, duration: playback.duration } : null;
    return completed;
  }
}

let owner: string | null = null;
let tracker: ListeningTracker | null = null;
let enabled = false;
let generation = 0;
let pending: ListeningEvent[] = [];
let flushing = false;
let checkpointAt = 0;
let bindings = '';
let refreshSequence = 0;
const key = (id: string) => `lumina:music:listening-events:${id}`;

function persist(): void {
  if (!owner) return;
  try { localStorage.setItem(key(owner), JSON.stringify({ pending, current: tracker?.snapshot() ?? null })); } catch { /* Full/private storage cannot interrupt playback. */ }
}
function enqueue(event: ListeningEvent): void {
  if (event.listened_seconds <= 0) return;
  pending = [...pending.filter((row) => row.id !== event.id), { ...event }].slice(-200);
  persist();
}

export function setListeningExportOwner(next: string | null): void {
  if (owner === next) return;
  generation += 1; refreshSequence += 1; owner = next; tracker = null; enabled = false; pending = []; flushing = false; checkpointAt = 0; bindings = '';
  if (next) void refreshListeningExport(next);
}

export async function refreshListeningExport(expectedOwner: string): Promise<void> {
  if (owner !== expectedOwner) return;
  const current = generation;
  const sequence = ++refreshSequence;
  try {
    const result = await requestJson<{ items: { enabled: boolean; provider: string; generation?: string | null }[] }>('/api/me/listening-export');
    if (sequence !== refreshSequence || generation !== current || owner !== expectedOwner) return;
    const accounts = Object.fromEntries(result.items.filter((entry) => entry.enabled && ['lastfm', 'listenbrainz'].includes(entry.provider) && typeof entry.generation === 'string')
      .map((entry) => [entry.provider, entry.generation as string]));
    const binding = JSON.stringify(Object.entries(accounts).sort());
    if (bindings !== binding) { generation += 1; flushing = false; tracker = null; bindings = binding; }
    enabled = Object.keys(accounts).length > 0;
    tracker = enabled ? tracker ?? new ListeningTracker(expectedOwner, undefined, accounts) : null;
    if (enabled) {
      try {
        const raw = localStorage.getItem(key(expectedOwner));
        const stored: unknown = raw && raw.length <= 100_000 ? JSON.parse(raw) : [];
        const valid = (entry: unknown): entry is ListeningEvent => {
          if (!entry || typeof entry !== 'object') return false;
          const value = entry as Partial<ListeningEvent>;
          return Boolean(value.account_generations && typeof value.account_generations === 'object' && Object.entries(value.account_generations).length <= 2 && Object.entries(value.account_generations).every(([provider, binding]) => ['lastfm', 'listenbrainz'].includes(provider) && typeof binding === 'string' && binding.length > 0 && binding.length <= 36)) && typeof value.id === 'string' && /^[a-f0-9-]{36}$/i.test(value.id) && value.owner_id === expectedOwner
            && typeof value.item_id === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(value.item_id)
            && typeof value.started_at === 'number' && Number.isInteger(value.started_at) && value.started_at >= Date.now() / 1000 - 7 * 86400
            && typeof value.listened_seconds === 'number' && Number.isFinite(value.listened_seconds) && value.listened_seconds >= 0 && value.listened_seconds <= 604800;
        };
        const cache = stored && typeof stored === 'object' && !Array.isArray(stored) ? stored as { pending?: unknown; current?: { event?: unknown; sample?: Partial<Sample> } } : null;
        const queued = cache?.pending ?? stored;
        if (Array.isArray(queued)) pending = queued.slice(-200).filter(valid);
        const saved = cache?.current;
        if (tracker && !tracker.current && valid(saved?.event) && JSON.stringify(Object.entries(saved.event.account_generations).sort()) === binding && saved?.sample?.track === saved.event.item_id
          && typeof saved.sample.index === 'number' && Number.isInteger(saved.sample.index) && saved.sample.index >= 0 && saved.sample.index < 5000
          && typeof saved.sample.position === 'number' && Number.isFinite(saved.sample.position) && saved.sample.position >= 0 && saved.sample.position <= 604800
          && typeof saved.sample.duration === 'number' && Number.isFinite(saved.sample.duration) && saved.sample.duration > 0 && saved.sample.duration <= 604800) {
          tracker.restore(saved.event, { ...saved.sample, at: Date.now(), playing: false } as Sample);
        }
      } catch { pending = []; }
      void flushListeningExport();
    } else {
      pending = []; persist();
    }
  } catch { /* Configuration and pending plays are retried on reconnect; no export starts before opt-in is verified. */ }
}

export function recordMusicListening(expectedOwner: string | null, track: MusicTrack | undefined, playback: MusicEngineState): void {
  if (owner !== expectedOwner) setListeningExportOwner(expectedOwner);
  if (!owner || !enabled || !tracker) return;
  const now = Date.now();
  for (const event of tracker.observe(track, playback, now)) enqueue(event);
  if (now - checkpointAt >= 10_000 || !playback.playing) {
    checkpointAt = now;
    if (tracker.current) enqueue(tracker.current);
    void flushListeningExport();
  }
}

export async function flushListeningExport(): Promise<void> {
  if (!owner || !enabled || flushing || !navigator.onLine) return;
  const current = generation;
  flushing = true;
  try {
    for (const event of [...pending]) {
      if (current !== generation) return;
      try {
        await requestJson('/api/me/listening-events', { method: 'POST', body: JSON.stringify(event), keepalive: true });
        if (current !== generation) return;
        pending = pending.filter((row) => row.id !== event.id || row.listened_seconds > event.listened_seconds);
        persist();
      } catch (error) {
        if (current !== generation) return;
        if (error instanceof ApiRequestError && [404, 409, 422].includes(error.status)) {
          pending = pending.filter((row) => row.id !== event.id); persist(); continue;
        }
        if (error instanceof ApiRequestError && [401, 403].includes(error.status)) { enabled = false; tracker = null; }
        return;
      }
    }
  } finally { if (current === generation) flushing = false; }
}

if (typeof window !== 'undefined') window.addEventListener('online', () => { if (owner) void refreshListeningExport(owner); });
