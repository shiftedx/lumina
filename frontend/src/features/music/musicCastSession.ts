import { useSyncExternalStore } from 'react';

import { ApiRequestError, controlMusicCast, getMusicCastStatus } from '../../api';
import { getMusicSharedSession } from './musicSharedSession';

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

type CastStatus = { transport_state?: unknown; state?: unknown; volume?: unknown; position_seconds?: unknown; track?: { duration_seconds?: unknown; position_seconds?: unknown; track_number?: unknown } | null };

const OWNER = /^[A-Za-z0-9_-]{1,128}$/;
const TICKET = /^[A-Za-z0-9_-]{1,256}$/;
const keyFor = (owner: string) => `lumina:music:cast:${owner}`;
const finite = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null;
const boundedInteger = (value: unknown, minimum: number, maximum: number): value is number => typeof value === 'number' && Number.isInteger(value) && value >= minimum && value <= maximum;

let current: MusicCastSession | null = null;
let browserOwner: string | null = null;
let restoreGeneration = 0;
let lifecycleGeneration = 0;
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

function clearLegacy(owner: string): void {
  if (!OWNER.test(owner)) return;
  try { localStorage.removeItem(keyFor(owner)); }
  catch { /* storage may be unavailable; playback is memory-only */ }
}

function replaceCurrent(value: MusicCastSession | null): void {
  current = value;
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
  browserOwner = null;
  lifecycleGeneration += 1;
  restoreGeneration += 1;
  if (!value) {
    if (current) clearLegacy(current.owner);
    replaceCurrent(null);
    return;
  }
  if (!validSession(value)) return;
  clearLegacy(value.owner);
  replaceCurrent(value);
}

export function forgetMusicCast(owner: string): void {
  browserOwner = null;
  lifecycleGeneration += 1;
  restoreGeneration += 1;
  clearLegacy(owner);
  if (current?.owner === owner) replaceCurrent(null);
}

export function updateMusicCastStatus(ticket: string, raw: unknown): void {
  if (!current || current.ticket !== ticket) return;
  replaceCurrent(statusSession(current, raw));
}

/** Returns true when an expired or forbidden ticket was cleared. Transient failures retain the controller. */
export function handleMusicCastStatusError(ticket: string, error: unknown): boolean {
  if (!terminalStatusError(error) || current?.ticket !== ticket) return false;
  forgetMusicCast(current.owner);
  return true;
}

/** Validate a server-restored descriptor before publishing speaker controls. */
export async function restoreSharedMusicCast(value: MusicCastSession): Promise<void> {
  browserOwner = null;
  clearLegacy(value.owner);
  const generation = ++restoreGeneration;
  if (!validSession(value)) return;
  try {
    const status = await getMusicCastStatus(value.ticket);
    if (generation !== restoreGeneration) return;
    replaceCurrent(statusSession(value, status));
  } catch (error) {
    if (generation !== restoreGeneration) return;
    handleMusicCastStatusError(value.ticket, error);
    // A descriptor alone cannot establish a controller. Existing controllers
    // survive transient failures so ordinary status polling can retry.
  }
}

/** Forget legacy browser state; authenticated shared-session restore owns reload recovery. */
export async function restoreMusicCast(owner: string): Promise<void> {
  clearLegacy(owner);
  if (current?.owner !== owner) {
    if (browserOwner !== owner) browserOwner = null;
    if (current) { lifecycleGeneration += 1; restoreGeneration += 1; replaceCurrent(null); }
    return;
  }
  await restoreSharedMusicCast(current);
}

export async function stopMusicCast(owner?: string): Promise<void> {
  const generation = lifecycleGeneration;
  restoreGeneration += 1;
  if (owner) clearLegacy(owner);
  if (owner && current && current.owner !== owner) return;
  const leaving = current;
  let ticket = leaving?.ticket;
  if (!ticket && owner && OWNER.test(owner)) {
    // Confirmed browser output can keep using downloaded audio offline.
    // Cold reloads and online playback resolve the authenticated canonical output.
    if (browserOwner === owner && navigator.onLine === false) return;
    browserOwner = null;
    const restore = restoreGeneration;
    const shared = await getMusicSharedSession();
    if (generation !== lifecycleGeneration) return;
    if (shared.active_output.kind === 'browser' && restore === restoreGeneration) browserOwner = owner;
    if (shared.active_output.kind === 'sonos' && TICKET.test(shared.active_output.ticket_id)) ticket = shared.active_output.ticket_id;
  }
  if (!ticket) return;
  try { await controlMusicCast(ticket, 'stop'); }
  finally {
    if (leaving) clearLegacy(leaving.owner);
    if (generation === lifecycleGeneration) {
      restoreGeneration += 1;
      if (current?.ticket === ticket && (!owner || current.owner === owner)) replaceCurrent(null);
    }
  }
}
