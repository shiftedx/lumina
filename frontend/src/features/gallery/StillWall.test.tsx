import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api';
import { stillItem } from '../../test/galleryFixtures';
import type { LibraryItem } from '../../types';
import { ToastProvider } from '../../ui';
import { resetImageLoader } from './imageLoader';
import { parseStillQuery, serializeStillQuery, StillWall, type StillWallProps } from './StillWall';

type Observed = { callback: IntersectionObserverCallback };
let observers: Observed[] = [];
class FakeObserver {
  callback: IntersectionObserverCallback;
  constructor(callback: IntersectionObserverCallback) { this.callback = callback; observers.push(this); }
  observe() {}
  unobserve() {}
  disconnect() {}
}
const page = (items: LibraryItem[], next_cursor: string | null = null) => ({ items, next_cursor });
function wall(overrides: Partial<StillWallProps> = {}) {
  const props: StillWallProps = { canDelete: () => true, count: 318, kind: 'video', onItemChanged: vi.fn(), onPlay: vi.fn(), onWallChange: vi.fn(), shape: 'still', ...overrides };
  return { props, ...render(<ToastProvider><StillWall {...props} /></ToastProvider>) };
}

beforeEach(() => {
  resetImageLoader();
  observers = [];
  vi.stubGlobal('IntersectionObserver', FakeObserver);
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('StillWall', () => {
  it('lists the newest 60 under the masthead and its count', async () => {
    const list = vi.spyOn(api, 'listLibrary').mockResolvedValue(page([stillItem('v1'), stillItem('v2', { title: 'Canal at noon' })]));
    wall();
    expect(screen.getByRole('heading', { level: 1, name: 'YouTube' })).toBeTruthy();
    expect(screen.getByText('318 videos · Sorted by recently added')).toBeTruthy();
    expect(await screen.findByRole('button', { name: /^Canal at noon, / })).toBeTruthy();
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ kind: 'video', sort: 'recent', limit: 60, cursor: null }));
  });

  it('sorts and filters through the address and drops invalid values', async () => {
    expect(parseStillQuery('video', 'sort=bogus&source=vimeo')).toEqual({ sort: 'recent', source: '' });
    expect(parseStillQuery('video', 'sort=title&source=twitch&view=saved')).toEqual({ sort: 'title', source: 'twitch' });
    expect(parseStillQuery('recording', 'source=twitch')).toEqual({ sort: 'recent', source: '' });
    expect(serializeStillQuery({ sort: 'title', source: 'kick' })).toBe('sort=title&source=kick');
    expect(serializeStillQuery({ sort: 'recent', source: '' })).toBe('');
    const list = vi.spyOn(api, 'listLibrary').mockResolvedValue(page([stillItem()]));
    const { props, rerender } = wall();
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Sort' }), 'title');
    expect(props.onWallChange).toHaveBeenLastCalledWith('sort=title');
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Source' }), 'twitch');
    expect(props.onWallChange).toHaveBeenLastCalledWith('source=twitch');
    rerender(<ToastProvider><StillWall {...props} wall="sort=title&source=twitch" /></ToastProvider>);
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'title', source: 'twitch', cursor: null })));
    expect(screen.getByText('Filtered · Sorted by title')).toBeTruthy();
    rerender(<ToastProvider><StillWall {...props} kind="recording" wall="" /></ToastProvider>);
    expect(screen.queryByRole('combobox', { name: 'Source' })).toBeNull();
  });

  it('loads the next page as the sentinel comes within a viewport', async () => {
    const list = vi.spyOn(api, 'listLibrary').mockResolvedValueOnce(page([stillItem('v1')], 'next')).mockResolvedValueOnce(page([stillItem('v2', { title: 'Second page' })]));
    wall();
    await screen.findByRole('button', { name: /^Harbor walk at dawn, / });
    act(() => observers.at(-1)!.callback([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(await screen.findByRole('button', { name: /^Second page, / })).toBeTruthy();
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: 'next' }));
  });

  it('deletes from the More menu with Undo, and reports each change', async () => {
    const item = stillItem();
    vi.spyOn(api, 'listLibrary').mockResolvedValue(page([item]));
    const remove = vi.spyOn(api, 'deleteLibraryFile').mockResolvedValue({ ...item, status: 'missing', media_state: 'quarantined' });
    const restore = vi.spyOn(api, 'restoreLibraryFile').mockResolvedValue(item);
    const { props } = wall();
    await userEvent.click(await screen.findByRole('button', { name: 'More options for Harbor walk at dawn' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Delete from Lumina' }));
    expect(remove).toHaveBeenCalledWith('video-1');
    expect(await screen.findByText('Deleted “Harbor walk at dawn”. It stays restorable for a while.')).toBeTruthy();
    expect(document.querySelector('.library-toast, .app-toast')).toBeNull();
    expect(screen.queryByRole('button', { name: /^Harbor walk at dawn, / })).toBeNull();
    expect(props.onItemChanged).toHaveBeenCalledWith(expect.objectContaining({ status: 'missing' }));
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(restore).toHaveBeenCalledWith('video-1');
    expect(await screen.findByRole('button', { name: /^Harbor walk at dawn, / })).toBeTruthy();
    expect(props.onItemChanged).toHaveBeenLastCalledWith(item);
  });

  it('keeps the card and says why when the delete fails', async () => {
    vi.spyOn(api, 'listLibrary').mockResolvedValue(page([stillItem()]));
    vi.spyOn(api, 'deleteLibraryFile').mockRejectedValue(new Error('The file is in use.'));
    wall();
    await userEvent.click(await screen.findByRole('button', { name: 'More options for Harbor walk at dawn' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Delete from Lumina' }));
    expect(await screen.findByText('The file is in use.')).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Harbor walk at dawn, / })).toBeTruthy();
  });

  it('adds to the watch queue from the More menu, even for an item it may not delete, and says so', async () => {
    vi.spyOn(api, 'listLibrary').mockResolvedValue(page([stillItem()]));
    const add = vi.spyOn(api, 'addToWatchQueue').mockResolvedValue({ entries: [], revision: 1 } as unknown as Awaited<ReturnType<typeof api.addToWatchQueue>>);
    wall({ canDelete: () => false });
    await userEvent.click(await screen.findByRole('button', { name: 'More options for Harbor walk at dawn' }));
    expect(screen.queryByRole('menuitem', { name: 'Delete from Lumina' })).toBeNull();
    await userEvent.click(screen.getByRole('menuitem', { name: 'Add to queue' }));
    expect(add).toHaveBeenCalledWith({ kind: 'library', library_item_id: 'video-1' }, 'end');
    expect(await screen.findByText('Added to your watchlist.')).toBeTruthy();
  });

  it('shows each kind its own empty state, and Clear filters for an empty source', async () => {
    vi.spyOn(api, 'listLibrary').mockResolvedValue(page([]));
    const videos = wall();
    expect(await screen.findByText('No saved videos yet')).toBeTruthy();
    videos.unmount();
    const recordings = wall({ kind: 'recording', count: 0 });
    expect(await screen.findByText('Recordings of live streams you record appear here.')).toBeTruthy();
    recordings.unmount();
    const { props } = wall({ wall: 'source=kick' });
    expect(await screen.findByText('Nothing matches these filters.')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(props.onWallChange).toHaveBeenLastCalledWith('');
  });

  it('says when a page fails and tries again', async () => {
    const list = vi.spyOn(api, 'listLibrary').mockRejectedValueOnce(new Error('boom')).mockResolvedValue(page([stillItem()]));
    wall();
    expect(await screen.findByText('Lumina could not load more.')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('button', { name: /^Harbor walk at dawn, / })).toBeTruthy();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('shows the caller\'s header in place of its masthead, with square cards for saved audio', async () => {
    vi.spyOn(api, 'listLibrary').mockResolvedValue(page([stillItem('a1', { kind: 'audio' })]));
    const { container } = wall({ header: <h1>Music</h1>, kind: 'audio', shape: 'square' });
    expect(screen.getByRole('heading', { level: 1, name: 'Music' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Saved audio' })).toBeNull();
    await screen.findByRole('button', { name: /^Harbor walk at dawn, / });
    expect(container.querySelector('.g-art-square')).not.toBeNull();
  });
});

describe('the channel filter and view switch', () => {
  it('filters the Videos wall to one channel through the address, names it and clears it', async () => {
    const list = vi.spyOn(api, 'listLibrary').mockResolvedValue(page([stillItem()]));
    expect(parseStillQuery('video', 'channel=Harbor%20Films&sort=title')).toEqual({ sort: 'title', source: '', channel: 'Harbor Films' });
    expect(parseStillQuery('recording', 'channel=Harbor')).toEqual({ sort: 'recent', source: '' });
    expect(serializeStillQuery({ sort: 'recent', source: '', channel: 'Harbor Films' })).toBe('channel=Harbor+Films');
    const { props } = wall({ wall: 'channel=Harbor%20Films', viewSwitch: <div role="group" aria-label="View">switch</div> });
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ group: 'Harbor Films' })));
    expect(screen.getByRole('heading', { level: 1, name: 'Harbor Films' })).toBeTruthy();
    expect(screen.getByRole('group', { name: 'View' })).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(props.onWallChange).toHaveBeenLastCalledWith('');
  });

  it('carries the empty no-uploader channel through the address as a filtered wall, not the unfiltered one', async () => {
    const list = vi.spyOn(api, 'listLibrary').mockResolvedValue(page([stillItem()]));
    expect(parseStillQuery('video', 'channel=')).toEqual({ sort: 'recent', source: '', channel: '' });
    expect(serializeStillQuery({ sort: 'recent', source: '', channel: '' })).toBe('channel=');
    wall({ wall: 'channel=' });
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ group: '' })));
    expect(screen.getByRole('heading', { level: 1, name: 'No channel' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Clear' })).toBeTruthy();
  });
});
