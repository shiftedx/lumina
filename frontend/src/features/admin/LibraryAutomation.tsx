import { useCallback, useContext, useEffect, useRef, useState } from 'react';

import { getLibraryAutomation, scanLibraries, setNightHour, updateRootAutomation } from '../../api';
import type { AutomationRoot, AutomationRootPatch, AutomationState, ScanSchedule, WatchIntervalS } from '../../types';
import { Button, Field, fieldProps, Select, SegmentedControl, StatusText, TextButton, useToast } from '../../ui';
import { errorMessage } from '../../utils';
import { settingDomId, SettingSwitch } from '../settings/SettingRow';
import { LibraryRefreshContext } from './AdminStorage';
import { HOURS, INTERVALS, lastScanLine, nextScanLine, SCHEDULES, scanToast, stateLine } from './libraryAutomationText';
import { useAdminResource } from './useAdminResource';

const BLOCKED: AutomationState[] = ['scanning', 'preparing', 'waiting_confirmation', 'offline', 'unresponsive', 'needs_first_import'];
const FAST: AutomationState[] = ['scanning', 'preparing', 'waiting_files'];
const goToImports = () => document.getElementById(settingDomId('library.imports'))?.scrollIntoView({ block: 'start' });

const LIVE: Partial<Record<AutomationRoot['state'], string>> = { scanning: 'Scanning', preparing: 'Getting ready to watch', waiting_files: 'Waiting for files to finish copying' };

/** Settings → Library & storage → Scans and folder watching. Every control saves at once. */
export function LibraryAutomation() {
  const { data, setData, error, loading, reload } = useAdminResource(getLibraryAutomation, 'Lumina could not load scan settings.');
  const { bump } = useContext(LibraryRefreshContext);
  const toast = useToast();
  const busy = useRef(0);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [scanning, setScanning] = useState(false);
  const [hourError, setHourError] = useState<string | null>(null);

  // Re-armed after every load or save settles; a tick that lands mid-save waits one more beat.
  useEffect(() => {
    if (!data || loading) return undefined;
    let timer = 0;
    const arm = () => {
      timer = window.setTimeout(() => (busy.current || document.hidden ? arm() : reload()), data.roots.some((root) => FAST.includes(root.state)) ? 5_000 : 30_000);
    };
    arm();
    // A tab that was hidden skips ticks; catch up the moment it is shown again.
    const shown = () => { if (!document.hidden && !busy.current) { window.clearTimeout(timer); reload(); } };
    document.addEventListener('visibilitychange', shown);
    return () => { window.clearTimeout(timer); document.removeEventListener('visibilitychange', shown); };
  }, [data, loading, reload]);

  const replace = useCallback((next: AutomationRoot) => setData((current) => (current ? { ...current, roots: current.roots.map((root) => (root.root_id === next.root_id ? next : root)) } : current)), [setData]);
  // Functional, so two quick edits on one folder never restore each other's stale copy.
  const merge = useCallback((id: string, part: Partial<AutomationRoot>) => setData((current) => (current ? { ...current, roots: current.roots.map((root) => (root.root_id === id ? { ...root, ...part } : root)) } : current)), [setData]);

  if (error && !data) return <p className="auth-error" role="alert">{error} <Button onClick={reload} variant="quiet">Try again</Button></p>;
  if (!data) return <p aria-busy="true" className="admin-note">Loading…</p>;

  const save = async (root: AutomationRoot, patch: AutomationRootPatch, optimistic: Partial<AutomationRoot>) => {
    setErrors((current) => ({ ...current, [root.root_id]: '' }));
    merge(root.root_id, optimistic);
    busy.current += 1;
    try {
      replace(await updateRootAutomation(root.root_id, patch));
    } catch (failure) {
      merge(root.root_id, Object.fromEntries(Object.keys(optimistic).map((key) => [key, root[key as keyof AutomationRoot]])));
      setErrors((current) => ({ ...current, [root.root_id]: errorMessage(failure, 'Lumina could not save this change.') }));
    } finally {
      busy.current -= 1;
    }
  };

  const changeHour = async (hour: number) => {
    const before = data;
    setHourError(null);
    setData({ ...data, night_hour: hour });
    busy.current += 1;
    try {
      setData(await setNightHour(hour));
    } catch (failure) {
      setData(before);
      setHourError(errorMessage(failure, 'Lumina could not save this change.'));
    } finally {
      busy.current -= 1;
    }
  };

  const scanNow = async (root: AutomationRoot) => {
    if (scanning) return;
    setScanning(true);
    try {
      const result = await scanLibraries(root.root_id);
      toast({ tone: result.started.length || result.queued.length ? 'success' : 'info', message: scanToast(root.label, result.started.length || result.queued.length ? 'started' : result.skipped[0]?.reason ?? 'started') });
    } catch (failure) {
      toast({ tone: 'error', message: errorMessage(failure, 'Lumina could not start the scan.') });
    }
    setScanning(false);
    bump();
    reload();
  };

  const now = new Date();
  return (
    <div className="g-subrows">
      {data.poller.stalled ? <p><StatusText tone="danger">Folder watching is stuck on a folder that isn&apos;t answering. Restart Lumina if it stays stuck.</StatusText></p> : null}
      <Field hint={`Server time (${data.server_timezone})`} label="Nightly scans start at">
        {(ids) => (
          <Select {...fieldProps(ids)} onChange={(event) => void changeHour(Number(event.target.value))} value={data.night_hour}>
            {HOURS.map((hour) => <option key={hour.value} value={hour.value}>{hour.label}</option>)}
          </Select>
        )}
      </Field>
      {hourError ? <p className="auth-error" role="alert">{hourError}</p> : null}
      {data.roots.map((root) => {
        const state = stateLine(root);
        const first = root.state === 'needs_first_import';
        return (
          <section className="g-panel g-subrows" key={root.root_id}>
            <h3 className="g-panel-title">{root.label}</h3>
            <p>
              <StatusText tone={state.tone}>{state.text}</StatusText>
              {state.action ? <> <TextButton onClick={goToImports}>Review</TextButton></> : null}
            </p>
            {/* The 5 s poll rewrites counters; only a change of state is announced. */}
            <span aria-live="polite" className="sr-only" role="status">{LIVE[root.state] ?? state.text}</span>
            {first ? <p className="g-setting-note">Import this folder once under Imports to turn on scans. <TextButton onClick={goToImports}>Go to Imports</TextButton></p> : null}
            <Field label="Scheduled scan">
              {(ids) => (
                <Select {...fieldProps(ids)} disabled={first} onChange={(event) => void save(root, { schedule: event.target.value as ScanSchedule }, { schedule: event.target.value as ScanSchedule })} value={root.schedule}>
                  {SCHEDULES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </Select>
              )}
            </Field>
            <SettingSwitch checked={root.watch} disabled={first} hint={root.watch && root.schedule === 'off' ? 'Pair watching with a nightly scan to catch edited artwork and details.' : undefined} label="Watch for new files" onChange={(watch) => void save(root, { watch }, { watch })} />
            {root.watch ? (
              <SegmentedControl legend="Check every" onChange={(value) => void save(root, { watch_interval_s: Number(value) as WatchIntervalS }, { watch_interval_s: Number(value) as WatchIntervalS })} options={INTERVALS} value={`${root.watch_interval_s}`} />
            ) : null}
            <p className="g-setting-note">{lastScanLine(root, now)}</p>
            <p className="g-setting-note">{nextScanLine(root, now)}</p>
            <div className="g-actions"><Button disabled={scanning || BLOCKED.includes(root.state)} onClick={() => void scanNow(root)}>Scan now</Button></div>
            {errors[root.root_id] ? <p className="auth-error" role="alert">{errors[root.root_id]}</p> : null}
          </section>
        );
      })}
    </div>
  );
}
