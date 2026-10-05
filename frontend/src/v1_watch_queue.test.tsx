import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from './api';
import { ApiRequestError } from './api';
import { recoEntry } from './test/recoFixtures';
import { ArtMenu } from './features/gallery/ArtMenu';
import { ToastProvider } from './ui';
import { remoteTarget } from './features/reco/recoModel';
import { WatchTools } from './features/watch/WatchTools';
import { resetWatchQueue, WatchQueuePanel } from './features/watch/WatchQueue';
import type { WatchQueue, WatchQueueEntry } from './types';

function entry(id: string, overrides: Partial<WatchQueueEntry> = {}): WatchQueueEntry {
  return { id, position: 0, availability: 'available', ref: { kind: 'remote', library_item_id: null, provider: 'youtube', remote_id: id, url: `https://example.test/${id}` }, title: `Video ${id}`, uploader: 'Channel', artwork_url: null, duration: 60, ...overrides };
}

const queue = (revision: number, ...entries: WatchQueueEntry[]): WatchQueue => ({ revision, limit: 500, entries });

beforeEach(() => resetWatchQueue());
afterEach(() => { vi.restoreAllMocks(); });

describe('watch queue', () => {
  it('reorders with the current revision and refetches after a conflict without replaying', async () => {
    const get = vi.spyOn(api, 'getWatchQueue')
      .mockResolvedValueOnce(queue(3, entry('a'), entry('b')))
      .mockResolvedValueOnce(queue(5, entry('b'), entry('a')));
    const move = vi.spyOn(api, 'moveWatchQueueEntry').mockRejectedValue(new ApiRequestError('conflict', 409));
    const onPlay = vi.fn();
    render(<WatchQueuePanel autoplay onPlay={onPlay} />);
    expect(await screen.findByRole('button', { name: 'Play Video a, plays next automatically' })).toBeTruthy();

    await userEvent.click(screen.getByRole('button', { name: 'Move Video b up' }));
    expect(move).toHaveBeenCalledWith('b', 0, 3);
    expect(await screen.findByText(/changed on another screen/)).toBeTruthy();
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(move).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getAllByRole('listitem').map((row) => row.textContent)).toEqual([expect.stringContaining('Video b'), expect.stringContaining('Video a')]));

    await userEvent.click(screen.getByRole('button', { name: /^Play Video b/ }));
    expect(onPlay).toHaveBeenCalledWith(expect.objectContaining({ id: 'b' }));
  });

  it('shows a revoked item as a redacted, unplayable tombstone', async () => {
    vi.spyOn(api, 'getWatchQueue').mockResolvedValue(queue(1, entry('gone', { availability: 'unavailable', title: null, ref: { kind: 'library', library_item_id: null, provider: null, remote_id: null, url: null } })));
    render(<WatchQueuePanel autoplay onPlay={vi.fn()} />);
    const play = await screen.findByRole('button', { name: 'Unavailable video, no longer shared with you' });
    expect((play as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Remove Unavailable video from queue' })).toBeTruthy();
  });

  it('queues from a card menu without starting a download', async () => {
    const add = vi.spyOn(api, 'addToWatchQueue').mockResolvedValue(queue(1, entry('x')));
    const download = vi.spyOn(api, 'createJob');
    const item = recoEntry('x', 0, { title: 'Video x', webpage_url: 'https://example.test/x', source: 'youtube' });
    render(<ToastProvider><ArtMenu subject={{ kind: 'remote', item }} /></ToastProvider>);
    await userEvent.click(screen.getByRole('button', { name: 'More options for Video x' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Add to queue' }));
    expect(add).toHaveBeenCalledWith(expect.objectContaining({ kind: 'remote', url: 'https://example.test/x', provider: 'youtube', remote_id: 'x' }), 'end');
    expect(await screen.findByText('Added to your queue')).toBeTruthy();
    expect(download).not.toHaveBeenCalled();
  });
});

describe('watch queue chrome', () => {
  it('clearing the queue asks first', async () => {
    const clear = vi.spyOn(api, 'clearWatchQueue').mockResolvedValue(queue(2));
    vi.spyOn(api, 'getWatchQueue').mockResolvedValue(queue(1, entry('a'), entry('b'), entry('c')));
    render(<WatchQueuePanel autoplay onPlay={vi.fn()} />);
    await screen.findByRole('button', { name: /^Play Video a/ });
    const button = screen.getByRole('button', { name: 'Clear' });
    await userEvent.click(button);
    const dialog = screen.getByRole('dialog', { name: 'Remove all 3 videos from your queue?' });
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Cancel' }));
    fireEvent(dialog, new Event('cancel', { cancelable: true })); // jsdom does not turn Escape into cancel
    expect(clear).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(button);
    await userEvent.click(button);
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove all' }));
    expect(clear).toHaveBeenCalledOnce();
  });

  it('queue entries are hairline rows', async () => {
    vi.spyOn(api, 'getWatchQueue').mockResolvedValue(queue(1, entry('a'), entry('b')));
    render(<WatchQueuePanel autoplay onPlay={vi.fn()} />);
    await screen.findByRole('button', { name: /^Play Video a/ });
    expect(document.querySelectorAll('.g-list-row[data-queue-entry]')).toHaveLength(2);
  });
});

it('the watch tools tablist is a g-tabs row', () => {
  render(<WatchTools tools={[{ id: 'a', label: 'One', panel: 'x' }, { id: 'b', label: 'Two', panel: 'y' }]} />);
  expect(screen.getByRole('tablist').classList.contains('g-tabs')).toBe(true);
});
