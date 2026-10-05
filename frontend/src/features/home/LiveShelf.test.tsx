import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { liveShelfEntries } from '../../liveRails';
import type { LiveSnapshot, YouTubeSearchResult } from '../../types';
import { StreamingProvidersProvider } from '../streaming/providers';
import { forgetHomeCache, rememberShelf } from './homeCache';
import { LIVE_POLL_MS, LiveShelf, recoverFocusIndex } from './LiveShelf';

const api = vi.hoisted(() => ({ getLiveDiscovery: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));

const stream = (id: string, patch: Partial<YouTubeSearchResult> = {}): YouTubeSearchResult => ({
  id, title: `Live ${id}`, uploader: `Channel ${id}`, webpage_url: `https://www.twitch.tv/${id}`, artwork_url: `/api/artwork/remote/${id}`,
  view_count: 12_400, source: 'twitch', source_label: 'Twitch', kind: 'live', ...patch,
});
const snapshot = (patch: Partial<LiveSnapshot> = {}): LiveSnapshot => ({ items: [], categories: [], state: 'ready', refreshing: false, stale: false, twitch_available: true, hero: [], ...patch });
const flush = () => act(async () => { await Promise.resolve(); });
const cards = () => [...document.querySelectorAll('[data-shelf-section="live"] .h-card [data-focus-item]')] as HTMLElement[];

function renderShelf() {
  const onStatus = vi.fn();
  const onOpenRemote = vi.fn();
  const view = render(<LiveShelf onOpenRemote={onOpenRemote} onSeeAll={vi.fn()} onStatus={onStatus} userId="member-1" />);
  return { ...view, onStatus, onOpenRemote };
}

beforeEach(() => { api.getLiveDiscovery.mockReset(); forgetHomeCache(); });
afterEach(() => { vi.useRealTimers(); });

describe('LiveShelf provider choices', () => {
  it('hides Twitch and Kick cards when both are off, keeping YouTube', async () => {
    api.getLiveDiscovery.mockResolvedValue(snapshot({ items: [stream('t'), stream('k', { source: 'kick' }), stream('y', { source: 'youtube', source_label: 'YouTube', webpage_url: 'https://www.youtube.com/watch?v=y' })] }));
    render(<StreamingProvidersProvider enabled={[]} onChange={vi.fn()}><LiveShelf onOpenRemote={vi.fn()} onSeeAll={vi.fn()} onStatus={vi.fn()} userId="member-1" /></StreamingProvidersProvider>);
    await flush();
    expect(cards()).toHaveLength(1);
  });
});

describe('LiveShelf', () => {
  it('lists followed live first, then popular, with the LIVE badge, a provider kicker and full accessible names', async () => {
    api.getLiveDiscovery.mockResolvedValue(snapshot({ hero: [stream('mine')], items: [stream('mine'), stream('big', { source: 'youtube', source_label: 'YouTube', webpage_url: 'https://www.youtube.com/watch?v=big' })] }));
    const { onOpenRemote, onStatus } = renderShelf();
    await flush();
    const region = screen.getByRole('region', { name: 'Live now' });
    const watching = (12_400).toLocaleString();
    const mine = within(region).getByRole('button', { name: `Live: Live mine, Channel mine, ${watching} watching on Twitch, a channel you follow` });
    within(region).getByRole('button', { name: `Live: Live big, Channel big, ${watching} watching on YouTube` });
    expect(cards()).toHaveLength(2);
    expect(mine.querySelector('.g-still-caption')?.textContent).toBe('TWITCHLive mineFollowing · Channel mine · 12.4K watching');
    expect(mine.querySelector('.g-live.is-live.on-art')?.getAttribute('aria-hidden')).toBe('true');
    expect(within(region).getByRole('button', { name: 'See all' })).toBeTruthy();
    fireEvent.click(mine);
    expect(onOpenRemote).toHaveBeenCalledWith(expect.objectContaining({ id: 'mine' }));
    expect(onStatus).toHaveBeenLastCalledWith('items');
  });

  it('polls every 60 s, and a refresh keeps each card, and focus, by URL', async () => {
    vi.useFakeTimers();
    api.getLiveDiscovery.mockResolvedValue(snapshot({ items: [stream('a'), stream('b')] }));
    renderShelf();
    await flush();
    const b = cards()[1];
    b.focus();
    api.getLiveDiscovery.mockResolvedValue(snapshot({ items: [stream('c'), stream('a'), stream('b')] }));
    await act(() => vi.advanceTimersByTimeAsync(LIVE_POLL_MS - 1));
    expect(api.getLiveDiscovery).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(api.getLiveDiscovery).toHaveBeenCalledTimes(2);
    expect(cards()).toHaveLength(3);
    expect(cards()[2]).toBe(b);
    expect(document.activeElement).toBe(b);
  });

  it('when the focused stream ends, focus moves to the card now at its index, else the last card', async () => {
    vi.useFakeTimers();
    api.getLiveDiscovery.mockResolvedValue(snapshot({ items: [stream('a'), stream('b'), stream('c')] }));
    renderShelf();
    await flush();
    cards()[1].focus();
    api.getLiveDiscovery.mockResolvedValue(snapshot({ items: [stream('a'), stream('c')] }));
    await act(() => vi.advanceTimersByTimeAsync(LIVE_POLL_MS));
    expect(document.activeElement?.getAttribute('aria-label')).toMatch(/^Live: Live c,/);
    api.getLiveDiscovery.mockResolvedValue(snapshot({ items: [stream('a')] }));
    await act(() => vi.advanceTimersByTimeAsync(LIVE_POLL_MS));
    expect(document.activeElement?.getAttribute('aria-label')).toMatch(/^Live: Live a,/);
  });

  it('recovers focus by the stream\'s stable key across a re-rank, not by its old index (E-I1)', () => {
    // Popularity re-ranks a refresh from [a, b] to [b, a]: a moves from index 0 to index 1. Recovering by the old
    // index alone would land on b, a different stream, instead of following a to its new position.
    const reranked = liveShelfEntries(snapshot({ items: [stream('b'), stream('a')] }));
    expect(recoverFocusIndex('https://www.twitch.tv/a', 0, reranked)).toBe(1);
    // b has left the list entirely: recovery falls back to the old index (the nearest remaining card).
    const trimmed = liveShelfEntries(snapshot({ items: [stream('a')] }));
    expect(recoverFocusIndex('https://www.twitch.tv/b', 1, trimmed)).toBe(1);
  });

  it('notices are plain text under the heading, and stale data says so', async () => {
    api.getLiveDiscovery.mockResolvedValue(snapshot({ items: [stream('a')], twitch_available: false, stale: true, state: 'stale' }));
    renderShelf();
    await flush();
    const region = screen.getByRole('region', { name: 'Live now' });
    const notice = within(region).getByText('Twitch streams are temporarily unavailable.');
    expect(notice.closest('[aria-live], [role="status"], [role="alert"]')).toBeNull();
    expect(within(region).getByText('Showing the last-known live streams.')).toBeTruthy();
  });

  it('shows frames while loading, hides itself when nothing is live, and reports each state', async () => {
    let resolve: (value: LiveSnapshot) => void = () => undefined;
    api.getLiveDiscovery.mockReturnValue(new Promise<LiveSnapshot>((done) => { resolve = done; }));
    const { container, onStatus } = renderShelf();
    expect(screen.getByRole('region', { name: 'Live now' }).getAttribute('aria-busy')).toBe('true');
    expect(onStatus).toHaveBeenLastCalledWith('loading');
    await act(async () => { resolve(snapshot({ state: 'empty' })); });
    expect(container.innerHTML).toBe('');
    expect(onStatus).toHaveBeenLastCalledWith('empty');
  });

  it('an error with Try again only when there is no data; a later failure keeps the data silently', async () => {
    vi.useFakeTimers();
    api.getLiveDiscovery.mockRejectedValueOnce(new Error('429'));
    const { onStatus } = renderShelf();
    await flush();
    const region = screen.getByRole('region', { name: 'Live now' });
    expect(region.querySelector('.h-shelf-error')?.textContent).toContain('Live streams are unavailable right now.');
    expect(onStatus).toHaveBeenLastCalledWith('failed');
    api.getLiveDiscovery.mockResolvedValue(snapshot({ items: [stream('a')] }));
    fireEvent.click(within(region).getByRole('button', { name: 'Try again' }));
    await flush();
    expect(api.getLiveDiscovery).toHaveBeenCalledTimes(2);
    expect(cards()).toHaveLength(1);
    api.getLiveDiscovery.mockRejectedValue(new Error('offline'));
    await act(() => vi.advanceTimersByTimeAsync(LIVE_POLL_MS));
    expect(cards()).toHaveLength(1);
    expect(document.querySelector('[data-shelf-section="live"] .h-shelf-error')).toBeNull();
  });

  it('paints the last snapshot from the session cache on a return visit, before the refetch', () => {
    rememberShelf('member-1', 'live', snapshot({ items: [stream('cached')] }));
    api.getLiveDiscovery.mockReturnValue(new Promise(() => undefined));
    renderShelf();
    expect(cards().map((card) => card.getAttribute('aria-label')?.split(',')[0])).toEqual(['Live: Live cached']);
  });
});
