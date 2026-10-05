import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ActivityHistoryRow, ActivitySession, AdminActivity } from '../../types';

const api = vi.hoisted(() => ({ getAdminActivity: vi.fn(), stopActivitySession: vi.fn(), getActivityHistory: vi.fn(), listUsers: vi.fn() }));
vi.mock('../../api', () => api);
const { ActivityBackground, ActivityHistory, ActivityNowPlaying, ActivityProvider, ActivityServerStats } = await import('./AdminActivity');

const session = (over: Partial<ActivitySession> = {}): ActivitySession => ({
  id: 's1', source: 'library', user: { id: 'u1', name: 'Maya' }, title: 'Dune', subtitle: null, item_id: 'i1', artwork_url: null,
  client: { kind: 'app', name: 'Infuse', device: 'Apple TV' }, method: 'transcode',
  video: { from: 'HEVC', to: 'H.264', height: 1080, tonemap: true }, audio: { from: 'EAC3', to: 'AAC' }, hardware: 'qsv', speed: 3.2, throttled: true,
  position_seconds: 600, duration_seconds: 7200, started_at: new Date(Date.now() - 12 * 60_000).toISOString(), last_seen_at: new Date().toISOString(), stoppable: true, ...over,
});
const snapshot = (over: Partial<AdminActivity> = {}): AdminActivity => ({
  generated_at: new Date().toISOString(), sessions: [session()], downloads: [], recordings: [],
  server: { cpu_percent: 41, memory: { used_bytes: 4 * 1024 ** 3, total_bytes: 16 * 1024 ** 3 }, load_average: [1, 0.5, 0.25], uptime_seconds: 90_000, transcoder_cpu_percent: 30, ffmpeg_processes: 2,
    hardware: { mode: 'auto', active: 'qsv', disabled: false, failures: 0, fallbacks: 1 } }, ...over,
});
const row = (id: string, over: Partial<ActivityHistoryRow> = {}): ActivityHistoryRow => ({
  id, source: 'library', user: { id: 'u1', name: 'Maya' }, title: `Film ${id}`, subtitle: null, item_id: null, client: { kind: 'web', name: 'Lumina web', device: null }, method: 'direct', hardware: null, video: null,
  started_at: '2026-10-02T10:00:00Z', ended_at: '2026-10-02T11:00:00Z', watched_seconds: 3600, stopped_by_admin: false, ...over,
});
const ui = (children: React.ReactNode) => <ActivityProvider>{children}</ActivityProvider>;

beforeEach(() => { vi.clearAllMocks(); api.listUsers.mockResolvedValue([{ id: 'u1', username: 'maya', display_name: 'Maya' }, { id: 'u2', username: 'sam', display_name: 'Sam' }]); });
afterEach(() => vi.useRealTimers());

describe('Activity: now playing', () => {
  it('shows method, hardware, conversion and speed for a session', async () => {
    api.getAdminActivity.mockResolvedValue(snapshot());
    render(ui(<ActivityNowPlaying />));
    expect(await screen.findByText('Dune')).toBeTruthy();
    for (const text of ['Transcode', 'Intel QSV', '3.2×', 'Throttled']) expect(screen.getByText(text), text).toBeTruthy();
    for (const text of [/Maya · Infuse · Apple TV/, /playing for 12 min/]) expect(screen.getByText(text), String(text)).toBeTruthy();
    expect(screen.getByText('HEVC → H.264 · 1080p · HDR tone-mapped · Audio EAC3 → AAC')).toBeTruthy();
  });

  it('stops after confirming, then refreshes', async () => {
    api.getAdminActivity.mockResolvedValueOnce(snapshot()).mockResolvedValue(snapshot({ sessions: [] }));
    api.stopActivitySession.mockResolvedValue(undefined);
    render(ui(<ActivityNowPlaying />));
    fireEvent.click(await screen.findByRole('button', { name: 'Stop Dune' }));
    expect(screen.getByText('Stop this stream? Maya will see that the server owner stopped it.')).toBeTruthy();
    expect(api.stopActivitySession).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Stop stream' }));
    await waitFor(() => expect(api.stopActivitySession).toHaveBeenCalledWith('s1'));
    expect(await screen.findByText('Nothing is playing right now.')).toBeTruthy();
    expect(api.getAdminActivity).toHaveBeenCalledTimes(2);
  });

  it('shows the empty state', async () => {
    api.getAdminActivity.mockResolvedValue(snapshot({ sessions: [] }));
    render(ui(<ActivityNowPlaying />));
    expect(await screen.findByText('Nothing is playing right now.')).toBeTruthy();
  });

  it('polls every 3 seconds', async () => {
    vi.useFakeTimers();
    api.getAdminActivity.mockResolvedValue(snapshot());
    render(ui(<ActivityNowPlaying />));
    await act(async () => { await vi.advanceTimersByTimeAsync(6100); });
    expect(api.getAdminActivity).toHaveBeenCalledTimes(3);
  });
});

describe('Activity: server and background work', () => {
  it('says what is not available instead of guessing', async () => {
    const base = snapshot();
    api.getAdminActivity.mockResolvedValue({ ...base, server: { ...base.server, cpu_percent: null, memory: null, load_average: null, uptime_seconds: null, transcoder_cpu_percent: null, hardware: { mode: 'auto', active: null, disabled: true, failures: 3, fallbacks: 4 } } });
    render(ui(<ActivityServerStats />));
    await waitFor(() => expect(screen.getAllByText('Not available on this system')).toHaveLength(5));
    expect(screen.getByText('Hardware encoding disabled after 3 failures')).toBeTruthy();
    expect(screen.getByText(/4 fallbacks/)).toBeTruthy();
  });

  it('shows figures and the active encoder', async () => {
    api.getAdminActivity.mockResolvedValue(snapshot());
    render(ui(<ActivityServerStats />));
    expect(await screen.findByText('41 %')).toBeTruthy();
    expect(screen.getByText('4.0 GB')).toBeTruthy();
    expect(screen.getByText('Intel QSV active')).toBeTruthy();
  });

  it('lists downloads and recordings', async () => {
    api.getAdminActivity.mockResolvedValue(snapshot({ downloads: [{ id: 'd', title: 'Clip', user: { id: 'u2', name: 'Sam' }, progress: 40, speed_bytes: 2048, started_at: null }], recordings: [{ id: 'r', title: 'Stream', user: { id: 'u1', name: 'Maya' }, status: 'live', started_at: null }] }));
    render(ui(<ActivityBackground />));
    expect(await screen.findByText(/Download · Sam/)).toBeTruthy();
    expect(screen.getByText('Recording · Maya · Recording live')).toBeTruthy();
    expect(screen.getByRole('progressbar', { name: 'Clip progress' }).getAttribute('aria-valuenow')).toBe('40');
  });
});

describe('Activity: history', () => {
  it('filters by member, searches after a pause, and loads more by cursor', async () => {
    api.getActivityHistory.mockImplementation(({ before }: { before?: string | null }) => Promise.resolve(before ? { items: [row('b')], next_before: null } : { items: [row('a', { stopped_by_admin: true, method: 'transcode', hardware: 'qsv' })], next_before: '2026-10-02T11:00:00Z' }));
    render(<ActivityHistory />);
    expect(await screen.findByText('Film a')).toBeTruthy();
    expect(screen.getByText('Stopped by owner')).toBeTruthy();
    expect(screen.getByText('Intel QSV')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }));
    expect(await screen.findByText('Film b')).toBeTruthy();
    expect(api.getActivityHistory).toHaveBeenLastCalledWith(expect.objectContaining({ before: '2026-10-02T11:00:00Z' }));
    expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull();

    fireEvent.change(await screen.findByLabelText('Member'), { target: { value: 'u2' } });
    await waitFor(() => expect(api.getActivityHistory).toHaveBeenLastCalledWith(expect.objectContaining({ userId: 'u2', before: null })));
    vi.useFakeTimers();
    const calls = api.getActivityHistory.mock.calls.length;
    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'dun' } });
    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'dune' } });
    expect(api.getActivityHistory).toHaveBeenCalledTimes(calls);
    await act(async () => { await vi.advanceTimersByTimeAsync(350); });
    expect(api.getActivityHistory).toHaveBeenCalledTimes(calls + 1);
    expect(api.getActivityHistory).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'dune', userId: 'u2' }));
  });

  it('shows an empty state', async () => {
    api.getActivityHistory.mockResolvedValue({ items: [], next_before: null });
    render(<ActivityHistory />);
    expect(await within(document.body).findByText('No playback history yet.')).toBeTruthy();
  });
});
