import { describe, expect, it } from 'vitest';

import type { AutomationRoot } from '../../types';
import { HOURS, lastScanLine, nextScanLine, scanToast, stateLine } from './libraryAutomationText';

const NOW = new Date(2026, 9, 2, 14, 0, 0);
const at = (day: number, hour: number, minute = 0) => new Date(2026, 9, day, hour, minute).toISOString();
const root = (patch: Partial<AutomationRoot> = {}): AutomationRoot => ({
  root_id: 'r1', label: 'TV', schedule: 'nightly', watch: false, watch_interval_s: 300, state: 'idle',
  state_detail: { pending_files: 0, dirs_listed: 0, dirs_total: null, since: null },
  active_run: null, last_run: null, last_full_run_at: null, next_scan_at: null, last_skip: null, ...patch,
});
const run = (patch: Record<string, unknown> = {}) => ({
  id: 'x', trigger: 'scheduled' as const, state: 'succeeded', finished_at: at(2, 3), scope_dirs: null,
  counters: { indexed: 12, updated: 0, relinked: 0, missing: 0 }, error: null, ...patch,
});

describe('stateLine', () => {
  it('gives the exact copy and tone for every state', () => {
    expect(stateLine(root())).toEqual({ text: 'Up to date', tone: 'ok' });
    expect(stateLine(root({ state: 'scanning', active_run: { id: 'a', trigger: 'manual', scope_dirs: null, inspected: 1240 } }))).toEqual({ text: 'Scanning… 1,240 files checked', tone: 'muted' });
    expect(stateLine(root({ state: 'scanning', active_run: { id: 'a', trigger: 'watch', scope_dirs: 2, inspected: 9 } })).text).toBe('Scanning 2 changed folders…');
    expect(stateLine(root({ state: 'preparing', state_detail: { pending_files: 0, dirs_listed: 1203, dirs_total: 4210, since: null } }))).toEqual({ text: 'Getting ready to watch: 1,203 of about 4,210 folders', tone: 'muted' });
    expect(stateLine(root({ state: 'waiting_files', state_detail: { pending_files: 3, dirs_listed: 0, dirs_total: null, since: null } }))).toEqual({ text: 'Waiting for 3 files to finish copying', tone: 'muted' });
    expect(stateLine(root({ state: 'waiting_confirmation', state_detail: { pending_files: 312, dirs_listed: 0, dirs_total: null, since: null } }))).toEqual({ text: 'Paused: 312 files look missing. Review them under Imports.', tone: 'attention', action: 'review' });
    expect(stateLine(root({ state: 'offline', state_detail: { pending_files: 0, dirs_listed: 0, dirs_total: null, since: at(2, 14, 2) } }))).toEqual({ text: "Folder unavailable since 14:02. Scans resume when it's back.", tone: 'danger' });
    expect(stateLine(root({ state: 'unresponsive' }))).toEqual({ text: "The folder isn't answering. Scans resume when it does.", tone: 'danger' });
    expect(stateLine(root({ state: 'too_large' }))).toEqual({ text: 'Too many folders to watch (over 50,000). Scheduled scans still run.', tone: 'attention' });
    expect(stateLine(root({ state: 'needs_first_import' }))).toEqual({ text: 'Not imported yet', tone: 'muted' });
  });
});

describe('lastScanLine', () => {
  it('reads never, watched, yesterday, moved and stopped runs', () => {
    expect(lastScanLine(root(), NOW)).toBe('Last scan: never');
    expect(lastScanLine(root({ last_run: run({ trigger: 'watch', finished_at: at(2, 13, 58), scope_dirs: 2, counters: { indexed: 2, updated: 0, relinked: 0, missing: 0 } }) }), NOW)).toBe('Last scan: 2 min ago · 2 folders (watched) · 2 new');
    expect(lastScanLine(root({ last_run: run({ finished_at: at(1, 3) }) }), NOW)).toBe('Last scan: yesterday 03:00 · whole folder · 12 new');
    expect(lastScanLine(root({ last_run: run({ finished_at: at(2, 3) }) }), NOW)).toBe('Last scan: today 03:00 · whole folder · 12 new');
    expect(lastScanLine(root({ last_run: run({ counters: { indexed: 12, updated: 0, relinked: 1, missing: 0 } }) }), NOW)).toBe('Last scan: today 03:00 · whole folder · 12 new, 1 moved');
    expect(lastScanLine(root({ last_run: run({ state: 'failed', error: 'offline', finished_at: at(1, 3) }) }), NOW)).toBe('Last scan: yesterday 03:00 · stopped: folder unavailable');
  });
});

describe('nextScanLine', () => {
  it('covers off, watching, offline and minutes ahead', () => {
    expect(nextScanLine(root({ schedule: 'off' }), NOW)).toBe('Next scan: off');
    expect(nextScanLine(root({ schedule: 'off', watch: true }), NOW)).toBe('Next scan: off · watching for changes');
    expect(nextScanLine(root({ state: 'offline' }), NOW)).toBe('Next scan: when the folder is back');
    expect(nextScanLine(root({ state: 'scanning' }), NOW)).toBe('Next scan: after the current scan');
    expect(nextScanLine(root({ next_scan_at: new Date(NOW.getTime() + 42 * 60_000).toISOString() }), NOW)).toBe('Next scan: in 42 min');
    expect(nextScanLine(root({ next_scan_at: at(3, 3) }), NOW)).toBe('Next scan: tonight at 03:00');
  });
});

describe('scanToast and HOURS', () => {
  it('words each outcome', () => {
    expect(scanToast('TV', 'started')).toBe('Scanning TV');
    expect(scanToast('TV', 'scanning')).toBe('Already scanning TV');
    expect(scanToast('TV', 'offline')).toBe("TV isn't available right now");
    expect(scanToast('TV', 'unresponsive')).toBe("TV isn't available right now");
    expect(scanToast('TV', 'waiting_confirmation')).toBe('TV is waiting for you to confirm missing files');
    expect(scanToast('TV', 'needs_first_import')).toBe('Import this folder once under Imports first');
  });
  it('lists 24 hours', () => {
    expect(HOURS).toHaveLength(24);
    expect([HOURS[0], HOURS[23]]).toEqual([{ value: 0, label: '00:00' }, { value: 23, label: '23:00' }]);
  });
});
