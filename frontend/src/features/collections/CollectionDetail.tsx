import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowDownToLine, ChevronDown, ChevronUp, ListVideo, Play, X } from 'lucide-react';

import {
  addHouseholdCollectionRemoteItem,
  ApiRequestError,
  createAcquisitionBatch,
  moveHouseholdCollectionEntry,
  removeHouseholdCollectionEntry,
} from '../../api';
import type { CollectionEntry, FormatSelection, HouseholdCollection, OutputProfile, TitleSummary, YouTubeSearchResult } from '../../types';
import { Artwork } from '../../Artwork';
import { Button, ConfirmDialog, Dialog, ErrorState, Field, Input, Masthead, fieldProps } from '../../ui';
import { PosterCard } from '../gallery/PosterCard';
import { StillCard } from '../gallery/StillCard';
import { relativeAge } from '../gallery/remoteModel';
import { formatDuration } from '../../utils';
import { queueMedia } from '../watch/WatchQueue';
import { moveFocus } from '../media/focusNav';
import { ruleChipLabel } from './SmartCollectionBuilder';
import './collections.css';

function friendlyMutationError(error: unknown): string {
  if (error instanceof ApiRequestError && (error.status === 403 || error.status === 404)) return 'Collection access changed. The latest household view is shown.';
  return 'The collection could not be updated. Refresh and try again.';
}

type CollectionDetailApi = {
  addRemote: typeof addHouseholdCollectionRemoteItem;
  removeEntry: typeof removeHouseholdCollectionEntry;
  moveEntry: typeof moveHouseholdCollectionEntry;
  createBatch: typeof createAcquisitionBatch;
};

const defaultApi: CollectionDetailApi = {
  addRemote: addHouseholdCollectionRemoteItem,
  removeEntry: removeHouseholdCollectionEntry,
  moveEntry: moveHouseholdCollectionEntry,
  createBatch: createAcquisitionBatch,
};

/** A queued/saved entry's own remote fields, reopened through the normal remote Watch flow. Never triggers a download. */
export function entryAsRemote(entry: CollectionEntry): YouTubeSearchResult {
  return { id: entry.ref.remote_id, source: (entry.ref.provider || undefined) as YouTubeSearchResult['source'], title: entry.title, uploader: entry.uploader, artwork_url: entry.artwork_url, duration: entry.duration, webpage_url: entry.ref.url };
}

export function CollectionDetail({
  collection,
  owner,
  onBack,
  onRename,
  onDelete,
  onEditRules,
  onChanged,
  onReload,
  onOpenLibrary = () => undefined,
  onOpenRemote = () => undefined,
  onQueueRemote = () => undefined,
  isQueueing = () => false,
  onOpenTitle = () => undefined,
  formatSelection,
  outputProfile,
  api = defaultApi,
}: {
  collection: HouseholdCollection;
  owner: boolean;
  /** Set by the Collections page: renders the editorial header (back, rename, delete); absent in the All landing's embedded panel. */
  onBack?: () => void;
  onRename?: (name: string) => Promise<void> | void;
  onDelete?: () => Promise<void> | void;
  onEditRules?: () => void;
  onChanged: (collection: HouseholdCollection) => void;
  onReload: () => void;
  onOpenLibrary?: (libraryItemId: string) => void;
  onOpenRemote?: (item: YouTubeSearchResult) => void;
  onQueueRemote?: (item: YouTubeSearchResult) => void;
  isQueueing?: (item: YouTubeSearchResult) => boolean;
  onOpenTitle?: (title: TitleSummary) => void;
  // Save-all-to-vault (a batch dry-run over remote entries) is available only
  // when the host surface supplies its acquisition defaults.
  formatSelection?: FormatSelection;
  outputProfile?: OutputProfile;
  api?: CollectionDetailApi;
}) {
  const mounted = useRef(true);
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [linkUrl, setLinkUrl] = useState('');
  const [savingAll, setSavingAll] = useState(false);
  const [savedAllMessage, setSavedAllMessage] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const acting = useRef(false);
  // A failed rename or delete surfaces its reason; the page state is untouched. One at a time.
  async function act(run: (() => Promise<void> | void) | undefined) {
    if (!run || acting.current) return;
    acting.current = true;
    try { await run(); } catch (failure) { if (mounted.current) setError(friendlyMutationError(failure)); } finally { acting.current = false; }
  }

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const entries = collection.entries;
  const remoteEntries = entries.filter((entry) => entry.ref.kind === 'remote');

  async function mutate(entryId: string, action: () => Promise<HouseholdCollection>) {
    setBusy((current) => new Set(current).add(entryId));
    try {
      onChanged(await action());
      setError(null);
    } catch (failure) {
      const conflict = failure instanceof ApiRequestError && failure.status === 409;
      setError(conflict ? 'This collection changed elsewhere. Refreshed to the latest order.' : friendlyMutationError(failure));
      onReload();
    } finally {
      if (mounted.current) setBusy((current) => { const next = new Set(current); next.delete(entryId); return next; });
    }
  }

  function watch(entry: CollectionEntry) {
    if (entry.availability !== 'available') return;
    if (entry.ref.kind === 'library' && entry.ref.library_item_id) onOpenLibrary(entry.ref.library_item_id);
    else if (entry.ref.kind === 'remote') onOpenRemote(entryAsRemote(entry));
  }

  async function addLink(event: FormEvent) {
    event.preventDefault();
    const url = linkUrl.trim();
    if (!url) return;
    await mutate('add-link', () => api.addRemote(collection.id, { url, title: url }, collection.revision));
    if (mounted.current) setLinkUrl('');
  }

  async function move(entry: CollectionEntry, index: number, direction: 'up' | 'down') {
    await mutate(entry.id, () => api.moveEntry(collection.id, entry.id, index, collection.revision));
    requestAnimationFrame(() => {
      const row = document.querySelector(`[data-collection-entry="${entry.id}"]`);
      (row?.querySelector<HTMLButtonElement>(`[data-direction="${direction}"]:not(:disabled)`) || row?.querySelector<HTMLButtonElement>('button:not(:disabled)'))?.focus();
    });
  }

  async function playAll() {
    const playable = entries.filter((entry) => entry.availability === 'available');
    for (const entry of playable) {
      const ref = entry.ref.kind === 'library' && entry.ref.library_item_id
        ? { kind: 'library' as const, library_item_id: entry.ref.library_item_id }
        : { kind: 'remote' as const, provider: entry.ref.provider, remote_id: entry.ref.remote_id, url: entry.ref.url || '', title: entry.title, uploader: entry.uploader, artwork_url: entry.artwork_url, duration: entry.duration };
      if (ref.kind === 'remote' && !ref.url) continue;
      // eslint-disable-next-line no-await-in-loop -- queue order must match collection order
      await queueMedia(ref, 'end');
    }
  }

  async function saveAllToVault() {
    if (!remoteEntries.length || !formatSelection || !outputProfile) return;
    setSavingAll(true);
    try {
      await api.createBatch({
        source_url: remoteEntries[0].ref.url || '',
        source_title: collection.name,
        format_selection: formatSelection,
        output_profile: outputProfile,
        entries: remoteEntries.map((entry) => ({
          source_url: entry.ref.url || '',
          extractor: entry.ref.provider || undefined,
          remote_id: entry.ref.remote_id || undefined,
          title: entry.title || undefined,
          thumbnail: entry.artwork_url || undefined,
          uploader: entry.uploader || undefined,
          duration: entry.duration || undefined,
        })),
      });
      setSavedAllMessage(`${remoteEntries.length} sources sent to your vault. See Downloads for progress.`);
      setError(null);
    } catch (failure) {
      setError('These sources could not be saved to your vault. Try again.');
    } finally {
      if (mounted.current) setSavingAll(false);
    }
  }

  const count = collection.item_count;
  const updated = relativeAge(collection.updated_at);
  const header = onBack ? (
    <>
      <Button onClick={onBack} variant="quiet">Collections</Button>
      <Masthead
        actions={owner ? <>
          {collection.rules && onEditRules ? <Button onClick={onEditRules}>Edit rules</Button> : null}
          {!collection.rules && entries.length ? <Button icon={<Play />} onClick={() => void playAll()}>Play all</Button> : null}
          {onRename ? <Button onClick={() => setRenaming(collection.name)}>Rename</Button> : null}
          {onDelete ? <Button onClick={() => setDeleting(true)} variant="danger">Delete</Button> : null}
        </> : null}
        kicker={collection.rules ? 'Smart collection' : 'Collection'}
        meta={`${count} ${count === 1 ? 'title' : 'titles'}${updated ? ` · updated ${updated}` : ''}`}
        title={collection.name}
      />
      {renaming !== null ? (
        <Dialog
          footer={<><Button onClick={() => setRenaming(null)}>Cancel</Button><Button disabled={!renaming.trim()} form="rename-collection-form" type="submit" variant="primary">Save</Button></>}
          onClose={() => setRenaming(null)}
          open
          size="sm"
          title="Rename"
        >
          <form id="rename-collection-form" onSubmit={(event) => { event.preventDefault(); const next = renaming.trim(); if (next) { setRenaming(null); void act(() => onRename?.(next)); } }}>
            <Field label="Name">{(ids) => <Input {...fieldProps(ids)} maxLength={255} onChange={(event) => setRenaming(event.target.value)} value={renaming} />}</Field>
          </form>
        </Dialog>
      ) : null}
      <ConfirmDialog
        body="The titles and videos in it stay in your library."
        confirmLabel="Delete"
        danger
        onCancel={() => setDeleting(false)}
        onConfirm={() => { setDeleting(false); void act(onDelete); }}
        open={deleting}
        title={`Delete \u201c${collection.name}\u201d?`}
      />
    </>
  ) : null;

  // Smart collections are evaluated per viewer on the server (ADR 0003): no manual order, add or remove.
  if (collection.rules) {
    // Channel_video rules match library videos, not titles.
    const channelVideos = collection.rules.type === 'channel_video';
    return (
      <div className="collection-detail">
        {header}
        <ul aria-label="Rules" className="g-chips g-rule-chips">{collection.rules.conditions.map((condition, index) => <li className="g-chip" key={index}>{ruleChipLabel(condition)}</li>)}</ul>
        {channelVideos
          ? (collection.items?.length
            ? <div className="g-results-grid" onKeyDown={moveFocus}>{collection.items.map((item, index) => <StillCard item={item} key={item.id} kind="video" onPlay={(played) => onOpenLibrary(played.id)} priority={index < 8 ? 1 : 2} shape="still" sizes="280px" />)}</div>
            : <p>No titles match these rules for you yet.</p>)
          : (collection.titles?.length
            ? <div className="g-results-grid" onKeyDown={moveFocus}>{collection.titles.map((title, index) => <PosterCard key={title.id} onOpen={onOpenTitle} priority={index < 12 ? 1 : 2} sizes="160px" title={title} />)}</div>
            : <p>No titles match these rules for you yet.</p>)}
        {error ? <ErrorState title={error} /> : null}
      </div>
    );
  }

  return (
    <div className="collection-detail">
      {header}
      {entries.length ? (
        <>
          <ol className="collection-entry-list g-list-rows">
            {entries.map((entry, index) => {
              const available = entry.availability === 'available';
              const title = entry.title || (available ? 'Untitled' : 'No longer available');
              const item = entry.ref.kind === 'remote' ? entryAsRemote(entry) : null;
              return (
                <li className={`collection-entry-item g-list-row${available ? '' : ' unavailable'}`} data-collection-entry={entry.id} key={entry.id}>
                  <button aria-label={available ? `Watch ${title}` : `${title}, no longer available`} className="collection-entry-watch" disabled={!available} onClick={() => watch(entry)} type="button">
                    <span className="collection-entry-thumb">{available ? <Artwork alt="" src={entry.artwork_url} /> : <ListVideo aria-hidden="true" />}{entry.duration ? <b>{formatDuration(entry.duration)}</b> : null}</span>
                    <span className="collection-entry-copy"><strong>{title}</strong><small>{available ? entry.uploader || (entry.ref.kind === 'remote' ? 'Public link' : 'Saved to vault') : 'Removed or no longer shared with you'}</small></span>
                  </button>
                  <span className="collection-entry-controls">
                    {available && item ? <button aria-label={`Save ${title} to vault`} className="g-button is-quiet" disabled={isQueueing(item)} onClick={() => onQueueRemote(item)} title="Save to vault" type="button"><ArrowDownToLine />{isQueueing(item) ? 'Saving…' : 'Save to vault'}</button> : null}
                    {owner ? <>
                      <button aria-label={`Move ${title} up`} data-direction="up" disabled={busy.has(entry.id) || index === 0} onClick={() => void move(entry, index - 1, 'up')} type="button"><ChevronUp /></button>
                      <button aria-label={`Move ${title} down`} data-direction="down" disabled={busy.has(entry.id) || index === entries.length - 1} onClick={() => void move(entry, index + 1, 'down')} type="button"><ChevronDown /></button>
                      <button aria-label={`Remove ${title} from collection`} disabled={busy.has(entry.id)} onClick={() => void mutate(entry.id, () => api.removeEntry(collection.id, entry.id, collection.revision))} type="button"><X /></button>
                    </> : null}
                  </span>
                </li>
              );
            })}
          </ol>
          <div className="collection-entry-actions">
            {onBack ? null : <button className="g-button" onClick={() => void playAll()} type="button"><Play />Play all</button>}
            {remoteEntries.length > 1 && formatSelection && outputProfile ? <button className="g-button" disabled={savingAll} onClick={() => void saveAllToVault()} type="button"><ArrowDownToLine />{savingAll ? 'Saving…' : `Save ${remoteEntries.length} to vault`}</button> : null}
          </div>
          {savedAllMessage ? <p role="status">{savedAllMessage}</p> : null}
        </>
      ) : <p>No titles yet.</p>}
      {owner ? (
        <form className="collection-add-link" onSubmit={(event) => void addLink(event)}>
          <label><span className="sr-only">Add a public link to {collection.name}</span><input className="g-input" inputMode="url" onChange={(event) => setLinkUrl(event.target.value)} placeholder="Paste a public video link" type="url" value={linkUrl} /></label>
          <button className="g-button" disabled={!linkUrl.trim() || busy.has('add-link')} type="submit">Add link</button>
        </form>
      ) : null}
      {error ? <ErrorState title={error} /> : null}
    </div>
  );
}
