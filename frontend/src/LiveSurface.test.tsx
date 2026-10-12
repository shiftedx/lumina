import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LiveSurface } from './features/live/LiveSurface';
import { resetImageLoader } from './features/gallery/imageLoader';
import { endedEntry, FIXED_NOW, liveEntry, liveSnapshot, upcomingEntry } from './test/remoteFixtures';
import type { LiveSnapshot, SourceAutomation } from './types';

const api = vi.hoisted(() => ({ listLiveRecordings: vi.fn(), createLiveRecording: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));

beforeEach(() => {
  vi.clearAllMocks();
  resetImageLoader();
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(FIXED_NOW);
  api.listLiveRecordings.mockResolvedValue({ items: [], next_cursor: null });
});
afterEach(() => vi.useRealTimers());

function live(snapshot: LiveSnapshot | null, props: Record<string, unknown> = {}) {
  const onOpen = vi.fn();
  const onRetry = vi.fn();
  const view = render(<LiveSurface error={null} isQueueing={() => false} library={[]} onOpen={onOpen} onQueue={vi.fn()} onRetry={onRetry} snapshot={snapshot} {...props} />);
  const again = (next: LiveSnapshot | null, extra: Record<string, unknown> = {}) => view.rerender(<LiveSurface error={null} isQueueing={() => false} library={[]} onOpen={onOpen} onQueue={vi.fn()} onRetry={onRetry} snapshot={next} {...props} {...extra} />);
  return { onOpen, onRetry, again, ...view };
}

describe('Live surface', () => {
  it('shows the masthead with its kicker and the Recordings link', () => {
    live(liveSnapshot({ hero: [liveEntry('f1')] }));
    expect(screen.getByRole('heading', { level: 1, name: 'Live' })).toBeTruthy();
    expect(screen.getByText(/^6 live now · 1 from your follows · Checked /)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Recordings →' }).getAttribute('href')).toBe('/library/recordings');
  });

  it('leads with a followed stream and rails the other follows first', () => {
    const { container } = live(liveSnapshot({ hero: [liveEntry('f1', { view_count: 5, uploader: 'Small' }), liveEntry('f2', { view_count: 50, uploader: 'Big' })] }));
    const hero = container.querySelector('.g-live-hero') as HTMLElement;
    expect(within(hero).getByRole('heading', { name: 'Big' })).toBeTruthy();
    expect(within(hero).queryByText('Most watched right now')).toBeNull();
    const rails = [...container.querySelectorAll('section.g-rail h2')].map((heading) => heading.textContent);
    expect(rails[0]).toBe('Also live from your follows');
  });

  it('falls back to the most-watched stream overall, and omits the hero when nothing is live', () => {
    const fallback = live(liveSnapshot());
    expect(within(fallback.container.querySelector('.g-live-hero') as HTMLElement).getByText('Most watched right now')).toBeTruthy();
    fallback.unmount();
    const none = live(liveSnapshot({ items: [endedEntry('e1')] }));
    expect(none.container.querySelector('.g-live-hero')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Recently ended' })).toBeTruthy();
  });

  it('puts the day-grouped schedule above the category rails and expands past 8 rows', async () => {
    const at = (hour: number) => new Date(2026, 8, 29, hour, 0).toISOString();
    const upcoming = Array.from({ length: 10 }, (_, index) => upcomingEntry(`u${index}`, index < 9 ? at(21 + (index % 3)) : null, { title: `Premiere ${index}` }));
    const { container } = live(liveSnapshot({ items: [...liveSnapshot().items, ...upcoming] }));
    const headings = [...container.querySelectorAll('h2')].map((heading) => heading.textContent);
    expect(headings.indexOf('Upcoming')).toBeLessThan(headings.indexOf('Gaming'));
    expect(container.querySelectorAll('.g-schedule-row')).toHaveLength(8);
    expect(screen.getByText('Later today')).toBeTruthy();
    vi.useRealTimers();
    await userEvent.click(screen.getByRole('button', { name: 'Show all 10' }));
    expect(container.querySelectorAll('.g-schedule-row')).toHaveLength(10);
    expect(screen.getByText('Time not announced')).toBeTruthy();
  });

  it('says a failed provider could not be checked, never that nobody is live', async () => {
    const { container } = live(liveSnapshot({ items: [liveEntry('y1')], followed_unavailable: [{ source: 'kick', checked_at: '2026-09-29T20:39:00' }] }));
    expect(container.querySelector('.g-notices[role="status"]')?.textContent ?? '').toMatch(/Live status for followed Kick channels is unavailable · last tried/);
    vi.useRealTimers();
    await userEvent.click(within(screen.getByRole('group', { name: 'Provider' })).getByRole('button', { name: 'Kick' }));
    expect(screen.getByText('Kick could not be checked')).toBeTruthy();
    expect(screen.queryByText(/Nobody is live/)).toBeNull();
  });

  it('has a serif empty state, a pause state with Try again, and a first-load frame', async () => {
    const empty = live(liveSnapshot({ items: [], hero: [], categories: [], state: 'empty' }));
    expect(screen.getByText('Nobody is live right now.')).toBeTruthy();
    expect(screen.getByText('New streams appear as Lumina checks again, every few minutes.')).toBeTruthy();
    empty.unmount();
    const failed = live(liveSnapshot({ items: [], categories: [], state: 'failed', error: 'Discovery is resting.' }));
    expect(screen.getByText('Live is taking a pause.')).toBeTruthy();
    expect(screen.getByText('Discovery is resting.')).toBeTruthy();
    vi.useRealTimers();
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(failed.onRetry).toHaveBeenCalledTimes(1);
    failed.unmount();
    live(null);
    expect(screen.getByRole('status', { name: 'Loading live streams' })).toBeTruthy();
  });

  it('keeps the focused card through a refresh and shows it ENDED once it leaves', () => {
    const first = liveSnapshot();
    const { container, again } = live(first);
    const gaming = container.querySelector('section.g-rail[data-rail-key="gaming"]') as HTMLElement;
    const g2 = within(gaming).getByRole('button', { name: /^Night market walk, Chan, live, 900/ });
    act(() => g2.focus());
    act(() => again(liveSnapshot({ items: first.items.filter((item) => item.id !== 'g2').concat(liveEntry('g9', { view_count: 1 })) })));
    expect(document.activeElement).toBe(g2);
    expect(g2.closest('.g-remote-card')?.querySelector('.g-live')?.textContent).toBe('ENDED');
    const keys = [...gaming.querySelectorAll('[data-remote-key]')].map((card) => card.getAttribute('data-remote-key'));
    expect(keys).toEqual(['https://www.youtube.com/watch?v=g2', 'https://www.twitch.tv/g3', 'https://www.youtube.com/watch?v=g9']);
    act(() => (screen.getByRole('heading', { level: 1, name: 'Live' }) as HTMLElement).focus());
    expect(gaming.querySelector('[data-remote-key="https://www.youtube.com/watch?v=g2"]')).toBeNull();
  });

  it('records from the live edge on request, shows the status, and keeps the boundary copy as a description', async () => {
    vi.useRealTimers();
    api.createLiveRecording.mockResolvedValue({ id: 'r1', source_url: 'https://www.youtube.com/watch?v=g2', status: 'live', media: { status: 'recording' }, chat: { status: 'capturing' } });
    const { container } = live(liveSnapshot());
    const card = container.querySelector('[data-remote-key="https://www.youtube.com/watch?v=g2"]') as HTMLElement;
    expect(api.createLiveRecording).not.toHaveBeenCalled();
    const record = within(card).getByRole('button', { name: 'Record' });
    expect(record.getAttribute('title')).toBe('Recording starts from the moment Lumina connects. Nothing earlier is imported. Chat is saved alongside where the provider offers it.');
    expect(document.getElementById(card.querySelector('button.g-still')?.getAttribute('aria-describedby') ?? '')?.textContent).toMatch(/^Recording starts/);
    await userEvent.click(record);
    expect(api.createLiveRecording).toHaveBeenCalledWith({ source_url: 'https://www.youtube.com/watch?v=g2', start_intent: 'live_edge', fallback_policy: 'allow_live_edge' });
    expect(await within(card).findByText('Recording')).toBeTruthy();
    expect(api.listLiveRecordings).toHaveBeenCalledTimes(1);
  });

  it('shows a failed recording inline as an alert', async () => {
    vi.useRealTimers();
    api.createLiveRecording.mockRejectedValue(new Error('This stream cannot be recorded right now.'));
    const { container } = live(liveSnapshot());
    await userEvent.click(within(container.querySelector('[data-remote-key="https://www.youtube.com/watch?v=g2"]') as HTMLElement).getByRole('button', { name: 'Record' }));
    expect((await screen.findByRole('alert')).textContent).toBe('This stream cannot be recorded right now.');
  });

  it('links the hero to its channel page and its follow settings', () => {
    const follow = { id: 'follow-1', source_url: 'https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv', source_type: 'channel' } as SourceAutomation;
    live(liveSnapshot({ hero: [liveEntry('f1')] }), { channels: [follow] });
    expect(screen.getByRole('link', { name: 'Open channel' }).getAttribute('href')).toBe('/channel/youtube/UCabcdefghijklmnopqrstuv');
    expect(screen.getByRole('link', { name: 'Follow settings' }).getAttribute('href')).toBe('/subscriptions/follow-1');
  });

  it('opens the follow settings in place when the app supplies them', async () => {
    vi.useRealTimers();
    const follow = { id: 'follow-1', label: 'Harbor', source_url: 'https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv', source_type: 'channel' } as SourceAutomation;
    const settings = { commands: {} as never, onChange: vi.fn(), onRecover: vi.fn(), onRemoved: vi.fn() };
    live(liveSnapshot({ hero: [liveEntry('f1')] }), { channels: [follow], settings });
    await userEvent.click(screen.getByRole('button', { name: 'Follow settings' }));
    expect(document.querySelector('dialog')).toBeTruthy();
  });

  describe('in-place follow settings stay on the follow that opened them', () => {
    const idA = 'UCaaaaaaaaaaaaaaaaaaaaaa';
    const idB = 'UCbbbbbbbbbbbbbbbbbbbbbb';
    const followA = { id: 'follow-a', label: 'Alpha', source_url: `https://www.youtube.com/channel/${idA}`, source_type: 'channel' } as SourceAutomation;
    const followB = { id: 'follow-b', label: 'Bravo', source_url: `https://www.youtube.com/channel/${idB}`, source_type: 'channel' } as SourceAutomation;
    const settings = { commands: {} as never, onChange: vi.fn(), onRecover: vi.fn(), onRemoved: vi.fn() };
    const stream = (id: string, channel: string, viewers: number) => liveEntry(id, { uploader_id: channel, uploader_url: `https://www.youtube.com/channel/${channel}`, view_count: viewers });
    const dialogText = () => document.querySelector('dialog')?.textContent ?? '';

    it('keeps the first follow when a second followed stream takes over the hero', async () => {
      vi.useRealTimers();
      const { again } = live(liveSnapshot({ hero: [stream('a1', idA, 900), stream('b1', idB, 100)] }), { channels: [followA, followB], settings });
      await userEvent.click(screen.getByRole('button', { name: 'Follow settings' }));
      const before = dialogText();
      again(liveSnapshot({ hero: [stream('a1', idA, 900), stream('b1', idB, 5000)] }));
      expect(document.querySelector('dialog')).toBeTruthy();
      expect(dialogText()).toBe(before);
      expect(screen.getByRole('button', { name: 'Follow settings' })).toBeTruthy();
    });

    it('closes when the follow disappears and does not reopen when it returns', async () => {
      vi.useRealTimers();
      const { again } = live(liveSnapshot({ hero: [stream('a1', idA, 900)] }), { channels: [followA], settings });
      await userEvent.click(screen.getByRole('button', { name: 'Follow settings' }));
      expect(document.querySelector('dialog')).toBeTruthy();
      again(liveSnapshot({ hero: [] }), { channels: [] });
      expect(document.querySelector('dialog')).toBeNull();
      again(liveSnapshot({ hero: [stream('a1', idA, 900)] }), { channels: [followA] });
      expect(document.querySelector('dialog')).toBeNull();
    });
  });

  it('reaches the chips, Recordings and See all with arrow keys, and Escape leaves the wall', async () => {
    const items = Array.from({ length: 30 }, (_, index) => liveEntry(`x${index}`, { webpage_url: `https://www.youtube.com/watch?v=x${index}` }));
    const onRailChange = vi.fn();
    const { container, again } = live(liveSnapshot({ items }), { onRailChange, rail: null });
    for (const control of [screen.getByRole('link', { name: 'Recordings →' }), within(container.querySelector('section.g-rail[data-rail-key="gaming"]') as HTMLElement).getByRole('button', { name: 'See all' }), ...container.querySelectorAll('.g-chip')]) {
      expect((control as HTMLElement).hasAttribute('data-focus-item')).toBe(true);
    }
    again(liveSnapshot({ items }), { onRailChange, rail: 'gaming' });
    const back = vi.spyOn(window.history, 'back').mockImplementation(() => {});
    fireEvent.keyDown(container.querySelector('.g-rail-wall button') as HTMLElement, { key: 'Escape' });
    fireEvent.keyDown(container.querySelector('.g-rail-wall button') as HTMLElement, { key: 'Backspace' });
    expect(back).toHaveBeenCalledTimes(2);
    back.mockRestore();
  });

  it('turns See all into a wall of one rail through the rail prop', async () => {
    const items = Array.from({ length: 30 }, (_, index) => liveEntry(`x${index}`, { webpage_url: `https://www.youtube.com/watch?v=x${index}` }));
    const onRailChange = vi.fn();
    const { container, again } = live(liveSnapshot({ items }), { onRailChange, rail: null });
    vi.useRealTimers();
    await userEvent.click(within(container.querySelector('section.g-rail[data-rail-key="gaming"]') as HTMLElement).getByRole('button', { name: 'See all' }));
    expect(onRailChange).toHaveBeenCalledWith('gaming');
    again(liveSnapshot({ items }), { onRailChange, rail: 'gaming' });
    expect(container.querySelector('.g-live-hero')).toBeNull();
    expect(container.querySelectorAll('.g-rail-wall .g-remote-card').length).toBe(29); // 30 in Gaming, less the hero
  });

  it('never renders an image from outside Lumina', () => {
    const { container } = live(liveSnapshot({ items: [liveEntry('p', { artwork_url: 'https://i.ytimg.com/vi/p/hqdefault_live.jpg' })] }));
    for (const image of container.querySelectorAll('img')) expect(image.getAttribute('src') ?? '').toMatch(/^(\/api\/|data:)/);
  });

  it('keeps arrow keys on cards: Right stays in the rail and Down reaches the next one', () => {
    const { container } = live(liveSnapshot());
    const first = container.querySelector('section.g-rail[data-rail-key="gaming"] button.g-still') as HTMLElement;
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowRight' });
    expect((document.activeElement as HTMLElement).closest('section.g-rail')?.getAttribute('data-rail-key')).toBe('gaming');
  });
});
