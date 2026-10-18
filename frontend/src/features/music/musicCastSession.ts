import { useSyncExternalStore } from 'react';

import { ApiRequestError, controlMusicCast, getMusicCastStatus } from '../../api';

export interface MusicCastSession {
  owner: string;
  ticket: string;
  name: string;
  playing: boolean;
  /** Index in the full browser queue represented by Sonos track number 1. */
  baseIndex: number;
  queueIndices?: number[];
  index: number;
  position: number;
  duration: number;
  volume: number | null;
  queued: number;
}

type StoredCast = { version: 1; expiresAt: number; session: MusicCastSession };
type CastStatus = { transport_state?: unknown; state?: unknown; volume?: unknown; position_seconds?: unknown; track?: { duration_seconds?: unknown; position_seconds?: unknown; track_number?: unknown } | null };

const CAST_TTL_MS = 24 * 60 * 60 * 1000;
const OWNER = /^[A-Za-z0-9_-]{1,128}$/;
const TICKET = /^[A-Za-z0-9_-]{1,256}$/;
const keyFor = (owner: string) => `lumina:music:cast:${owner}`;
const finite = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null;
const boundedInteger = (value: unknown, minimum: number, maximum: number): value is number => typeof value === 'number' && Number.isInteger(value) && value >= minimum && value <= maximum;

let current: MusicCastSession | null = null;
let currentExpiresAt: number | null = null;
let restoreGeneration = 0;
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
const notify = () => listeners.forEach((listener) => listener());

function validSession(value: unknown, owner?: string): value is MusicCastSession {
  if (!value || typeof value !== 'object') return false;
  const cast = value as Partial<MusicCastSession>;
  return typeof cast.owner === 'string' && OWNER.test(cast.owner) && (!owner || cast.owner === owner)
    && typeof cast.ticket === 'string' && TICKET.test(cast.ticket)
    && typeof cast.name === 'string' && cast.name.length > 0 && cast.name.length <= 120
    && typeof cast.playing === 'boolean'
    && boundedInteger(cast.baseIndex, 0, 5000)
    && boundedInteger(cast.index, 0, 5000)
    && typeof cast.position === 'number' && Number.isFinite(cast.position) && cast.position >= 0 && cast.position <= 7 * 24 * 60 * 60
    && typeof cast.duration === 'number' && Number.isFinite(cast.duration) && cast.duration >= 0 && cast.duration <= 7 * 24 * 60 * 60
    && (cast.volume === null || typeof cast.volume === 'number' && Number.isFinite(cast.volume) && cast.volume >= 0 && cast.volume <= 1)
    && boundedInteger(cast.queued, 1, 100)
    && (!cast.queueIndices || Array.isArray(cast.queueIndices) && cast.queueIndices.length === cast.queued && cast.queueIndices.every((value) => boundedInteger(value, 0, 5000)));
}

function readStored(owner: string): StoredCast | null {
  if (!OWNER.test(owner)) return null;
  try {
    const raw = localStorage.getItem(keyFor(owner));
    if (!raw || raw.length > 2048) {
      localStorage.removeItem(keyFor(owner));
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<StoredCast> | null;
    if (!parsed || parsed.version !== 1 || typeof parsed.expiresAt !== 'number' || !Number.isFinite(parsed.expiresAt)
      || parsed.expiresAt <= Date.now() || parsed.expiresAt > Date.now() + CAST_TTL_MS || !validSession(parsed.session, owner)) {
      localStorage.removeItem(keyFor(owner));
      return null;
    }
    return parsed as StoredCast;
  } catch {
    try { localStorage.removeItem(keyFor(owner)); } catch { /* storage may be unavailable */ }
    return null;
  }
}

function writeStored(session: MusicCastSession, expiresAt: number): void {
  try { localStorage.setItem(keyFor(session.owner), JSON.stringify({ version: 1, expiresAt, session } satisfies StoredCast)); }
  catch { /* private browsing or a full quota must not interrupt playback */ }
}

function replaceCurrent(value: MusicCastSession | null, expiresAt: number | null, persist: boolean): void {
  current = value;
  currentExpiresAt = expiresAt;
  if (value && persist && expiresAt) writeStored(value, expiresAt);
  notify();
}

function statusCode(error: unknown): number | null {
  if (error instanceof ApiRequestError) return error.status;
  if (!error || typeof error !== 'object') return null;
  const status = (error as { status?: unknown }).status;
  return typeof status === 'number' ? status : null;
}

function terminalStatusError(error: unknown): boolean {
  const status = statusCode(error);
  return status === 401 || status === 403 || status === 404;
}

function statusSession(session: MusicCastSession, raw: unknown): MusicCastSession {
  const status = (raw && typeof raw === 'object' ? raw : {}) as CastStatus;
  const trackNumber = finite(status.track?.track_number);
  const relative = trackNumber !== null && Number.isInteger(trackNumber) && trackNumber >= 1
    ? Math.min(session.queued - 1, trackNumber - 1) : session.queueIndices?.indexOf(session.index) ?? session.index - session.baseIndex;
  const position = finite(status.track?.position_seconds) ?? finite(status.position_seconds) ?? session.position;
  const duration = finite(status.track?.duration_seconds) ?? session.duration;
  const volume = finite(status.volume);
  return {
    ...session,
    index: session.queueIndices?.[Math.max(0, relative)] ?? session.baseIndex + Math.max(0, relative),
    position: Math.max(0, position),
    duration: Math.max(0, duration),
    volume: volume === null ? session.volume : Math.max(0, Math.min(1, volume > 1 ? volume / 100 : volume)),
    playing: status.transport_state === 'PLAYING' || status.state === 'PLAYING',
  };
}

export const musicCast = (): MusicCastSession | null => current;
export const useMusicCast = (): MusicCastSession | null => useSyncExternalStore(subscribe, musicCast);

export function setMusicCast(value: MusicCastSession | null): void {
  restoreGeneration += 1;
  if (!value) {
    if (current) try { localStorage.removeItem(keyFor(current.owner)); } catch { /* storage may be unavailable */ }
    replaceCurrent(null, null, false);
    return;
  }
  if (!validSession(value)) return;
  const expiresAt = current?.ticket === value.ticket && currentExpiresAt && currentExpiresAt > Date.now()
    ? currentExpiresAt : Date.now() + CAST_TTL_MS;
  replaceCurrent(value, expiresAt, true);
}

export function forgetMusicCast(owner: string): void {
  restoreGeneration += 1;
  if (OWNER.test(owner)) try { localStorage.removeItem(keyFor(owner)); } catch { /* storage may be unavailable */ }
  if (current?.owner === owner) replaceCurrent(null, null, false);
}

export function updateMusicCastStatus(ticket: string, raw: unknown): void {
  if (!current || current.ticket !== ticket || !currentExpiresAt) return;
  replaceCurrent(statusSession(current, raw), currentExpiresAt, true);
}

/** Returns true when an expired or forbidden ticket was cleared. Transient failures retain the controller. */
export function handleMusicCastStatusError(ticket: string, error: unknown): boolean {
  if (!terminalStatusError(error) || current?.ticket !== ticket) return false;
  forgetMusicCast(current.owner);
  return true;
}

/** Validate a server-restored descriptor before publishing speaker controls. */
export async function restoreSharedMusicCast(value: MusicCastSession): Promise<void> {
  if (!validSession(value)) return;
  const generation = ++restoreGeneration;
  try {
    const status = await getMusicCastStatus(value.ticket);
    if (generation !== restoreGeneration) return;
    const expiresAt = Date.now() + CAST_TTL_MS;
    replaceCurrent(statusSession(value, status), expiresAt, true);
  } catch {
    // The server already rejected stale/revoked tickets. Transient failures do
    // not manufacture a controller from a descriptor alone.
  }
}

export async function restoreMusicCast(owner: string): Promise<void> {
  const generation = ++restoreGeneration;
  const stored = readStored(owner);
  if (!stored) {
    if (current?.owner !== owner) replaceCurrent(null, null, false);
    return;
  }
  // Expose transport controls while validating, but never claim the speaker is still playing first.
  replaceCurrent({ ...stored.session, playing: false }, stored.expiresAt, false);
  try {
    const status = await getMusicCastStatus(stored.session.ticket);
    if (generation !== restoreGeneration || current?.ticket !== stored.session.ticket) return;
    replaceCurrent(statusSession(current, status), stored.expiresAt, true);
  } catch (error) {
    if (generation !== restoreGeneration || current?.ticket !== stored.session.ticket) return;
    if (terminalStatusError(error)) forgetMusicCast(owner);
    // A gateway/network failure leaves the paused controller visible. MusicPlayer polling retries it.
  }
}

function forgetTicket(owner: string, ticket: string): void {
  restoreGeneration += 1;
  const stored = readStored(owner);
  if (stored?.session.ticket === ticket) try { localStorage.removeItem(keyFor(owner)); } catch { /* storage may be unavailable */ }
  if (current?.owner === owner && current.ticket === ticket) replaceCurrent(null, null, false);
}

export async function stopMusicCast(owner?: string): Promise<void> {
  const leaving = owner
    ? current?.owner === owner ? current : readStored(owner)?.session ?? null
    : current;
  if (!leaving) return;
  try { await controlMusicCast(leaving.ticket, 'stop'); }
  finally { forgetTicket(leaving.owner, leaving.ticket); }
}
