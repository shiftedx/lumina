import type { AutomationRoot, AutomationSkipReason, ScanSchedule, WatchIntervalS } from '../../types';
import { formatDateTime, parseServerTime } from '../../utils';
import type { StatusTone } from '../../ui';

const count = new Intl.NumberFormat('en-US');
const two = (value: number) => String(value).padStart(2, '0');
const hhmm = (date: Date) => `${two(date.getHours())}:${two(date.getMinutes())}`;
const plural = (n: number, one: string, many: string) => `${count.format(n)} ${n === 1 ? one : many}`;

export const HOURS = Array.from({ length: 24 }, (_, value) => ({ value, label: `${two(value)}:00` }));
export const SCHEDULES: { value: ScanSchedule; label: string }[] = [
  { value: 'off', label: 'Off' }, { value: '15m', label: 'Every 15 minutes' }, { value: '1h', label: 'Every hour' },
  { value: '6h', label: 'Every 6 hours' }, { value: 'nightly', label: 'Nightly' },
];
export const INTERVALS: { value: `${WatchIntervalS}`; label: string }[] = [
  { value: '60', label: '1 min' }, { value: '300', label: '5 min' }, { value: '900', label: '15 min' },
];

export interface StateLine { text: string; tone: StatusTone; action?: 'review' }

/** Copy is spec 7.3; tones map the spec's neutral to muted and warning to attention. */
export function stateLine(root: AutomationRoot): StateLine {
  const { state, state_detail: detail, active_run: active } = root;
  switch (state) {
    case 'idle': return { text: 'Up to date', tone: 'ok' };
    case 'scanning': return {
      text: active?.scope_dirs ? `Scanning ${plural(active.scope_dirs, 'changed folder', 'changed folders')}…` : `Scanning…${active ? ` ${plural(active.inspected, 'file', 'files')} checked` : ''}`,
      tone: 'muted',
    };
    case 'preparing': return { text: `Getting ready to watch: ${count.format(detail.dirs_listed)}${detail.dirs_total ? ` of about ${count.format(detail.dirs_total)}` : ''} folders`, tone: 'muted' };
    case 'waiting_files': return { text: `Waiting for ${plural(detail.pending_files, 'file', 'files')} to finish copying`, tone: 'muted' };
    case 'waiting_confirmation': return { text: `Paused: ${plural(detail.pending_files, 'file looks', 'files look')} missing. Review them under Imports.`, tone: 'attention', action: 'review' };
    case 'offline': return { text: `Folder unavailable${detail.since ? ` since ${hhmm(parseServerTime(detail.since))}` : ''}. Scans resume when it's back.`, tone: 'danger' };
    case 'unresponsive': return { text: "The folder isn't answering. Scans resume when it does.", tone: 'danger' };
    case 'too_large': return { text: 'Too many folders to watch (over 50,000). Scheduled scans still run.', tone: 'attention' };
    case 'needs_first_import': return { text: 'Not imported yet', tone: 'muted' };
  }
}

const STOPPED: Record<string, string> = {
  offline: 'folder unavailable', permission_denied: 'permission denied', identity_mismatch: 'folder changed',
  root_removed: 'folder removed', root_empty: 'folder is empty',
};

function whenLine(date: Date, now: Date): string {
  const minutes = Math.floor((now.getTime() - date.getTime()) / 60_000);
  if (minutes >= 0 && minutes < 60) return `${Math.max(minutes, 1)} min ago`;
  const day = (value: Date) => new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const diff = Math.round((day(now) - day(date)) / 86_400_000);
  if (diff === 0) return `today ${hhmm(date)}`;
  if (diff === 1) return `yesterday ${hhmm(date)}`;
  return formatDateTime(date.toISOString());
}

export function lastScanLine(root: AutomationRoot, now: Date): string {
  const run = root.last_run;
  if (!run) return 'Last scan: never';
  const when = run.finished_at ? whenLine(parseServerTime(run.finished_at), now) : '';
  const parts = [when];
  if (run.state === 'failed' || run.state === 'interrupted' || run.state === 'cancelled') {
    parts.push(`stopped: ${(run.error && STOPPED[run.error]) || run.error || run.state}`);
  } else {
    const scope = run.scope_dirs === null ? 'whole folder' : plural(run.scope_dirs, 'folder', 'folders');
    parts.push(`${scope}${run.trigger === 'watch' ? ' (watched)' : ''}`);
    const { indexed, relinked, missing } = run.counters;
    parts.push([`${count.format(indexed)} new`, relinked ? `${count.format(relinked)} moved` : '', missing ? `${count.format(missing)} missing` : ''].filter(Boolean).join(', '));
  }
  return `Last scan: ${parts.filter(Boolean).join(' · ')}`;
}

export function nextScanLine(root: AutomationRoot, now: Date): string {
  const watching = root.watch ? ' · watching for changes' : '';
  let text: string;
  if (!root.next_scan_at) {
    text = root.schedule === 'off' ? 'off'
      : root.state === 'offline' ? 'when the folder is back'
      : root.state === 'scanning' ? 'after the current scan'
      : root.state === 'waiting_confirmation' ? 'after you review the missing files' : 'off';
  } else {
    const next = parseServerTime(root.next_scan_at);
    const ahead = (next.getTime() - now.getTime()) / 60_000;
    // "Tonight" covers evening and small-hours times within the next day.
    const tonight = ahead < 24 * 60 && (next.getHours() >= 18 || next.getHours() < 6);
    text = ahead < 120 ? `in ${Math.max(Math.round(ahead), 1)} min` : tonight ? `tonight at ${hhmm(next)}` : formatDateTime(next.toISOString());
  }
  return `Next scan: ${text}${watching}`;
}

export function scanToast(label: string, outcome: 'started' | AutomationSkipReason): string {
  switch (outcome) {
    case 'started': return `Scanning ${label}`;
    case 'scanning': return `Already scanning ${label}`;
    case 'offline': case 'unresponsive': return `${label} isn't available right now`;
    case 'waiting_confirmation': return `${label} is waiting for you to confirm missing files`;
    case 'needs_first_import': return 'Import this folder once under Imports first';
  }
}
