import { useEffect, useState, useSyncExternalStore } from 'react';
import { ChevronDown, ChevronUp, FolderHeart, ListVideo, X } from 'lucide-react';
import { addHouseholdCollectionItem, addHouseholdCollectionRemoteItem, addToWatchQueue, ApiRequestError, clearWatchQueue, createHouseholdCollection, getWatchQueue, moveWatchQueueEntry, removeWatchQueueEntry } from '../../api';
import { type WatchQueue, type WatchQueueEntry, type WatchQueueRef, type YouTubeSearchResult } from '../../types';
import { Artwork } from '../../Artwork';
import { ConfirmDialog, IconButton } from '../../ui';
import { formatDuration } from '../../utils';
import './tools.css';

/*
 * The member's server-side watch queue. One module-level store so any
 * card menu can queue without prop threading, and the Watch rail reads the
 * same snapshot. Queueing is playback intent only; it never starts a download.
 */
type QueueState = { queue: WatchQueue | null; loading: boolean; message: string | null };

let state: QueueState = { queue: null, loading: false, message: null };
let generation = 0;
const listeners = new Set<() => void>();

function publish(patch: Partial<QueueState>) {
  state = { ...state, ...patch };
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

const snapshot = () => state;

export function useWatchQueue(): QueueState {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** Drops the previous member's queue on sign-in/out; late responses are ignored. */
export function resetWatchQueue() {
  generation += 1;
  publish({ queue: null, loading: false, message: null });
}

async function run(request: () => Promise<WatchQueue>, failure: string): Promise<boolean> {
  const current = generation;
  try {
    const queue = await request();
    if (!Array.isArray(queue?.entries)) throw new Error('Malformed watch queue');
    if (current === generation) publish({ queue, loading: false, message: null });
    return current === generation;
  } catch (error) {
    if (current !== generation) return false;
    if (error instanceof ApiRequestError && error.status === 409) {
      // Another screen changed the order first: show its result, never replay ours.
      void run(getWatchQueue, 'Your watch queue could not load.').then((loaded) => {
        if (loaded) publish({ message: 'Your queue changed on another screen, so Lumina refreshed it.' });
      });
      return false;
    }
    publish({ loading: false, message: failure });
    return false;
  }
}

const UPDATE_FAILED = 'Lumina could not update your watch queue.';

export function loadWatchQueue() {
  if (!state.queue) publish({ loading: true });
  return run(getWatchQueue, 'Your watch queue could not load.');
}

export const queueMedia = (ref: WatchQueueRef, position: 'next' | 'end') => run(() => addToWatchQueue(ref, position), UPDATE_FAILED);
export const removeQueued = (id: string) => run(() => removeWatchQueueEntry(id), UPDATE_FAILED);
const moveQueued = (id: string, position: number, revision: number) => run(() => moveWatchQueueEntry(id, position, revision), UPDATE_FAILED);

export function remoteQueueRef(item: YouTubeSearchResult): WatchQueueRef | null {
  if (!item.webpage_url) return null;
  return { kind: 'remote', provider: item.source || null, remote_id: item.id || null, url: item.webpage_url, title: item.title?.slice(0, 1000) || null, uploader: item.uploader?.slice(0, 500) || null, artwork_url: item.artwork_url || null, duration: item.duration ?? null };
}

/** Reopens a queued remote ref through the normal remote Watch flow. */
export function queuedAsRemote(entry: WatchQueueEntry): YouTubeSearchResult {
  return { id: entry.ref.remote_id, source: (entry.ref.provider || undefined) as YouTubeSearchResult['source'], title: entry.title, uploader: entry.uploader, artwork_url: entry.artwork_url, duration: entry.duration, webpage_url: entry.ref.url };
}

export function firstPlayableQueued(queue: WatchQueue | null): WatchQueueEntry | null {
  return queue?.entries.find((entry) => entry.availability === 'available') || null;
}

export function WatchQueuePanel({ onPlay, autoplay }: { onPlay: (entry: WatchQueueEntry) => void; autoplay: boolean }) {
  const { queue, message } = useWatchQueue();
  const [savingCollection, setSavingCollection] = useState(false);
  const [confirmingClear, setConfirmingClear] = useState(false);
  const [savedCollectionMessage, setSavedCollectionMessage] = useState<string | null>(null);
  useEffect(() => { void loadWatchQueue(); }, []);
  const entries = queue?.entries || [];
  const next = autoplay ? firstPlayableQueued(queue) : null;

  async function move(entry: WatchQueueEntry, index: number, direction: 'up' | 'down') {
    if (!queue) return;
    await moveQueued(entry.id, index, queue.revision);
    // Reordering moves DOM nodes; keep keyboard focus on the moved entry.
    requestAnimationFrame(() => {
      const row = document.querySelector(`[data-queue-entry="${entry.id}"]`);
      (row?.querySelector<HTMLButtonElement>(`[data-direction="${direction}"]:not(:disabled)`) || row?.querySelector<HTMLButtonElement>('button:not(:disabled)'))?.focus();
    });
  }

  // Deferred: turn the current play-next order into a saved collection.
  async function saveAsCollection() {
    const name = window.prompt('Name this collection', 'From my queue')?.trim();
    if (!name) return;
    setSavingCollection(true);
    setSavedCollectionMessage(null);
    try {
      const created = await createHouseholdCollection({ name, visibility: 'private' });
      for (const entry of entries) {
        if (entry.availability !== 'available') continue;
        // eslint-disable-next-line no-await-in-loop -- entries must land in queue order
        if (entry.ref.kind === 'library' && entry.ref.library_item_id) await addHouseholdCollectionItem(created.id, entry.ref.library_item_id);
        // eslint-disable-next-line no-await-in-loop -- entries must land in queue order
        else if (entry.ref.kind === 'remote' && entry.ref.url) await addHouseholdCollectionRemoteItem(created.id, { provider: entry.ref.provider, remote_id: entry.ref.remote_id, url: entry.ref.url, title: entry.title, uploader: entry.uploader, artwork_url: entry.artwork_url, duration: entry.duration });
      }
      setSavedCollectionMessage(`Saved as “${created.name}”.`);
    } catch {
      setSavedCollectionMessage('Your queue could not be saved as a collection. Try again.');
    } finally {
      setSavingCollection(false);
    }
  }

  // An empty queue takes no room on the watch page: queueing is offered from each video's options instead.
  if (!entries.length && !message && !savedCollectionMessage) return null;
  return (
    <section aria-labelledby="watch-queue-title" className="watch-queue">
      <header>
        <h2 id="watch-queue-title">Your queue{entries.length ? <span className="watch-queue-count">{entries.length}</span> : null}</h2>
        {entries.length ? <div className="watch-queue-header-actions"><button className="g-text-button" disabled={savingCollection} onClick={() => void saveAsCollection()} type="button"><FolderHeart />Save queue as collection</button><button className="g-text-button" onClick={() => setConfirmingClear(true)} type="button">Clear</button></div> : null}
      </header>
      {savedCollectionMessage ? <p className="watch-queue-message" role="status">{savedCollectionMessage}</p> : null}
      {message ? <p className="watch-queue-message" role="status">{message}</p> : null}
      {entries.length ? (
        <ol className="watch-queue-list">
          {entries.map((entry, index) => {
            const available = entry.availability === 'available';
            const title = entry.title || (available ? 'Untitled video' : 'Unavailable video');
            return (
              <li className={`g-list-row watch-queue-item${available ? '' : ' unavailable'}`} data-queue-entry={entry.id} key={entry.id}>
                <button aria-label={available ? `Play ${title}${entry === next ? ', plays next automatically' : ''}` : 'Unavailable video, no longer shared with you'} className="watch-queue-play" disabled={!available} onClick={() => onPlay(entry)} type="button">
                  <span className="watch-queue-thumb">{available ? <Artwork alt="" src={entry.artwork_url} /> : <ListVideo aria-hidden="true" />}{entry.duration ? <b>{formatDuration(entry.duration)}</b> : null}</span>
                  <span className="watch-queue-copy"><strong>{title}</strong><small className={entry === next ? 'watch-queue-next' : undefined}>{entry === next ? 'Plays next' : available ? entry.uploader || 'Video' : 'No longer available to you'}</small></span>
                </button>
                <span className="watch-queue-controls">
                  <IconButton data-direction="up" disabled={index === 0} icon={<ChevronUp />} label={`Move ${title} up`} onClick={() => void move(entry, index - 1, 'up')} />
                  <IconButton data-direction="down" disabled={index === entries.length - 1} icon={<ChevronDown />} label={`Move ${title} down`} onClick={() => void move(entry, index + 1, 'down')} />
                  <IconButton icon={<X />} label={`Remove ${title} from queue`} onClick={() => void removeQueued(entry.id)} />
                </span>
              </li>
            );
          })}
        </ol>
      ) : null}
      <ConfirmDialog
        body="Your queue will be empty. The videos stay where they are."
        confirmLabel="Remove all"
        danger
        onCancel={() => setConfirmingClear(false)}
        onConfirm={() => { setConfirmingClear(false); void run(clearWatchQueue, UPDATE_FAILED); }}
        open={confirmingClear}
        title={`Remove all ${entries.length} videos from your queue?`}
      />
    </section>
  );
}
