import axe from 'axe-core';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { LiveSurface } from './features/live/LiveSurface';
import type { LiveSnapshot, MediaSourceCapabilities, YouTubeSearchResult } from './types';

const api = vi.hoisted(() => ({
  listLiveRecordings: vi.fn(),
  createLiveRecording: vi.fn(),
}));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));

beforeEach(() => {
  vi.clearAllMocks();
  api.listLiveRecordings.mockResolvedValue({ items: [], next_cursor: null });
  api.createLiveRecording.mockReset();
});

const caps = (provider: MediaSourceCapabilities['provider'], lifecycle: MediaSourceCapabilities['lifecycle'], extra: Partial<MediaSourceCapabilities> = {}): MediaSourceCapabilities => ({
  provider, lifecycle, can_play: lifecycle !== 'upcoming', can_acquire: false, chat: { live: 'unavailable', replay: 'unavailable' }, ...extra,
});
const entry = (id: string, source: 'youtube' | 'twitch' | 'kick', capabilities: MediaSourceCapabilities, extra: Partial<YouTubeSearchResult> = {}) => ({
  id, title: `${id} title`, uploader: `${id} channel`, source, webpage_url: `https://example.test/${id}`, category_keys: ['gaming'], capabilities, ...extra,
});

function snapshot(overrides: Partial<LiveSnapshot> = {}): LiveSnapshot {
  return {
    items: [
      entry('yt-live', 'youtube', caps('youtube', 'live', { can_record: true }), { view_count: 1200 }),
      entry('tw-live', 'twitch', caps('twitch', 'live', { can_record: true }), { view_count: 5400 }),
      entry('kick-live', 'kick', caps('kick', 'live', { can_record: true })),
    ],
    hero: [
      entry('yt-upcoming', 'youtube', caps('youtube', 'upcoming', { can_schedule: true })),
      entry('tw-ended', 'twitch', caps('twitch', 'completed_live')),
    ],
    categories: [{ key: 'gaming', label: 'Gaming', state: 'ready' }],
    state: 'ready', refreshing: false, stale: false, twitch_available: true,
    refreshed_at: '2026-07-20T12:00:00Z',
    ...overrides,
  } as LiveSnapshot;
}

const renderLive = (snap: LiveSnapshot | null, onOpen = vi.fn()) => render(
  <LiveSurface error={null} isQueueing={() => false} library={[]} onOpen={onOpen} onQueue={vi.fn()} snapshot={snap} />,
);
// The most-watched stream is the hero (its Watch button names it) and upcoming streams are schedule rows.
const cardFor = (title: string) => screen.getByRole('button', { name: new RegExp(`^(Watch )?${title}`) }).closest('.g-remote-card, .g-live-hero, .g-schedule-row') as HTMLElement;

describe('v1 Live UI', () => {
  it('test_live_lifecycle_actions', async () => {
    api.createLiveRecording.mockResolvedValue({ id: 'rec-1', source_url: 'https://example.test/yt-live', status: 'live', media: { status: 'recording' }, chat: { status: 'capturing' } });
    renderLive(snapshot());

    expect(screen.getByRole('heading', { name: 'Upcoming' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Recently ended' })).toBeTruthy();
    // Only what each source's capabilities allow.
    expect(within(cardFor('yt-live title')).getByRole('button', { name: 'Record' })).toBeTruthy();
    expect(within(cardFor('tw-live title')).getByRole('button', { name: 'Record' })).toBeTruthy();
    expect(within(cardFor('kick-live title')).getByRole('button', { name: 'Record' })).toBeTruthy();
    expect(within(cardFor('kick-live title')).queryByRole('button', { name: 'Schedule' })).toBeNull();
    expect(within(cardFor('yt-upcoming title')).getByRole('button', { name: 'Schedule' })).toBeTruthy();
    expect(within(cardFor('yt-upcoming title')).queryByRole('button', { name: 'Record' })).toBeNull();
    expect(within(cardFor('tw-ended title')).queryByRole('button', { name: /Record|Schedule/ })).toBeNull();
    // Viewer counts only where real, and phrased as concurrent viewers for live.
    expect(within(cardFor('tw-live title')).getByText(/5\.4K watching/)).toBeTruthy();
    expect(within(cardFor('kick-live title')).queryByText(/watching/)).toBeNull();
    expect(within(cardFor('yt-live title')).getByRole('button', { name: 'Record' }).getAttribute('title')).toMatch(/^Recording starts from the moment Lumina connects/);

    // Nothing records on entry; an explicit click starts from the live edge.
    expect(api.createLiveRecording).not.toHaveBeenCalled();
    await userEvent.click(within(cardFor('yt-live title')).getByRole('button', { name: 'Record' }));
    expect(api.createLiveRecording).toHaveBeenCalledWith({ source_url: 'https://example.test/yt-live', start_intent: 'live_edge', fallback_policy: 'allow_live_edge' });
    expect(await within(cardFor('yt-live title')).findByText('Recording')).toBeTruthy();
  });

  it('shows an existing durable recording instead of offering a second one', async () => {
    api.listLiveRecordings.mockResolvedValue({ items: [{ id: 'rec-2', source_url: 'https://example.test/tw-live', status: 'waiting', media: { status: 'pending' }, chat: { status: 'pending' } }], next_cursor: null });
    renderLive(snapshot());
    expect(await within(cardFor('tw-live title')).findByText('Waiting for the broadcast')).toBeTruthy();
    expect(within(cardFor('tw-live title')).queryByRole('button', { name: 'Record' })).toBeNull();
  });

  it('test_live_refresh_keeps_player', async () => {
    const onOpen = vi.fn();
    const { rerender } = renderLive(snapshot(), onOpen);
    await userEvent.click(screen.getByRole('button', { name: 'Twitch' }));
    expect(screen.queryByText('yt-live title')).toBeNull();

    rerender(<LiveSurface error={null} isQueueing={() => false} library={[]} onOpen={onOpen} onQueue={vi.fn()} snapshot={snapshot({ refreshing: true })} />);
    expect(screen.getByRole('button', { name: 'Twitch' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByText('tw-live title')).toBeTruthy();
    expect(onOpen).not.toHaveBeenCalled();
    expect(api.listLiveRecordings).toHaveBeenCalledTimes(1);
  });

  it('test_live_unavailable_source', async () => {
    renderLive(snapshot({ items: [snapshot().items[0]], hero: [], followed_unavailable: [{ source: 'kick', checked_at: '2026-07-20T12:00:00Z' }] }));
    await userEvent.click(screen.getByRole('button', { name: 'Kick' }));
    expect(screen.getByText('Kick could not be checked')).toBeTruthy();
    expect(screen.queryByText(/No Kick streams live/)).toBeNull();
    expect(screen.getByText(/Live status for followed Kick channels is unavailable · last tried/)).toBeTruthy();

    cleanup();
    renderLive(snapshot({ items: [snapshot().items[0]], hero: [], twitch_available: true, followed_unavailable: [] }));
    // With nothing to filter by, a checked provider with no streams reads as "no streams", never an outage.
    expect(screen.queryByRole('group', { name: 'Provider' })).toBeNull();
    cleanup();
    renderLive(snapshot({ items: [snapshot().items[0], snapshot().items[1]], hero: [] }));
    await userEvent.click(screen.getByRole('button', { name: 'Twitch' }));
    expect(screen.queryByText(/could not be checked/)).toBeNull();
  });

  it('test_live_accessible_themes', async () => {
    const { container } = renderLive(snapshot({ twitch_available: false }));
    await waitFor(() => expect(api.listLiveRecordings).toHaveBeenCalled());
    const results = await axe.run(container, { runOnly: ['aria-prohibited-attr', 'button-name', 'aria-allowed-attr', 'aria-required-attr'] });
    expect(results.violations).toEqual([]);
    expect(screen.getByRole('group', { name: 'Provider' })).toBeTruthy();
  });
});
