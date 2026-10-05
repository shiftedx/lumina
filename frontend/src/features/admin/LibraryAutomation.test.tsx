import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import axe from 'axe-core';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getLibraryAutomation, scanLibraries, setNightHour, updateRootAutomation } from '../../api';
import { ToastProvider } from '../../ui';
import type { AutomationRoot, AutomationState, LibraryAutomation as Snapshot } from '../../types';
import { LibraryRefreshContext } from './AdminStorage';
import { LibraryAutomation } from './LibraryAutomation';

vi.mock('../../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api')>()),
  getLibraryAutomation: vi.fn(), updateRootAutomation: vi.fn(), setNightHour: vi.fn(), scanLibraries: vi.fn(),
}));

const root = (patch: Partial<AutomationRoot> = {}): AutomationRoot => ({
  root_id: 'r1', label: 'TV', schedule: 'off', watch: false, watch_interval_s: 300, state: 'idle',
  state_detail: { pending_files: 0, dirs_listed: 0, dirs_total: null, since: null },
  active_run: null, last_run: null, last_full_run_at: null, next_scan_at: null, last_skip: null, ...patch,
});
const snapshot = (roots: AutomationRoot[] = [root(), root({ root_id: 'r2', label: 'Movies' })], patch: Partial<Snapshot> = {}): Snapshot => ({
  server_timezone: 'UTC', night_hour: 3, poller: { heartbeat_at: null, stalled: false }, roots, ...patch,
});
const card = (label: string) => screen.getByRole('heading', { name: label }).closest('section') as HTMLElement;
const draw = (bump = vi.fn()) => render(<ToastProvider><LibraryRefreshContext.Provider value={{ version: 0, bump }}><LibraryAutomation /></LibraryRefreshContext.Provider></ToastProvider>);

beforeEach(() => {
  for (const mocked of [getLibraryAutomation, updateRootAutomation, setNightHour, scanLibraries]) vi.mocked(mocked).mockReset();
  vi.mocked(getLibraryAutomation).mockResolvedValue(snapshot());
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('LibraryAutomation', () => {
  it('renders one card per root returned and none for a root the API omits', async () => {
    draw();
    await screen.findByRole('heading', { name: 'TV' });
    expect(screen.getByRole('heading', { name: 'Movies' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Music' })).toBeNull();
  });

  it('changing the schedule PATCHes and shows the returned value', async () => {
    vi.mocked(updateRootAutomation).mockResolvedValue(root({ schedule: 'nightly' }));
    draw();
    await screen.findByRole('heading', { name: 'TV' });
    const select = within(card('TV')).getByLabelText('Scheduled scan') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'nightly' } });
    await waitFor(() => expect(updateRootAutomation).toHaveBeenCalledWith('r1', { schedule: 'nightly' }));
    await waitFor(() => expect((within(card('TV')).getByLabelText('Scheduled scan') as HTMLSelectElement).value).toBe('nightly'));
  });

  it("a failed save reverts the control and shows the server's message", async () => {
    vi.mocked(updateRootAutomation).mockRejectedValue(new Error('Import this folder once before scheduling it'));
    draw();
    await screen.findByRole('heading', { name: 'TV' });
    fireEvent.change(within(card('TV')).getByLabelText('Scheduled scan'), { target: { value: 'nightly' } });
    expect((await within(card('TV')).findByText('Import this folder once before scheduling it'))).toBeTruthy();
    expect((within(card('TV')).getByLabelText('Scheduled scan') as HTMLSelectElement).value).toBe('off');
  });

  it('the Watch switch reveals Check every and saves the interval', async () => {
    vi.mocked(updateRootAutomation).mockResolvedValueOnce(root({ watch: true })).mockResolvedValueOnce(root({ watch: true, watch_interval_s: 900 }));
    draw();
    await screen.findByRole('heading', { name: 'TV' });
    expect(within(card('TV')).queryByText('Check every')).toBeNull();
    fireEvent.click(within(card('TV')).getByRole('switch', { name: 'Watch for new files' }));
    await waitFor(() => expect(updateRootAutomation).toHaveBeenCalledWith('r1', { watch: true }));
    expect(await within(card('TV')).findByText('Check every')).toBeTruthy();
    fireEvent.click(within(card('TV')).getByRole('radio', { name: '15 min' }));
    await waitFor(() => expect(updateRootAutomation).toHaveBeenCalledWith('r1', { watch_interval_s: 900 }));
  });

  it('Watch on with schedule off shows the pairing hint', async () => {
    vi.mocked(getLibraryAutomation).mockResolvedValue(snapshot([root({ watch: true })]));
    draw();
    expect(await screen.findByText(/Pair watching with a nightly scan/)).toBeTruthy();
  });

  it('a not-imported root disables its controls and offers Go to Imports', async () => {
    vi.mocked(getLibraryAutomation).mockResolvedValue(snapshot([root({ state: 'needs_first_import' })]));
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    render(<><div id="setting-library-imports" /><ToastProvider><LibraryAutomation /></ToastProvider></>);
    await screen.findByRole('heading', { name: 'TV' });
    const tv = card('TV');
    expect((within(tv).getByLabelText('Scheduled scan') as HTMLSelectElement).disabled).toBe(true);
    expect((within(tv).getByRole('switch') as HTMLInputElement).disabled).toBe(true);
    expect((within(tv).getByRole('button', { name: 'Scan now' }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(tv).getByText('Import this folder once under Imports to turn on scans.')).toBeTruthy();
    fireEvent.click(within(tv).getByRole('button', { name: 'Go to Imports' }));
    expect(scroll).toHaveBeenCalled();
  });

  it('Scan now calls scanLibraries(root id), toasts, and reloads', async () => {
    vi.mocked(scanLibraries).mockResolvedValue({ started: [{ root_id: 'r1', run_id: 'x' }], queued: [], skipped: [] });
    const bump = vi.fn();
    draw(bump);
    await screen.findByRole('heading', { name: 'TV' });
    fireEvent.click(within(card('TV')).getByRole('button', { name: 'Scan now' }));
    expect(await screen.findByText('Scanning TV')).toBeTruthy();
    expect(scanLibraries).toHaveBeenCalledWith('r1');
    expect(bump).toHaveBeenCalled();
    await waitFor(() => expect(getLibraryAutomation).toHaveBeenCalledTimes(2));
  });

  it.each<[AutomationState, RegExp]>([
    ['scanning', /^Scanning/], ['preparing', /^Getting ready to watch/], ['waiting_confirmation', /^Paused:/],
    ['offline', /^Folder unavailable/], ['unresponsive', /isn't answering/],
  ])('Scan now is disabled while %s and the state line says why', async (state, line) => {
    vi.mocked(getLibraryAutomation).mockResolvedValue(snapshot([root({ state })]));
    draw();
    await screen.findByRole('heading', { name: 'TV' });
    expect((within(card('TV')).getByRole('button', { name: 'Scan now' }) as HTMLButtonElement).disabled).toBe(true);
    const live = card('TV').querySelector('[aria-live="polite"]') as HTMLElement;
    expect(line.test(live.textContent ?? '')).toBe(true);
  });

  it('the nightly hour select lists 24 hours and saves', async () => {
    vi.mocked(setNightHour).mockResolvedValue(snapshot(undefined, { night_hour: 4 }));
    draw();
    const select = await screen.findByLabelText('Nightly scans start at') as HTMLSelectElement;
    expect(select.options).toHaveLength(24);
    expect([select.options[0].text, select.options[23].text]).toEqual(['00:00', '23:00']);
    fireEvent.change(select, { target: { value: '4' } });
    await waitFor(() => expect(setNightHour).toHaveBeenCalledWith(4));
    expect(screen.getByText('Server time (UTC)')).toBeTruthy();
  });

  it('stalled poller shows the stuck banner above the cards', async () => {
    vi.mocked(getLibraryAutomation).mockResolvedValue(snapshot(undefined, { poller: { heartbeat_at: null, stalled: true } }));
    draw();
    expect(await screen.findByText("Folder watching is stuck on a folder that isn't answering. Restart Lumina if it stays stuck.")).toBeTruthy();
  });

  it('shows a load error with Try again', async () => {
    vi.mocked(getLibraryAutomation).mockRejectedValueOnce({}).mockResolvedValue(snapshot());
    draw();
    expect((await screen.findByRole('alert')).textContent).toContain('Lumina could not load scan settings.');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('heading', { name: 'TV' })).toBeTruthy();
  });

  it('polls every 5 s while a root is scanning, every 30 s otherwise, and stops on unmount', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(getLibraryAutomation).mockResolvedValue(snapshot([root({ state: 'scanning' })]));
    const first = draw();
    await screen.findByRole('heading', { name: 'TV' });
    await act(() => vi.advanceTimersByTimeAsync(4_000));
    expect(getLibraryAutomation).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(1_500));
    expect(getLibraryAutomation).toHaveBeenCalledTimes(2);
    first.unmount();
    vi.mocked(getLibraryAutomation).mockClear();
    vi.mocked(getLibraryAutomation).mockResolvedValue(snapshot([root()]));
    const second = draw();
    await screen.findByRole('heading', { name: 'TV' });
    await act(() => vi.advanceTimersByTimeAsync(29_000));
    expect(getLibraryAutomation).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(1_500));
    expect(getLibraryAutomation).toHaveBeenCalledTimes(2);
    second.unmount();
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(getLibraryAutomation).toHaveBeenCalledTimes(2);
  });

  it('has no ARIA, name, label or heading-order violations', async () => {
    vi.mocked(getLibraryAutomation).mockResolvedValue(snapshot([root({ state: 'scanning' }), root({ root_id: 'r2', label: 'Movies', watch: true, schedule: 'nightly' })]));
    const { container } = draw();
    await screen.findByRole('heading', { name: 'TV' });
    const results = await axe.run(container, { runOnly: ['aria-allowed-attr', 'aria-required-attr', 'button-name', 'label', 'list', 'heading-order'] });
    expect(results.violations).toEqual([]);
  });
});
