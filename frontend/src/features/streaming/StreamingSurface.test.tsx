import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { liveEntry, liveSnapshot, remoteEntry } from '../../test/remoteFixtures';
import type { PopularSnapshot } from '../../types';
import { type StreamingProvider, StreamingSurface, type StreamingSurfaceProps } from './StreamingSurface';

const api = vi.hoisted(() => ({ getLiveDiscovery: vi.fn(), listLiveRecordings: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));

const many = (count: number) => Array.from({ length: count }, (_, index) => remoteEntry(`p${index}`, { category_keys: ['gaming'] }));
const popular = { items: many(30), for_you: [remoteEntry('fy1')], categories: [{ key: 'gaming', label: 'Gaming', state: 'ready' }], state: 'ready', refreshing: false, stale: false, refreshed_at: '2026-09-29T20:30:00' } as unknown as PopularSnapshot;
const twitchLive = liveEntry('t1', { source: 'twitch', source_label: 'Twitch', title: 'Twitch only stream', webpage_url: 'https://www.twitch.tv/t1' });

function setup(patch: Partial<StreamingSurfaceProps> = {}, explore: Record<string, unknown> = {}) {
  const onNavigate = vi.fn();
  const onSearch = vi.fn();
  const props: StreamingSurfaceProps = {
    view: 'home', provider: 'youtube', providers: ['youtube'], onNavigate,
    live: { error: null, isQueueing: () => false, library: [], onOpen: vi.fn(), onQueue: vi.fn(), snapshot: liveSnapshot({ items: [...liveSnapshot().items, twitchLive] }) },
    explore: { error: null, isQueueing: () => false, library: [], loading: false, onOpen: vi.fn(), onQueue: vi.fn(), onSearch, popular, popularError: null, query: '', results: [], ...explore } as StreamingSurfaceProps['explore'],
    channels: <section aria-label="Channels screen" />,
    ...patch,
  };
  return { onNavigate, onSearch, props, ...render(<StreamingSurface {...props} />) };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getLiveDiscovery.mockResolvedValue(liveSnapshot({ items: [...liveSnapshot().items, twitchLive] }));
  api.listLiveRecordings.mockResolvedValue({ items: [], next_cursor: null });
});

describe('Streaming page', () => {
  it('has the Streaming masthead and a search box named for the provider', () => {
    setup();
    expect(screen.getByRole('heading', { level: 1, name: 'Streaming' })).toBeTruthy();
    expect(screen.getByRole('searchbox', { name: 'Search YouTube' })).toBeTruthy();
  });

  it('has no search box on Twitch or Kick, since only YouTube is searchable', () => {
    setup({ provider: 'twitch', providers: ['youtube', 'twitch', 'kick'] });
    expect(screen.queryByRole('searchbox')).toBeNull();
  });

  it('hides the provider switch when only YouTube is on, and shows exactly the enabled ones otherwise', () => {
    const view = setup();
    expect(screen.queryByRole('group', { name: 'Provider' })).toBeNull();
    view.unmount();
    setup({ providers: ['youtube', 'kick'] });
    const group = screen.getByRole('group', { name: 'Provider' });
    expect(within(group).getAllByRole('radio').map((radio) => (radio as HTMLInputElement).value)).toEqual(['youtube', 'kick']);
  });

  it('switches provider through onNavigate', async () => {
    const { onNavigate } = setup({ providers: ['youtube', 'twitch'] });
    await userEvent.click(screen.getByRole('radio', { name: 'Twitch' }));
    expect(onNavigate).toHaveBeenCalledWith({ view: 'home', provider: 'twitch' });
  });

  it('home shows Live now and Recommended first, then the category chips (one compact row), then Popular', async () => {
    const { container } = setup();
    await screen.findByRole('heading', { level: 2, name: 'Live now' });
    const order = [...container.querySelectorAll('h2')].map((heading) => heading.textContent);
    const at = (name: string) => order.findIndex((text) => text?.includes(name));
    expect(at('Live now')).toBeGreaterThanOrEqual(0);
    expect(at('Live now')).toBeLessThan(at('Recommended for you'));
    expect(at('Recommended for you')).toBeLessThan(at('Gaming'));
    const chips = screen.getByRole('navigation', { name: 'Browse by category' });
    expect(chips.compareDocumentPosition(screen.getByRole('heading', { level: 2, name: 'Live now' })) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    expect(chips.compareDocumentPosition(screen.getByRole('heading', { level: 2, name: 'Gaming' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(chips).getAllByRole('button').length).toBeGreaterThan(10);
    expect(screen.queryByRole('region', { name: 'Streaming' })).toBeNull();
    expect(container.querySelector('h1.g-masthead-title, h1')?.textContent).toBe('Streaming');
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  });

  it('keeps Twitch and Kick cards out of YouTube and out of everything when they are off', async () => {
    setup();
    await screen.findByRole('heading', { level: 2, name: 'Live now' });
    expect(screen.queryByText('Twitch only stream')).toBeNull();
  });

  it('with only YouTube on, no Twitch or Kick card renders in the home or live views', async () => {
    const kick = liveEntry('k1', { source: 'kick', title: 'Kick only stream', webpage_url: 'https://kick.com/k1' });
    const snapshot = liveSnapshot({ hero: [twitchLive, kick], items: [...liveSnapshot().items, twitchLive, kick] });
    const pop = { ...popular, items: [...many(3), remoteEntry('pt', { title: 'Popular twitch', source: 'twitch', category_keys: ['gaming'] })] } as unknown as PopularSnapshot;
    const base = setup().props.live;
    cleanup();
    const view = setup({ live: { ...base, snapshot } }, { popular: pop });
    await screen.findByRole('heading', { level: 2, name: 'Live now' });
    for (const text of ['Twitch only stream', 'Kick only stream', 'Popular twitch']) expect(screen.queryByText(text)).toBeNull();
    view.unmount();
    setup({ view: 'live', live: { ...base, snapshot } });
    for (const text of ['Twitch only stream', 'Kick only stream']) expect(screen.queryByText(text)).toBeNull();
  });

  it('view=live renders the Live screen embedded, without its own masthead', () => {
    setup({ view: 'live' });
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(screen.getByRole('link', { name: 'Recordings →' })).toBeTruthy();
  });

  it('Twitch shows only Twitch live and none of the YouTube-only rows', () => {
    setup({ provider: 'twitch', providers: ['youtube', 'twitch'] });
    expect(screen.getAllByText('Twitch only stream').length).toBeGreaterThan(0);
    expect(screen.queryByRole('navigation', { name: 'Browse by category' })).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Recommended for you' })).toBeNull();
  });

  it('view=search renders Explore results for the query, scoped to the provider', () => {
    setup({ view: 'search', query: 'harbor' }, { query: 'harbor', results: [remoteEntry('v1', { title: 'Harbor film' }), remoteEntry('tw', { title: 'Twitch clip', source: 'twitch' })] });
    expect(screen.getByRole('heading', { level: 2, name: 'Results for “harbor”' })).toBeTruthy();
    expect(screen.getByText('Harbor film')).toBeTruthy();
    expect(screen.queryByText('Twitch clip')).toBeNull();
  });

  it('submits a YouTube search through the shared search', async () => {
    const { onSearch } = setup();
    await userEvent.type(screen.getByRole('searchbox', { name: 'Search YouTube' }), 'slow trains{Enter}');
    expect(onSearch).toHaveBeenCalledWith('slow trains');
  });

  it('view=channels renders the channels element, and an off provider falls back to YouTube', () => {
    setup({ view: 'channels', provider: 'kick' as StreamingProvider });
    expect(screen.getByRole('region', { name: 'Channels screen' })).toBeTruthy();
    expect(screen.getByRole('searchbox', { name: 'Search YouTube' })).toBeTruthy();
  });

  it('Live now See all goes to the live view', async () => {
    const { onNavigate } = setup();
    // The loading placeholder shares the heading; wait for the real row's link.
    await userEvent.click(await screen.findByRole('link', { name: 'See all' }));
    expect(onNavigate).toHaveBeenCalledWith({ view: 'live', provider: 'youtube' });
  });

  it('Escape in a popular See all wall asks the address to go back; Escape in a live rail wall does too', async () => {
    const back = vi.spyOn(window.history, 'back').mockImplementation(() => undefined);
    const { onNavigate } = setup({ rail: 'popular-gaming', onNavigate: vi.fn() }, { rail: 'popular-gaming', onRailChange: vi.fn() });
    await waitFor(() => expect(document.querySelector('.g-rail-wall')).not.toBeNull());
    await userEvent.keyboard('{Escape}');
    expect(back).toHaveBeenCalledOnce();
    expect(onNavigate).not.toHaveBeenCalled();
    back.mockRestore();
  });

  it('entering a popular See all reports the rail through the Explore handler', async () => {
    const onRailChange = vi.fn();
    setup({}, { onRailChange });
    await userEvent.click(await screen.findByRole('button', { name: 'See all' }));
    expect(onRailChange).toHaveBeenCalledWith('popular-gaming');
  });
});
