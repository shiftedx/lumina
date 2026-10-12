import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from './api';
import { CollectionDetail } from './features/collections/CollectionDetail';
import { resetWatchQueue, WatchQueuePanel } from './features/watch/WatchQueue';
import type { CollectionEntry, HouseholdCollection, WatchQueue } from './types';

function libraryEntry(id: string, overrides: Partial<CollectionEntry> = {}): CollectionEntry {
  return { id, position: 0, availability: 'available', ref: { kind: 'library', library_item_id: id, provider: null, remote_id: null, url: null }, title: `Saved ${id}`, uploader: null, artwork_url: null, duration: 90, ...overrides };
}

function remoteEntry(id: string, overrides: Partial<CollectionEntry> = {}): CollectionEntry {
  return { id, position: 0, availability: 'available', ref: { kind: 'remote', library_item_id: null, provider: 'youtube', remote_id: id, url: `https://example.test/${id}` }, title: `Video ${id}`, uploader: 'Channel', artwork_url: null, duration: 60, ...overrides };
}

function collection(entries: CollectionEntry[], overrides: Partial<HouseholdCollection> = {}): HouseholdCollection {
  return { id: 'c1', owner_user_id: 'viewer', name: 'Mixed picks', visibility: 'private', revision: 3, item_count: entries.filter((e) => e.ref.kind === 'library').length, items: [], entries, created_at: '2026-07-16T00:00:00Z', updated_at: '2026-07-16T00:00:00Z', ...overrides };
}

describe('collection detail: watch vs save, mixed sources', () => {
  it('watches a library entry locally and a remote entry via the remote flow, never downloading either', async () => {
    const onOpenLibrary = vi.fn();
    const onOpenRemote = vi.fn();
    const onQueueRemote = vi.fn();
    render(<CollectionDetail collection={collection([libraryEntry('lib-1'), remoteEntry('rem-1')])} onChanged={vi.fn()} onOpenLibrary={onOpenLibrary} onOpenRemote={onOpenRemote} onQueueRemote={onQueueRemote} onReload={vi.fn()} owner />);

    await userEvent.click(screen.getByRole('button', { name: 'Watch Saved lib-1' }));
    expect(onOpenLibrary).toHaveBeenCalledWith('lib-1');
    await userEvent.click(screen.getByRole('button', { name: 'Watch Video rem-1' }));
    expect(onOpenRemote).toHaveBeenCalledWith(expect.objectContaining({ webpage_url: 'https://example.test/rem-1' }));
    expect(onQueueRemote).not.toHaveBeenCalled();
  });

  it('separates Save to vault from Watch, and offers it only for remote entries', async () => {
    const onQueueRemote = vi.fn();
    render(<CollectionDetail collection={collection([libraryEntry('lib-1'), remoteEntry('rem-1')])} onChanged={vi.fn()} onQueueRemote={onQueueRemote} onReload={vi.fn()} owner />);

    expect(screen.queryByRole('button', { name: 'Save Saved lib-1 to vault' })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Save Video rem-1 to vault' }));
    expect(onQueueRemote).toHaveBeenCalledWith(expect.objectContaining({ webpage_url: 'https://example.test/rem-1' }));
  });

  it('tombstones a revoked library entry as unwatchable with no Save action, never leaking a title', async () => {
    const tombstone = libraryEntry('lib-2', { availability: 'unavailable', title: null, ref: { kind: 'library', library_item_id: null, provider: null, remote_id: null, url: null } });
    render(<CollectionDetail collection={collection([tombstone])} onChanged={vi.fn()} onReload={vi.fn()} owner />);
    const watch = screen.getByRole('button', { name: 'No longer available, no longer available' });
    expect((watch as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole('button', { name: /Save .* to vault/ })).toBeNull();
  });

  it('reorders via buttons against the current revision and applies the server result', async () => {
    const moveEntry = vi.fn().mockResolvedValue(collection([remoteEntry('b'), remoteEntry('a')]));
    const onChanged = vi.fn();
    render(<CollectionDetail api={{ addRemote: vi.fn(), removeEntry: vi.fn(), moveEntry, createBatch: vi.fn() }} collection={collection([remoteEntry('a'), remoteEntry('b')])} onChanged={onChanged} onReload={vi.fn()} owner />);

    await userEvent.click(screen.getByRole('button', { name: 'Move Video b up' }));
    expect(moveEntry).toHaveBeenCalledWith('c1', 'b', 0, 3);
    expect(onChanged).toHaveBeenCalledWith(expect.objectContaining({ entries: [expect.objectContaining({ id: 'b' }), expect.objectContaining({ id: 'a' })] }));
  });

  it('removing an entry from the collection never deletes the underlying artifact (server owns that guarantee; this only asserts the request shape)', async () => {
    const removeEntry = vi.fn().mockResolvedValue(collection([]));
    render(<CollectionDetail api={{ addRemote: vi.fn(), removeEntry, moveEntry: vi.fn(), createBatch: vi.fn() }} collection={collection([libraryEntry('lib-1')])} onChanged={vi.fn()} onReload={vi.fn()} owner />);
    await userEvent.click(screen.getByRole('button', { name: 'Remove Saved lib-1 from collection' }));
    expect(removeEntry).toHaveBeenCalledWith('c1', 'lib-1', 3);
  });

  it('reload on a stale reorder conflict shows the winner rather than losing it', async () => {
    const moveEntry = vi.fn().mockRejectedValue(new api.ApiRequestError('conflict', 409));
    const onReload = vi.fn();
    render(<CollectionDetail api={{ addRemote: vi.fn(), removeEntry: vi.fn(), moveEntry, createBatch: vi.fn() }} collection={collection([remoteEntry('a'), remoteEntry('b')])} onChanged={vi.fn()} onReload={onReload} owner />);
    await userEvent.click(screen.getByRole('button', { name: 'Move Video b up' }));
    expect(await screen.findByText(/changed elsewhere/)).toBeTruthy();
    expect(onReload).toHaveBeenCalled();
  });
});

describe('play all pushes the collection to the watch queue', () => {
  beforeEach(() => resetWatchQueue());
  afterEach(() => vi.restoreAllMocks());

  it('queues every available entry, in order, without visiting Save to vault', async () => {
    const add = vi.spyOn(api, 'addToWatchQueue').mockResolvedValue({ revision: 1, limit: 500, entries: [] } as WatchQueue);
    render(<CollectionDetail collection={collection([libraryEntry('lib-1'), remoteEntry('rem-1')])} onChanged={vi.fn()} onReload={vi.fn()} owner />);
    await userEvent.click(screen.getByRole('button', { name: 'Play all' }));
    expect(add.mock.calls.map((call) => call[0])).toEqual([
      expect.objectContaining({ kind: 'library', library_item_id: 'lib-1' }),
      expect.objectContaining({ kind: 'remote', url: 'https://example.test/rem-1' }),
    ]);
  });
});

describe('watch queue rail: save queue as a collection', () => {
  beforeEach(() => resetWatchQueue());
  afterEach(() => vi.restoreAllMocks());

  it('creates a collection from the current queue in order', async () => {
    vi.spyOn(api, 'getWatchQueue').mockResolvedValue({
      revision: 2, limit: 500,
      entries: [
        { id: 'q1', position: 0, availability: 'available', ref: { kind: 'library', library_item_id: 'lib-1', provider: null, remote_id: null, url: null }, title: 'Saved show', uploader: null, artwork_url: null, duration: 100 },
        { id: 'q2', position: 1, availability: 'available', ref: { kind: 'remote', library_item_id: null, provider: 'youtube', remote_id: 'r1', url: 'https://example.test/r1' }, title: 'Public clip', uploader: 'Channel', artwork_url: null, duration: 40 },
      ],
    });
    const create = vi.spyOn(api, 'createHouseholdCollection').mockResolvedValue(collection([], { id: 'new-collection', name: 'From my queue' }));
    const addItem = vi.spyOn(api, 'addHouseholdCollectionItem').mockResolvedValue(collection([]));
    const addRemote = vi.spyOn(api, 'addHouseholdCollectionRemoteItem').mockResolvedValue(collection([]));
    vi.spyOn(window, 'prompt').mockReturnValue('From my queue');

    render(<WatchQueuePanel autoplay={false} onPlay={vi.fn()} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Save queue as collection' }));

    expect(create).toHaveBeenCalledWith(expect.objectContaining({ name: 'From my queue' }));
    expect(addItem).toHaveBeenCalledWith('new-collection', 'lib-1');
    expect(addRemote).toHaveBeenCalledWith('new-collection', expect.objectContaining({ url: 'https://example.test/r1' }));
  });
});
