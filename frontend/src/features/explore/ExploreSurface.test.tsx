import type { ComponentProps } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { recoEntry } from '../../test/recoFixtures';
import { RecoFeedbackProvider } from '../reco/recoFeedback';
import { liveEntry, liveSnapshot, remoteEntry } from '../../test/remoteFixtures';
import { stillItem } from '../../test/galleryFixtures';
import type { PopularSnapshot } from '../../types';
import { ExploreSurface } from './ExploreSurface';

const api = vi.hoisted(() => ({ getLiveDiscovery: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));

const popular = {
  items: [remoteEntry('p1', { category_keys: ['gaming'] })], categories: [{ key: 'gaming', label: 'Gaming', state: 'ready' }],
  state: 'ready', refreshing: false, stale: false, refreshed_at: '2026-09-29T20:30:00',
} as PopularSnapshot;

const exploreProps = (patch: Record<string, unknown> = {}) => ({ error: null, isQueueing: () => false, library: [], loading: false, popular, popularError: null, query: '', results: [], ...patch }) as unknown as ComponentProps<typeof ExploreSurface>;

function explore(props: Record<string, unknown> = {}) {
  const handlers = { onSearch: vi.fn(), onOpen: vi.fn(), onQueue: vi.fn(), onOpenTitle: vi.fn(), onOpenLibrary: vi.fn(), onRailChange: vi.fn() };
  const view = render(<ExploreSurface {...exploreProps(props)} {...handlers} />);
  return { ...handlers, ...view };
}

beforeEach(() => { vi.clearAllMocks(); api.getLiveDiscovery.mockResolvedValue(liveSnapshot()); });

describe('Explore', () => {
  it('leads with the masthead, the search field, Live now from one fetch, the category chips and popular rails', async () => {
    const { onSearch } = explore();
    expect(screen.getByRole('heading', { level: 1, name: 'Explore' })).toBeTruthy();
    expect(screen.getByText(/^Popular across the web · updated /)).toBeTruthy();
    const search = screen.getByRole('searchbox', { name: 'Search' }) as HTMLInputElement;
    expect(search.placeholder).toBe('Search YouTube, SoundCloud and your library');
    await userEvent.type(search, 'slow trains{Enter}');
    expect(onSearch).toHaveBeenCalledWith('slow trains');
    expect(await screen.findByRole('heading', { level: 2, name: 'Live now' })).toBeTruthy();
    expect(api.getLiveDiscovery).toHaveBeenCalledTimes(1);
    // "See all" (link to /live, onSeeAll) is StillRail's: C2 owns that assertion once the real rail lands.
    const tiles = within(screen.getByRole('navigation', { name: 'Browse by category' })).getAllByRole('button');
    expect(tiles[0].textContent).toBe('Documentaries');
    await userEvent.click(tiles[0]);
    expect(onSearch).toHaveBeenLastCalledWith('documentary films');
    expect(screen.getByRole('heading', { level: 2, name: 'Gaming' })).toBeTruthy();
  });

  it('groups results in the fixed order with jump links, Source chips and no Type chips', async () => {
    const results = [
      remoteEntry('v1'), remoteEntry('s1', { kind: 'short' }), remoteEntry('c1', { kind: 'channel', title: 'Harbor Films' }),
      remoteEntry('p1', { kind: 'playlist' }), liveEntry('l1'), remoteEntry('sc', { source: 'soundcloud', source_label: 'SoundCloud' }),
    ];
    const { container } = explore({ query: 'harbor', results, libraryResults: [stillItem()], titleResults: [] });
    expect(screen.getByRole('heading', { level: 1, name: 'Results for “harbor”' })).toBeTruthy();
    const headings = () => [...container.querySelectorAll('.g-explore-group')].map((group) => group.querySelector('h2')?.textContent);
    expect(headings()).toEqual(['In your library', 'Channels', 'Live now', 'Videos', 'Shorts', 'Playlists']);
    expect(within(screen.getByRole('navigation', { name: 'Jump to' })).getAllByRole('button').map((button) => button.textContent)).toEqual(['Channels', 'Live', 'Videos', 'Shorts', 'Playlists']);
    expect(screen.queryByRole('group', { name: 'Type' })).toBeNull();
    await userEvent.click(within(screen.getByRole('group', { name: 'Source' })).getByRole('button', { name: 'SoundCloud' }));
    expect(headings()).toEqual(['Videos']);
  });

  it('keeps a failed source inline with its retry, and says what nothing means', async () => {
    const { onSearch } = explore({ query: 'x', results: [remoteEntry('v1')], sourceErrors: [{ source: 'soundcloud', message: 'SoundCloud is resting.', retryable: true }] });
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain('SoundCloud results are unavailable');
    await userEvent.click(within(alert).getByRole('button', { name: 'Retry SoundCloud' }));
    expect(onSearch).toHaveBeenCalledWith('x');
    document.body.innerHTML = '';
    explore({ query: 'zzz', results: [] });
    expect(screen.getByText('Nothing found for “zzz”.')).toBeTruthy();
    document.body.innerHTML = '';
    explore({ query: 'x', results: [], error: 'Search broke.' });
    expect(screen.getByText('Explore is unavailable right now.')).toBeTruthy();
  });

  it('links a YouTube channel result to its page, and saves videos from the wall', async () => {
    const { onQueue } = explore({ query: 'x', results: [remoteEntry('c1', { kind: 'channel', title: 'Harbor Films', id: 'UCabcdefghijklmnopqrstuv', webpage_url: 'https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv' }), remoteEntry('v1'), remoteEntry('v2', { saved_item_id: 'i1' })] });
    expect(screen.getByRole('link', { name: /Harbor Films/ }).getAttribute('href')).toBe('/channel/youtube/UCabcdefghijklmnopqrstuv');
    await userEvent.click(screen.getAllByRole('button', { name: 'Save' })[0]);
    expect(onQueue).toHaveBeenCalledWith(expect.objectContaining({ id: 'v1' }));
    expect((screen.getByRole('button', { name: 'Saved' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('opens a popular rail as a wall through the rail prop', async () => {
    const many = { ...popular, items: Array.from({ length: 30 }, (_, index) => remoteEntry(`p${index}`, { category_keys: ['gaming'] })) };
    const { onRailChange, rerender } = explore({ popular: many });
    rerender(<ExploreSurface error={null} isQueueing={() => false} library={[]} loading={false} onOpen={vi.fn()} onQueue={vi.fn()} onRailChange={onRailChange} onSearch={vi.fn()} popular={many} popularError={null} query="" rail="popular-gaming" results={[]} />);
    await waitFor(() => expect(screen.queryByRole('navigation', { name: 'Browse by category' })).toBeNull());
    expect(document.querySelector('.g-rail-wall')).not.toBeNull();
  });

  it('links the Live now teaser to /live and reports See all through onRailChange', async () => {
    const many = { ...popular, items: Array.from({ length: 30 }, (_, index) => remoteEntry(`p${index}`, { category_keys: ['gaming'] })) };
    const { onRailChange } = explore({ popular: many });
    expect((await screen.findByRole('link', { name: 'See all' })).getAttribute('href')).toBe('/streaming/live');
    await userEvent.click(screen.getByRole('button', { name: 'See all' }));
    expect(onRailChange).toHaveBeenCalledWith('popular-gaming');
  });

  it('Escape in the See all wall returns to the rails and puts focus back on that rail\'s See all', async () => {
    const many = { ...popular, items: Array.from({ length: 30 }, (_, index) => remoteEntry(`p${index}`, { category_keys: ['gaming'] })) };
    render(<ExploreSurface {...exploreProps({ popular: many })} onOpen={vi.fn()} onQueue={vi.fn()} onSearch={vi.fn()} />);
    const seeAll = await screen.findByRole('button', { name: 'See all' });
    const rail = seeAll.closest('section.g-rail')!.getAttribute('data-rail-key');
    await userEvent.click(seeAll);
    await waitFor(() => expect(document.querySelector('.g-rail-wall')).not.toBeNull());
    expect(screen.queryByRole('navigation', { name: 'Browse by category' })).toBeNull();
    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(document.querySelector('.g-rail-wall')).toBeNull());
    expect(screen.getByRole('navigation', { name: 'Browse by category' })).toBeTruthy();
    await waitFor(() => expect(document.activeElement).toBe(document.querySelector(`section.g-rail[data-rail-key="${rail}"] .g-rail-all`)));
  });

  it('Escape in the wall asks the address to go back when the rail is in the address', async () => {
    const many = { ...popular, items: Array.from({ length: 30 }, (_, index) => remoteEntry(`p${index}`, { category_keys: ['gaming'] })) };
    const back = vi.spyOn(window.history, 'back').mockImplementation(() => undefined);
    const { onRailChange } = explore({ popular: many, rail: 'popular-gaming' });
    await waitFor(() => expect(document.querySelector('.g-rail-wall')).not.toBeNull());
    await userEvent.keyboard('{Escape}');
    expect(back).toHaveBeenCalledOnce();
    expect(onRailChange).not.toHaveBeenCalled();
    back.mockRestore();
  });

  it('keeps the See all focus after the shell moves focus to the heading on an address pop', async () => {
    const many = { ...popular, items: Array.from({ length: 30 }, (_, index) => remoteEntry(`p${index}`, { category_keys: ['gaming'] })) };
    const handlers = { onSearch: vi.fn(), onOpen: vi.fn(), onQueue: vi.fn(), onOpenTitle: vi.fn(), onOpenLibrary: vi.fn(), onRailChange: vi.fn() };
    const view = render(<ExploreSurface {...exploreProps({ popular: many, rail: null })} {...handlers} />);
    const seeAll = await screen.findByRole('button', { name: 'See all' });
    const key = seeAll.closest('section.g-rail')!.getAttribute('data-rail-key');
    await userEvent.click(seeAll);
    view.rerender(<ExploreSurface {...exploreProps({ popular: many, rail: key })} {...handlers} />);
    await waitFor(() => expect(document.querySelector('.g-rail-wall')).not.toBeNull());
    // The shell's popstate path queues its heading focus before React commits the rail change.
    requestAnimationFrame(() => { document.querySelector<HTMLElement>('h1')?.focus(); });
    view.rerender(<ExploreSurface {...exploreProps({ popular: many, rail: null })} {...handlers} />);
    const target = () => document.querySelector(`section.g-rail[data-rail-key="${key}"] .g-rail-all`);
    await waitFor(() => expect(document.activeElement).toBe(target()));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(document.activeElement).toBe(target());
  });

  it('never renders an image from outside Lumina', async () => {
    const { container } = explore({ query: 'x', results: [remoteEntry('v', { artwork_url: 'https://i.ytimg.com/vi/v/hq.jpg' })] });
    await screen.findByRole('heading', { level: 1 });
    for (const image of container.querySelectorAll('img')) expect(image.getAttribute('src') ?? '').toMatch(/^(\/api\/|data:)/);
  });

  it('puts For you after the live teaser and orders the category rails by category_order', async () => {
    const withReco = { ...popular, items: [remoteEntry('p1', { category_keys: ['gaming'] }), remoteEntry('m1', { category_keys: ['music'] })], categories: [{ key: 'gaming', label: 'Gaming', state: 'ready' }, { key: 'music', label: 'Music', state: 'ready' }], for_you: [recoEntry('f1', 0, { title: 'Picked one' })], category_order: ['music', 'gaming'] } as PopularSnapshot;
    render(<RecoFeedbackProvider onError={vi.fn()} onExpired={vi.fn()} resetKey="m" restore={vi.fn()} suppress={vi.fn()}><ExploreSurface {...exploreProps({ popular: withReco })} onOpen={vi.fn()} onQueue={vi.fn()} onSearch={vi.fn()} /></RecoFeedbackProvider>);
    const forYou = await screen.findByRole('heading', { name: 'For you' });
    const music = screen.getByRole('heading', { name: /^Music/ });
    const gaming = screen.getByRole('heading', { name: /^Gaming/ });
    expect(forYou.compareDocumentPosition(music) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(music.compareDocumentPosition(gaming) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole('button', { name: 'More options for Picked one' })).toBeTruthy();
  });

  const shell = (snapshot: PopularSnapshot, handlers = { suppress: vi.fn().mockResolvedValue({ id: 's1' }), restore: vi.fn().mockResolvedValue(undefined) }) => {
    render(<RecoFeedbackProvider onError={vi.fn()} onExpired={vi.fn()} resetKey="m" {...handlers}><ExploreSurface {...exploreProps({ popular: snapshot })} onOpen={vi.fn()} onQueue={vi.fn()} onSearch={vi.fn()} /></RecoFeedbackProvider>);
  };

  it('offers no Show fewer on a category rail when the server sent no category order (kill switch off)', async () => {
    shell(popular);
    await userEvent.click(await screen.findByRole('button', { name: /^More options for/ }));
    expect(screen.getByRole('menuitem', { name: 'Not interested' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: /Show fewer/ })).toBeNull();
  });

  it('offers Show fewer on a category rail when the server sent a category order', async () => {
    shell({ ...popular, for_you: [], category_order: ['gaming'] } as PopularSnapshot);
    await userEvent.click(await screen.findByRole('button', { name: /^More options for/ }));
    expect(screen.getByRole('menuitem', { name: /Show fewer/ })).toBeTruthy();
  });

  it('shows one Undo row for an item held by For you and a category rail, and returns focus to the restored card', async () => {
    const shared = recoEntry('f1', 0, { title: 'Picked one', category_keys: ['gaming'] });
    shell({ ...popular, items: [{ ...shared, reco: undefined }], for_you: [shared], category_order: ['gaming'] } as PopularSnapshot);
    await userEvent.click(await screen.findAllByRole('button', { name: 'More options for Picked one' }).then((all) => all[0]));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Not interested' }));
    expect(await screen.findAllByRole('status', { name: '' }).then((rows) => rows.filter((row) => row.textContent?.includes('Undo')))).toHaveLength(1);
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(document.activeElement?.closest('[data-remote-key]')).toBeTruthy());
    expect(document.activeElement).not.toBe(document.body);
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
  });

  it('puts the Undo row and refocus in the rail the member acted in, not the first rail holding the item', async () => {
    const shared = recoEntry('f1', 0, { title: 'Picked one', category_keys: ['gaming'] });
    shell({ ...popular, items: [{ ...shared, reco: undefined }], for_you: [shared], category_order: ['gaming'] } as PopularSnapshot);
    const buttons = await screen.findAllByRole('button', { name: 'More options for Picked one' });
    await userEvent.click(buttons[buttons.length - 1]);
    await userEvent.click(screen.getByRole('menuitem', { name: 'Not interested' }));
    const undo = await screen.findByRole('button', { name: 'Undo' });
    // The Undo row renders just before its rail's section.
    const railAfter = (node: Element) => Array.from(document.querySelectorAll('[data-rail-key]')).find((rail) => node.compareDocumentPosition(rail) & Node.DOCUMENT_POSITION_FOLLOWING)?.getAttribute('data-rail-key');
    expect(railAfter(undo)).toBe('popular-gaming');
    await userEvent.click(undo);
    await waitFor(() => expect(document.activeElement?.closest('[data-rail-key]')?.getAttribute('data-rail-key')).toBe('popular-gaming'));
  });
});
