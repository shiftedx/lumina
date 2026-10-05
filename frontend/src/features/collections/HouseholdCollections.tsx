import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Eye, EyeOff, FolderHeart, Plus, Sparkles, Trash2 } from 'lucide-react';

import {
  ApiRequestError,
  addHouseholdCollectionItem,
  createHouseholdCollection,
  deleteHouseholdCollection,
  getHouseholdCollection,
  listHouseholdCollections,
  renameHouseholdCollection,
  setHouseholdCollectionVisibility,
} from '../../api';
import type { CollectionVisibility, FormatSelection, HouseholdCollection, LibraryItem, OutputProfile, TitleSummary, YouTubeSearchResult } from '../../types';
import { Button, Dialog, EmptyState, ErrorState, Field, Input, Masthead, SegmentedControl, Skeleton, fieldProps } from '../../ui';
import { GalleryArt } from '../gallery/GalleryArt';
import { cardColour } from '../gallery/galleryModel';
import { useEnrichment } from '../watch/TranscriptPanel';
import { CollectionDetail } from './CollectionDetail';
import { SmartCollectionBuilder } from './SmartCollectionBuilder';
import './collections.css';

type CollectionApi = {
  list: typeof listHouseholdCollections;
  get: typeof getHouseholdCollection;
  create: typeof createHouseholdCollection;
  rename: typeof renameHouseholdCollection;
  visibility: typeof setHouseholdCollectionVisibility;
  remove: typeof deleteHouseholdCollection;
  addItem: typeof addHouseholdCollectionItem;
};


const collectionApi: CollectionApi = {
  list: listHouseholdCollections,
  get: getHouseholdCollection,
  create: createHouseholdCollection,
  rename: renameHouseholdCollection,
  visibility: setHouseholdCollectionVisibility,
  remove: deleteHouseholdCollection,
  addItem: addHouseholdCollectionItem,
};

export function safeCollectionMutationError(error: unknown): string {
  if (error instanceof ApiRequestError && (error.status === 403 || error.status === 404)) return 'Collection access changed. The latest household view is shown.';
  return 'The collection could not be updated. Refresh and try again.';
}

const COLLECTIONS_OPEN_BY_DEFAULT = 4;
const COLLECTION_CHOICES_MAX = 200;

export function HouseholdCollectionsPanel({
  currentUserId,
  library,
  api = collectionApi,
  onOpenLibrary,
  onOpenRemote,
  onQueueRemote,
  isQueueing,
  onOpenTitle,
  formatSelection,
  outputProfile,
}: {
  currentUserId: string;
  library: LibraryItem[];
  api?: CollectionApi;
  onOpenLibrary?: (libraryItemId: string) => void;
  onOpenRemote?: (item: YouTubeSearchResult) => void;
  onQueueRemote?: (item: YouTubeSearchResult) => void;
  isQueueing?: (item: YouTubeSearchResult) => boolean;
  onOpenTitle?: (title: TitleSummary) => void;
  formatSelection?: FormatSelection;
  outputProfile?: OutputProfile;
}) {
  const mounted = useRef(true);
  const [collections, setCollections] = useState<HouseholdCollection[]>([]);
  const [name, setName] = useState('');
  const [visibility, setVisibility] = useState<CollectionVisibility>('private');
  const [selectedItems, setSelectedItems] = useState<Record<string, string>>({});
  const [renameDrafts, setRenameDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  // Only open rows carry entries; a large household lists summaries and fetches a detail on first open.
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const detailed = useRef(new Set<string>());
  const [building, setBuilding] = useState(false);
  const enrichment = useEnrichment(building);

  const load = useCallback(async () => {
    try {
      const summaries = await api.list();
      const opened = summaries.slice(0, COLLECTIONS_OPEN_BY_DEFAULT);
      const details = await Promise.all(opened.map((collection) => api.get(collection.id)));
      if (!mounted.current) return;
      detailed.current = new Set(details.map((collection) => collection.id));
      setOpen(new Set(detailed.current));
      setCollections([...details, ...summaries.slice(details.length)]);
      setError(null);
    } catch (failure) {
      if (!mounted.current) return;
      setError('Household collections are unavailable. Refresh after confirming the active member.');
    }
  }, [api]);
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; }; }, [load]);

  async function toggle(id: string, isOpen: boolean) {
    setOpen((current) => { const next = new Set(current); if (isOpen) next.add(id); else next.delete(id); return next; });
    if (!isOpen || detailed.current.has(id)) return;
    try {
      const detail = await api.get(id);
      if (!mounted.current) return;
      detailed.current.add(id);
      setCollections((current) => current.map((collection) => collection.id === id ? detail : collection));
    } catch (failure) {
      if (mounted.current) setError(safeCollectionMutationError(failure));
    }
  }

  useEffect(() => setRenameDrafts(Object.fromEntries(collections.map((collection) => [collection.id, collection.name]))), [collections]);

  async function mutate(id: string, action: () => Promise<HouseholdCollection | void>): Promise<boolean> {
    setBusy((current) => new Set(current).add(id));
    try {
      const result = await action();
      if (!mounted.current) return false;
      if (result) { detailed.current.add(id); setCollections((current) => current.map((collection) => collection.id === id ? result : collection)); }
      else setCollections((current) => current.filter((collection) => collection.id !== id));
      setError(null);
      return true;
    } catch (failure) {
      if (!mounted.current) return false;
      const message = safeCollectionMutationError(failure);
      if (failure instanceof ApiRequestError && (failure.status === 403 || failure.status === 404)) await load();
      setError(message);
      return false;
    } finally {
      if (mounted.current) setBusy((current) => { const next = new Set(current); next.delete(id); return next; });
    }
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy((current) => new Set(current).add('create'));
    try {
      const created = await api.create({ name: name.trim(), visibility });
      if (!mounted.current) return;
      detailed.current.add(created.id);
      setOpen((current) => new Set(current).add(created.id));
      setCollections((current) => [...current, created]);
      setName('');
      setError(null);
    } catch (failure) {
      if (mounted.current) setError(safeCollectionMutationError(failure));
    } finally { if (mounted.current) setBusy((current) => { const next = new Set(current); next.delete('create'); return next; }); }
  }

  return <section aria-labelledby="household-collections-title" className="household-collections">
    <header><div><h2 id="household-collections-title">Household collections</h2><p>Group visible titles without changing who can open the originals.</p></div></header>
    <form className="collection-create" onSubmit={(event) => void create(event)}><label><span>New collection name</span><input className="g-input" maxLength={255} onChange={(event) => setName(event.target.value)} placeholder="Weekend watchlist" value={name} /></label><label><span>Who can see it</span><select className="g-input" onChange={(event) => setVisibility(event.target.value as CollectionVisibility)} value={visibility}><option value="private">Only me</option><option value="shared">Household</option></select></label><button className="g-button is-primary" disabled={!name.trim() || busy.has('create')} type="submit"><Plus />Create</button></form>
    {building
      ? <SmartCollectionBuilder aiAvailable={Boolean(enrichment?.ai_summaries)} create={api.create} mode="new" onCancel={() => setBuilding(false)} open onCreated={(created) => { detailed.current.add(created.id); setOpen((current) => new Set(current).add(created.id)); setCollections((current) => [...current, created]); setBuilding(false); }} />
      : <button className="g-button" onClick={() => setBuilding(true)} type="button"><Sparkles aria-hidden="true" /> New smart collection</button>}
    {!collections.length ? <p className="workspace-empty">Create a collection, then add anything visible in your library.</p> : <div className="collection-list">{collections.map((collection) => {
      const owner = collection.owner_user_id === currentUserId;
      const isOpen = open.has(collection.id);
      const members = new Set(collection.items.map((member) => member.id));
      // the newest loaded titles only; add a search picker if members need to reach older ones here.
      const available = isOpen && owner && !collection.rules ? library.filter((item) => !members.has(item.id)).slice(0, COLLECTION_CHOICES_MAX) : [];
      return <article className="collection-row" key={collection.id}>
        <header><div><FolderHeart /><span><strong>{collection.name}</strong><small>{collection.rules ? 'Smart collection' : owner ? collection.visibility === 'shared' ? 'Your household collection' : 'Your private collection' : 'Shared by another household member'} · {collection.item_count} {collection.item_count === 1 ? 'title' : 'titles'}</small></span></div>{owner ? <div><button aria-label={`Make ${collection.name} ${collection.visibility === 'shared' ? 'private' : 'shared'}`} className="g-icon-button" disabled={busy.has(collection.id)} onClick={() => void mutate(collection.id, () => api.visibility(collection.id, collection.visibility === 'shared' ? 'private' : 'shared'))} title={collection.visibility === 'shared' ? 'Make private' : 'Share with household'} type="button">{collection.visibility === 'shared' ? <Eye /> : <EyeOff />}</button><button aria-label={`Delete ${collection.name}`} className="g-icon-button" disabled={busy.has(collection.id)} onClick={() => void mutate(collection.id, () => api.remove(collection.id))} type="button"><Trash2 /></button></div> : null}</header>
        {owner ? <label className="collection-rename"><span className="sr-only">Rename {collection.name}</span><input className="g-input" disabled={busy.has(collection.id)} onBlur={async () => { const next = (renameDrafts[collection.id] || '').trim(); if (!next || next === collection.name) { setRenameDrafts((current) => ({ ...current, [collection.id]: collection.name })); return; } const updated = await mutate(collection.id, () => api.rename(collection.id, next)); if (!updated) setRenameDrafts((current) => ({ ...current, [collection.id]: collection.name })); }} onChange={(event) => setRenameDrafts((current) => ({ ...current, [collection.id]: event.target.value }))} value={renameDrafts[collection.id] ?? collection.name} /></label> : null}
        <details className="collection-titles" onToggle={(event) => { const next = event.currentTarget.open; if (next !== isOpen) void toggle(collection.id, next); }} open={isOpen}>
          <summary className="g-text-button">{isOpen ? 'Hide titles' : 'Show titles'}</summary>
          {isOpen && detailed.current.has(collection.id) ? <CollectionDetail collection={collection} formatSelection={formatSelection} isQueueing={isQueueing} onChanged={(updated) => setCollections((current) => current.map((entry) => entry.id === updated.id ? updated : entry))} onOpenLibrary={onOpenLibrary} onOpenRemote={onOpenRemote} onOpenTitle={onOpenTitle} onQueueRemote={onQueueRemote} onReload={() => void load()} outputProfile={outputProfile} owner={owner} /> : null}
          {isOpen && !detailed.current.has(collection.id) ? <p className="workspace-empty" role="status">Loading titles…</p> : null}
        </details>
        {available.length ? <div className="collection-add"><select className="g-input" aria-label={`Choose a title for ${collection.name}`} onChange={(event) => setSelectedItems((current) => ({ ...current, [collection.id]: event.target.value }))} value={selectedItems[collection.id] || ''}><option value="">Choose from your visible library</option>{available.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select><button className="g-button" disabled={!selectedItems[collection.id] || busy.has(collection.id)} onClick={() => { const itemId = selectedItems[collection.id]; if (itemId) void (async () => { const updated = await mutate(collection.id, () => api.addItem(collection.id, itemId)); if (updated) setSelectedItems((current) => ({ ...current, [collection.id]: '' })); })(); }} type="button"><Plus />Add</button></div> : null}
      </article>;
    })}</div>}
    {error ? <div className="inline-workspace-error" role="alert"><span>{error}</span><button className="g-text-button" onClick={() => void load()} type="button">Refresh</button></div> : null}
  </section>;
}


export type CollectionsRoute = { collections: 'list'; create?: 'collection' | 'smart' } | { collections: 'detail'; collectionId: string };

type CollectionsPageProps = {
  currentUserId: string;
  library: LibraryItem[];
  api?: CollectionApi;
  route: CollectionsRoute;
  /** The All landing's chapter: tiles and a See all link only. */
  embedded?: boolean;
  /** The Library lens row, shown first on the full page. */
  lenses?: ReactNode;
  onOpenCollection: (id: string) => void;
  onBackToList: () => void;
  /** Called once after `?new=` opened a form, so the address can drop it. */
  onRouteHandled: () => void;
  onOpenLibrary?: (libraryItemId: string) => void;
  onOpenRemote?: (item: YouTubeSearchResult) => void;
  onQueueRemote?: (item: YouTubeSearchResult) => void;
  isQueueing?: (item: YouTubeSearchResult) => boolean;
  onOpenTitle?: (title: TitleSummary) => void;
  formatSelection?: FormatSelection;
  outputProfile?: OutputProfile;
};

const TILES_EMBEDDED = 6;

function CollectionTile({ collection, onOpen }: { collection: HouseholdCollection; onOpen: (id: string) => void }) {
  const posters = (collection.titles ?? []).slice(0, 4);
  const label = collection.rules ? `${collection.titles?.length ?? collection.item_count} titles · Smart` : `${collection.item_count} items`;
  return (
    <a
      className="g-collection-tile"
      href={`/library/collections/${encodeURIComponent(collection.id)}`}
      onClick={(event) => { if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return; event.preventDefault(); onOpen(collection.id); }}
    >
      <span className={`g-mosaic is-${Math.max(posters.length, 1)}`}>
        {posters.length
          ? posters.map((title) => <GalleryArt alt="" art={title.poster} card={{ name: title.name, year: title.year }} colour={cardColour(title, 'poster')} key={title.id} kind="poster" priority={2} sizes="120px" />)
          : <GalleryArt alt="" art={null} card={{ name: collection.name }} colour={cardColour({ id: collection.id, poster: null, backdrop: null }, 'still')} kind="still" priority={2} sizes="280px" />}
      </span>
      <span className="g-caption-title">{collection.name}</span>
      <span className="g-label">{label}</span>
    </a>
  );
}

function NewCollectionDialog({ error, onClose, onCreate }: { error?: string | null; onClose: () => void; onCreate: (body: { name: string; visibility: CollectionVisibility }) => Promise<void> }) {
  const [name, setName] = useState('');
  const [visibility, setVisibility] = useState<CollectionVisibility>('private');
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    await onCreate({ name: name.trim(), visibility });
    setBusy(false);
  }
  return (
    <Dialog busy={busy} footer={<><Button onClick={onClose}>Cancel</Button><Button busy={busy} disabled={!name.trim()} form="new-collection-form" type="submit" variant="primary">Create</Button></>} onClose={onClose} open size="sm" title="New collection">
      <form id="new-collection-form" onSubmit={(event) => void submit(event)}>
        <Field label="Name">{(ids) => <Input {...fieldProps(ids)} maxLength={255} onChange={(event) => setName(event.target.value)} placeholder="Weekend watchlist" value={name} />}</Field>
        <SegmentedControl legend="Who can see it" onChange={setVisibility} options={[{ value: 'private', label: 'Only me' }, { value: 'shared', label: 'Household' }]} value={visibility} />
        {error ? <p role="alert">{error}</p> : null}
      </form>
    </Dialog>
  );
}

/** The Collections Library page: a poster-mosaic tile per collection, the detail by route. */
export function CollectionsPage({
  route, embedded = false, lenses, onOpenCollection, onBackToList, onRouteHandled, api = collectionApi, currentUserId, library: _library,
  onOpenLibrary, onOpenRemote, onQueueRemote, isQueueing, onOpenTitle, formatSelection, outputProfile,
}: CollectionsPageProps) {
  const mounted = useRef(true);
  const [collections, setCollections] = useState<HouseholdCollection[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [creating, setCreating] = useState(false);
  const [building, setBuilding] = useState(false);
  const [editing, setEditing] = useState(false);
  const [detail, setDetail] = useState<HouseholdCollection | null>(null);
  const [detailFailed, setDetailFailed] = useState(false);
  const [detailTry, setDetailTry] = useState(0);
  const [createError, setCreateError] = useState<string | null>(null);
  const enrichment = useEnrichment(building || editing);

  const load = useCallback(async () => {
    try {
      const list = await api.list();
      if (!mounted.current) return;
      setCollections(list);
      setFailed(false);
    } catch {
      if (mounted.current) setFailed(true);
    } finally {
      if (mounted.current) setRetrying(false);
    }
  }, [api]);
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; }; }, [load]);

  const create = route.collections === 'list' ? route.create : undefined;
  useEffect(() => {
    if (embedded || !create) return;
    if (create === 'smart') setBuilding(true); else setCreating(true);
    onRouteHandled();
  }, [create, embedded]);

  const detailId = !embedded && route.collections === 'detail' ? route.collectionId : null;
  const known = detailId ? collections?.find((entry) => entry.id === detailId) : undefined;
  useEffect(() => {
    setDetailFailed(false);
    if (!detailId || !known) { setDetail(null); return undefined; }
    let live = true;
    api.get(detailId).then((full) => { if (live) setDetail(full); }, () => { if (live) setDetailFailed(true); });
    return () => { live = false; };
  }, [detailId, Boolean(known), api, detailTry]);

  const added = useMemo(() => (made: HouseholdCollection) => { setCollections((current) => [...(current ?? []), made]); }, []);

  async function createPlain(body: { name: string; visibility: CollectionVisibility }) {
    setCreateError(null);
    try {
      const made = await api.create(body);
      if (!mounted.current) return;
      added(made);
      setCreateError(null);
      setCreating(false);
    } catch (failure) {
      if (mounted.current) setCreateError(safeCollectionMutationError(failure));
    }
  }

  const all = collections ?? [];
  const tiles = embedded ? all.slice(0, TILES_EMBEDDED) : all;
  const actions = <><Button onClick={() => setCreating(true)} variant="primary">New collection</Button><Button icon={<Sparkles />} onClick={() => setBuilding(true)}>New smart collection</Button></>;

  if (detailId && known && detail?.id === detailId) {
    return (
      <div className="gallery g-collections-page">
        {lenses}
        <CollectionDetail
          collection={detail}
          onBack={onBackToList}
          onEditRules={() => setEditing(true)}
          onDelete={async () => { await api.remove(detail.id); setCollections((current) => (current ?? []).filter((entry) => entry.id !== detail.id)); onBackToList(); }}
          onRename={async (name) => { const renamed = await api.rename(detail.id, name); setDetail(renamed); setCollections((current) => (current ?? []).map((entry) => (entry.id === renamed.id ? { ...entry, name: renamed.name } : entry))); }}
          formatSelection={formatSelection}
          isQueueing={isQueueing}
          onChanged={(updated) => { setDetail(updated); setCollections((current) => (current ?? []).map((entry) => (entry.id === updated.id ? updated : entry))); }}
          onOpenLibrary={onOpenLibrary}
          onOpenRemote={onOpenRemote}
          onOpenTitle={onOpenTitle}
          onQueueRemote={onQueueRemote}
          onReload={() => void load()}
          outputProfile={outputProfile}
          owner={detail.owner_user_id === currentUserId}
        />
        {editing ? <SmartCollectionBuilder aiAvailable={Boolean(enrichment?.ai_summaries)} collection={detail} mode="edit" onCancel={() => setEditing(false)} onCreated={(updated) => { setDetail(updated); setCollections((current) => (current ?? []).map((entry) => (entry.id === updated.id ? updated : entry))); setEditing(false); }} open /> : null}
      </div>
    );
  }

  return (
    <div className={`gallery g-collections-page${embedded ? ' is-embedded' : ''}`}>
      {embedded ? null : lenses}
      {embedded ? null : <Masthead actions={actions} title="Collections" />}
      {failed ? <ErrorState onRetry={() => { setRetrying(true); void load(); }} retrying={retrying} title="Lumina could not load your collections." />
        : detailId && known && detailFailed ? <ErrorState onRetry={() => setDetailTry((n) => n + 1)} title="Lumina could not load this collection." />
        : collections === null || (detailId && known && detail?.id !== detailId) ? <Skeleton count={embedded ? 3 : 6} label="Loading collections…" shape="still" />
          : all.length === 0 ? <EmptyState action={embedded ? undefined : actions} body="Group titles your household loves, or let a smart collection gather them." size={embedded ? 'inline' : 'page'} title="No collections yet." />
            : <ul className="g-collection-grid">{tiles.map((entry) => <li key={entry.id}><CollectionTile collection={entry} onOpen={onOpenCollection} /></li>)}</ul>}
      {embedded && all.length ? <a className="g-collections-all" href="/library/collections" onClick={(event) => { if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return; event.preventDefault(); onBackToList(); }}>See all</a> : null}
      {creating ? <NewCollectionDialog error={createError} onClose={() => { setCreating(false); setCreateError(null); }} onCreate={createPlain} /> : null}
      {building ? <SmartCollectionBuilder aiAvailable={Boolean(enrichment?.ai_summaries)} create={api.create} mode="new" onCancel={() => setBuilding(false)} onCreated={(made) => { added(made); setBuilding(false); }} open /> : null}
    </div>
  );
}
