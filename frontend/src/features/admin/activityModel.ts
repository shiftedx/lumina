import type { ActivityHardware, ActivityMethod, ActivityServer, ActivitySession, ActivityVideo } from '../../types';
import { parseServerTime } from '../../utils';

export const NOT_AVAILABLE = 'Not available on this system';
export const METHOD_LABELS: Record<ActivityMethod, string> = { direct: 'Direct play', remux: 'Remux', transcode: 'Transcode', relay: 'Relay' };
export const HARDWARE_LABELS: Record<ActivityHardware, string> = { qsv: 'Intel QSV', vaapi: 'VAAPI', software: 'Software' };

const pair = (from: string | null, to: string | null) => (from && to && from !== to ? `${from} → ${to}` : to ?? from);

/** "HEVC → H.264 · 1080p · HDR tone-mapped"; empty when the server reported no video facts. */
export function videoLine(video: ActivityVideo | null): string {
  if (!video) return '';
  return [pair(video.from, video.to), video.height ? `${video.height}p` : null, video.tonemap ? 'HDR tone-mapped' : null].filter(Boolean).join(' · ');
}
export const audioLine = (audio: ActivitySession['audio']): string => (audio ? pair(audio.from, audio.to) ?? '' : '');

export const clientLine = (client: ActivitySession['client']) => [client.name, client.device].filter(Boolean).join(' · ');

/** "12 min", "1 h 05 min"; under a minute reads "less than a minute". */
export function spanLabel(seconds: number): string {
  const minutes = Math.floor(Math.max(0, seconds) / 60);
  if (minutes < 1) return 'less than a minute';
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')} min`;
}
export const playingFor = (startedAt: string, now = Date.now()) => `playing for ${spanLabel((now - parseServerTime(startedAt).getTime()) / 1000)}`;

const RELATIVE = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
export function relativeTime(value: string, now = Date.now()): string {
  const seconds = Math.round((parseServerTime(value).getTime() - now) / 1000);
  const steps: [Intl.RelativeTimeFormatUnit, number][] = [['day', 86400], ['hour', 3600], ['minute', 60]];
  for (const [unit, size] of steps) if (Math.abs(seconds) >= size) return RELATIVE.format(Math.trunc(seconds / size), unit);
  return 'just now';
}

export const gigabytes = (bytes: number) => `${(bytes / 1024 ** 3).toFixed(1)} GB`;
export function uptimeLabel(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  return days ? `${days} d ${hours} h` : `${hours} h ${Math.floor((seconds % 3600) / 60)} min`;
}

/** The encoder status as words: [tone, text]. */
export function hardwareStatus(hardware: ActivityServer['hardware']): ['ok' | 'attention' | 'muted', string] {
  if (hardware.disabled) return ['attention', `Hardware encoding disabled after ${hardware.failures} failure${hardware.failures === 1 ? '' : 's'}`];
  if (hardware.active) return ['ok', `${HARDWARE_LABELS[hardware.active]} active`];
  if (hardware.mode === 'off') return ['muted', 'Software only (hardware encoding is off)'];
  return ['muted', 'Hardware encoding ready, none in use right now'];
}
