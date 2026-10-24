import { defaultRangeExtractor, useWindowVirtualizer } from '@tanstack/react-virtual';
import { CalendarDays, Camera, Check, FolderPlus, Heart, ImagePlus, Images, Search, Trash2, Undo2, Upload, UserRound, X } from 'lucide-react';
import { lazy, type KeyboardEvent, type ReactNode, Suspense, useCallback, useDeferredValue, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { downloadLocalModel } from '../../api';
import type { UserProfile } from '../../types';
import { Button, Checkbox, ConfirmDialog, Dialog, EmptyState, ErrorState, IconButton, Input, Select, Skeleton, Switch, TextButton, useToast } from '../../ui';
import { resolveArtworkUrl } from '../../Artwork';
import { GalleryArt } from '../gallery/GalleryArt';
import type { LoadPriority } from '../gallery/imageLoader';
import type { PhotosRoute } from './routes';
import {
  addPhotoToAlbum, createPhotoAlbum, deletePhotoAlbum, deletePhotoFaceData, getFaceSettings, listPhotoAlbums, listPhotoPeople, listPhotos, mergePhotoPeople, removePhotoFromAlbum, restorePhoto,
  scanPhotoFaces, updateFaceSettings, updatePhoto, updatePhotoAlbum, updatePhotoPerson, uploadPhoto,
  type FaceSettings, type Photo, type PhotoAlbum, type PhotoPerson, type PhotoQuery,
} from './photoApi';
import { packPhotoRows, photoDay, type PhotoRow } from './photoLayout';
import './photos.css';

const PhotoViewer = lazy(() => import('./PhotoViewer'));
const PhotoSources = lazy(() => import('./PhotoSources').then((module) => ({ default: module.PhotoSources })));
const PAGE_SIZE = 100;
type Feed = { key: string; items: Photo[]; total: number; cursor: string | null; loading: boolean; error: string | null };
type TimelineItem = { type: 'day'; key: string; label: string } | { type: 'row'; key: string; row: PhotoRow };

function usePhotoFeed(query: PhotoQuery, enabled = true) {
  const key = JSON.stringify(query);
  const [state, setState] = useState<Feed>({ key: '', items: [], total: 0, cursor: null, loading: false, error: null });
  const serial = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const activeKey = useRef<string | null>(null);
  activeKey.current = enabled ? key : null;
  const load = useCallback((cursor: string | null) => {
    if (activeKey.current !== key) return;
    const id = ++serial.current;
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    setState((current) => ({ ...current, key, items: cursor ? current.items : [], loading: true, error: null }));
    void listPhotos({ ...query, cursor, limit: PAGE_SIZE }, nextController.signal).then((page) => {
      if (id !== serial.current || activeKey.current !== key) return;
      setState((current) => ({ key, items: cursor ? [...current.items, ...page.items] : page.items, total: page.total, cursor: page.next_cursor, loading: false, error: null }));
    }, (failure: unknown) => {
      if (id !== serial.current || nextController.signal.aborted || activeKey.current !== key) return;
      setState((current) => ({ ...current, loading: false, error: failure instanceof Error ? failure.message : 'Lumina could not load these photos.' }));
    });
  }, [key]);
  useEffect(() => { if (enabled) load(null); return () => controller.current?.abort(); }, [enabled, load]);
  const current = state.key === key ? state : { key, items: [], total: 0, cursor: null, loading: true, error: null };
  const patch = (photo: Photo) => setState((value) => ({ ...value, items: value.items.map((item) => item.id === photo.id ? photo : item) }));
  return { ...current, patch, reload: () => load(null), retry: () => load(null), loadMore: () => { if (current.cursor && !current.loading) load(current.cursor); } };
}

function useElementWidth(ref: React.RefObject<HTMLElement | null>) {
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    const measure = () => setWidth(node.clientWidth);
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(node);
    window.addEventListener('resize', measure);
    return () => { observer?.disconnect(); window.removeEventListener('resize', measure); };
  });
  return width;
}

function groupTimeline(photos: Photo[], width: number, target: number, gap: number): TimelineItem[] {
  const groups = new Map<string, Photo[]>();
  photos.forEach((photo) => { const day = photoDay(photo); const group = groups.get(day); if (group) group.push(photo); else groups.set(day, [photo]); });
  return [...groups].flatMap(([label, entries]) => [
    { type: 'day' as const, key: `day:${label}`, label },
    ...packPhotoRows(entries, width, target, gap).map((row) => ({ type: 'row' as const, key: `row:${row.key}`, row })),
  ]);
}

function PhotoTile({ photo, index, width, height, priority, position, onOpen, onFavorite, onDelete, onRestore }: {
  photo: Photo; index: number; width: number; height: number; priority: LoadPriority; position: number;
  onOpen: (photo: Photo) => void; onFavorite: (photo: Photo) => void; onDelete?: (photo: Photo) => void; onRestore?: (photo: Photo) => void;
}) {
  const label = photo.title || photo.filename;
  return (
    <figure className="ph-tile" data-photo-id={photo.id} data-testid="photo-tile" style={{ width, height }}>
      <button aria-label={`View ${label}`} className="ph-image-button" data-photo-index={index} onClick={() => onOpen(photo)} type="button">
        <GalleryArt
          alt=""
          art={{ url: photo.thumbnail_url, rendition: `/api/photos/${photo.id}/image?width={w}`, widths: [400, 1200, 2400].filter((candidate) => candidate <= photo.width || candidate === 400), width: photo.width, height: photo.height }}
          card={{ name: label }}
          colour={{ colour: 'var(--g-paper-2)', fromPalette: false }}
          kind="still"
          position={position}
          priority={priority}
          sizes={`${Math.ceil(width)}px`}
        />
        <span className="ph-tile-name">{label}</span>
      </button>
      <div className="ph-tile-actions">
        <IconButton icon={<Heart fill={photo.favorite ? 'currentColor' : 'none'} />} label={photo.favorite ? `Remove ${label} from favorites` : `Add ${label} to favorites`} onClick={() => onFavorite(photo)} pressed={photo.favorite} variant="overlay" />
        {onDelete ? <IconButton icon={<Trash2 />} label={`Move ${label} to trash`} onClick={() => onDelete(photo)} variant="overlay" /> : null}
        {onRestore ? <IconButton icon={<Undo2 />} label={`Restore ${label}`} onClick={() => onRestore(photo)} variant="overlay" /> : null}
      </div>
    </figure>
  );
}

function PhotoTimeline({ photos, total, cursor, loading, error, density, onDensity, onLoadMore, onRetry, onPatch, onReload, trash = false }: {
  photos: Photo[]; total: number; cursor: string | null; loading: boolean; error: string | null; density: number; onDensity: (value: number) => void;
  onLoadMore: () => void; onRetry: () => void; onPatch: (photo: Photo) => void; onReload: () => void; trash?: boolean;
}) {
  const toast = useToast();
  const measure = useRef<HTMLDivElement>(null);
  const width = useElementWidth(measure);
  const gap = width > 0 && width < 600 ? 4 : 8;
  const items = useMemo(() => groupTimeline(photos, width, density, gap), [photos, width, density, gap]);
  const photoIndexes = useMemo(() => new Map(photos.map((photo, index) => [photo.id, index])), [photos]);
  const rowByPhoto = useMemo(() => new Map(items.flatMap((item, index) => item.type === 'row' ? item.row.photos.map((photo) => [photo.id, index] as const) : [])), [items]);
  const [viewer, setViewer] = useState<string | null>(null);
  const [focused, setFocused] = useState<string | null>(null);
  const pendingFocus = useRef<string | null>(null);
  const top = measure.current ? measure.current.offsetTop : 0;
  const virtualizer = useWindowVirtualizer({
    count: items.length,
    estimateSize: (index) => items[index]?.type === 'day' ? 52 : (items[index]?.row.height ?? density) + gap,
    getItemKey: (index) => items[index]?.key ?? index,
    overscan: 4,
    scrollMargin: top,
    rangeExtractor: (range) => {
      const indexes = defaultRangeExtractor(range);
      const keep = focused ? rowByPhoto.get(focused) : undefined;
      return keep === undefined || indexes.includes(keep) ? indexes : [...indexes, keep].sort((a, b) => a - b);
    },
  });
  const virtualItems = virtualizer.getVirtualItems();
  useEffect(() => { if (cursor && virtualItems.at(-1)?.index === items.length - 1) onLoadMore(); }, [cursor, items.length, onLoadMore, virtualItems.at(-1)?.index]);
  useLayoutEffect(() => {
    if (!pendingFocus.current) return;
    const target = measure.current?.querySelector<HTMLElement>(`[data-photo-id="${CSS.escape(pendingFocus.current)}"] .ph-image-button`);
    if (target) { target.focus({ preventScroll: true }); pendingFocus.current = null; }
  });
  const changeFavorite = async (photo: Photo) => {
    try { onPatch(await updatePhoto(photo.id, { favorite: !photo.favorite })); }
    catch (failure) { toast({ tone: 'error', message: failure instanceof Error ? failure.message : 'Lumina could not update that photo.' }); }
  };
  const remove = async (photo: Photo) => {
    try { onPatch(await updatePhoto(photo.id, { deleted: true })); onReload(); toast({ tone: 'success', message: `Moved “${photo.title || photo.filename}” to Trash.` }); }
    catch (failure) { toast({ tone: 'error', message: failure instanceof Error ? failure.message : 'Lumina could not move that photo.' }); }
  };
  const restore = async (photo: Photo) => {
    try { onPatch(await restorePhoto(photo.id)); onReload(); toast({ tone: 'success', message: `Restored “${photo.title || photo.filename}”.` }); }
    catch (failure) { toast({ tone: 'error', message: failure instanceof Error ? failure.message : 'Lumina could not restore that photo.' }); }
  };
  const moveFocus = (event: KeyboardEvent<HTMLDivElement>) => {
    const tile = (event.target as HTMLElement).closest<HTMLElement>('[data-photo-id]');
    if (!tile || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    const current = photoIndexes.get(tile.dataset.photoId ?? '');
    if (current === undefined) return;
    const step = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : event.key === 'ArrowUp' ? -4 : event.key === 'ArrowDown' ? 4 : 0;
    const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? photos.length - 1 : Math.max(0, Math.min(photos.length - 1, current + step));
    const next = photos[nextIndex];
    const row = rowByPhoto.get(next.id);
    if (row === undefined) return;
    event.preventDefault();
    setFocused(next.id);
    pendingFocus.current = next.id;
    virtualizer.scrollToIndex(row, { align: 'auto' });
  };
  const closeViewer = () => {
    const id = viewer;
    setViewer(null);
    if (!id) return;
    const row = rowByPhoto.get(id);
    if (row !== undefined) virtualizer.scrollToIndex(row, { align: 'auto' });
    pendingFocus.current = id;
    requestAnimationFrame(() => {
      measure.current?.querySelector<HTMLElement>(`[data-photo-id="${CSS.escape(id)}"] .ph-image-button`)?.focus({ preventScroll: true });
    });
  };
  if (!photos.length && loading) return <div ref={measure}><Skeleton count={8} label="Loading photos" shape="block" /></div>;
  if (!photos.length && error) return <div ref={measure}><ErrorState body={error} onRetry={onRetry} title="Photos are unavailable" /></div>;
  if (!photos.length) return <div ref={measure}><EmptyState body={trash ? 'Photos moved here can be restored.' : 'Import photos to begin a private, searchable household library.'} size="page" title={trash ? 'Trash is empty' : 'Your photo library is ready'} /></div>;
  return (
    <>
      <div className="ph-density">
        <label htmlFor="ph-density">Photo size</label>
        <input aria-label="Photo size" id="ph-density" max="300" min="120" onChange={(event) => onDensity(Number(event.target.value))} step="20" type="range" value={density} />
        <span>{total.toLocaleString()} photo{total === 1 ? '' : 's'}</span>
      </div>
      <div aria-label="Photos timeline" className="ph-timeline" onKeyDown={moveFocus} ref={measure} role="region">
        <div className="ph-virtual" style={{ height: virtualizer.getTotalSize() }}>
          {virtualItems.map((virtual) => {
            const item = items[virtual.index];
            if (!item) return null;
            return (
              <div className={item.type === 'day' ? 'ph-day-row' : 'ph-photo-row'} data-index={virtual.index} key={item.key} ref={virtualizer.measureElement} style={{ transform: `translateY(${virtual.start - top}px)` }}>
                {item.type === 'day' ? <h2>{item.label}</h2> : item.row.photos.map((photo, offset) => (
                  <PhotoTile
                    height={item.row.height}
                    index={photoIndexes.get(photo.id) ?? offset}
                    key={photo.id}
                    onDelete={!trash && photo.can_edit ? remove : undefined}
                    onFavorite={changeFavorite}
                    onOpen={(selected) => { setFocused(selected.id); setViewer(selected.id); }}
                    onRestore={trash && photo.can_edit ? restore : undefined}
                    photo={photo}
                    position={virtual.index * 1000 + offset}
                    priority={virtual.index <= (virtualItems[0]?.index ?? 0) + 2 ? 1 : 2}
                    width={item.row.widths[offset]}
                  />
                ))}
              </div>
            );
          })}
        </div>
        {loading ? <p aria-live="polite" className="ph-loading">Loading more photos…</p> : null}
        {error ? <div className="ph-inline-error" role="alert"><span>{error}</span><TextButton onClick={onLoadMore}>Try again</TextButton></div> : null}
      </div>
      {viewer ? <Suspense fallback={null}><PhotoViewer onClose={closeViewer} onNearEnd={onLoadMore} onPatch={onPatch} photoId={viewer} photos={photos} /></Suspense> : null}
    </>
  );
}

type UploadEntry = { id: string; file: File; state: 'queued' | 'uploading' | 'done' | 'failed'; progress: number; error?: string };
const PHOTO_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/tiff', 'image/heic', 'image/heif', 'image/avif']);
const PHOTO_EXTENSION = /\.(?:jpe?g|png|webp|tiff?|heic|heif|avif)$/i;
const PHOTO_ACCEPT = '.jpg,.jpeg,.png,.webp,.tif,.tiff,.heic,.heif,.avif,image/jpeg,image/png,image/webp,image/tiff,image/heic,image/heif,image/avif';

function ImportDialog({ open, onClose, onImported, user }: { open: boolean; onClose: () => void; onImported: () => void; user: UserProfile }) {
  const [entries, setEntries] = useState<UploadEntry[]>([]);
  const [running, setRunning] = useState(false);
  const [mode, setMode] = useState<'upload' | 'sources'>('upload');
  const [unsupported, setUnsupported] = useState(0);
  const folder = useRef<HTMLInputElement>(null);
  useEffect(() => { folder.current?.setAttribute('webkitdirectory', ''); }, [open]);
  const add = (files: FileList | null) => {
    if (!files) return;
    const selected = [...files];
    const accepted = selected.filter((file) => PHOTO_TYPES.has(file.type.toLowerCase()) || PHOTO_EXTENSION.test(file.name));
    setUnsupported((count) => count + selected.length - accepted.length);
    setEntries((current) => [...current, ...accepted.map((file, index) => ({ id: `${file.name}:${file.size}:${file.lastModified}:${index}`, file, state: 'queued' as const, progress: 0 }))]);
  };
  const uploadEntries = async (ids?: Set<string>) => {
    if (running) return;
    setRunning(true);
    const targets = entries.filter((entry) => ids ? ids.has(entry.id) : entry.state === 'queued' || entry.state === 'failed');
    for (const entry of targets) {
      setEntries((current) => current.map((value) => value.id === entry.id ? { ...value, state: 'uploading', progress: 0, error: undefined } : value));
      try {
        await uploadPhoto(entry.file, ({ progress }) => setEntries((current) => current.map((value) => value.id === entry.id ? { ...value, progress } : value))).request;
        setEntries((current) => current.map((value) => value.id === entry.id ? { ...value, state: 'done', progress: 1 } : value));
        onImported();
      } catch (failure) {
        setEntries((current) => current.map((value) => value.id === entry.id ? { ...value, state: 'failed', error: failure instanceof Error ? failure.message : 'Upload failed.' } : value));
      }
    }
    setRunning(false);
  };
  const failed = entries.filter((entry) => entry.state === 'failed');
  return (
    <Dialog busy={running} footer={<><Button disabled={running} onClick={onClose} variant="quiet">Done</Button>{mode === 'upload' ? <Button busy={running} disabled={!entries.some((entry) => entry.state === 'queued' || entry.state === 'failed')} icon={<Upload />} onClick={() => void uploadEntries()} variant="primary">Import</Button> : null}</>} onClose={onClose} open={open} size="lg" title="Add photos">
      {user.role === 'admin' ? <nav aria-label="Add photos method" className="ph-import-tabs"><button aria-current={mode === 'upload' ? 'page' : undefined} onClick={() => setMode('upload')} type="button">Upload files</button><button aria-current={mode === 'sources' ? 'page' : undefined} onClick={() => setMode('sources')} type="button">Synced folders</button></nav> : null}
      {mode === 'sources' && user.role === 'admin' ? <Suspense fallback={<p aria-busy="true">Loading photo sources…</p>}><PhotoSources onChanged={onImported} /></Suspense> : <div className="ph-import">
        <p>Choose individual images or a folder. Lumina uploads one file at a time and keeps failed files ready to retry.</p>
        <div className="ph-import-pickers">
          <Button icon={<ImagePlus />} onClick={() => document.getElementById('ph-files')?.click()}>Choose photos</Button>
          <Button icon={<FolderPlus />} onClick={() => folder.current?.click()} variant="secondary">Choose folder</Button>
          <input accept={PHOTO_ACCEPT} hidden id="ph-files" multiple onChange={(event) => add(event.target.files)} type="file" />
          <input accept={PHOTO_ACCEPT} hidden multiple onChange={(event) => add(event.target.files)} ref={folder} type="file" />
        </div>
        {entries.length ? <ul className="ph-upload-list">{entries.map((entry) => <li key={entry.id}>
          <span className="ph-upload-icon">{entry.state === 'done' ? <Check /> : entry.state === 'failed' ? <X /> : <Upload />}</span>
          <span><strong>{entry.file.name}</strong>{entry.error ? <small>{entry.error}</small> : null}</span>
          <progress aria-label={`Upload progress for ${entry.file.name}`} max="1" value={entry.progress} />
          {entry.state === 'failed' ? <TextButton onClick={() => void uploadEntries(new Set([entry.id]))}>Retry</TextButton> : null}
        </li>)}</ul> : <div className="ph-drop-note">JPEG, PNG, WebP, TIFF, HEIC, HEIF, and AVIF photos.</div>}
        {unsupported ? <p className="ph-import-warning" role="status">{unsupported.toLocaleString()} file{unsupported === 1 ? '' : 's'} not added because the format is unsupported.</p> : null}
        {failed.length > 1 ? <TextButton onClick={() => void uploadEntries(new Set(failed.map((entry) => entry.id)))}>Retry all failed</TextButton> : null}
      </div>}
    </Dialog>
  );
}

function AlbumView({ route, onRoute }: { route: PhotosRoute; onRoute: (route: PhotosRoute) => void }) {
  const toast = useToast();
  const [albums, setAlbums] = useState<PhotoAlbum[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<PhotoAlbum | 'new' | null>(null);
  const [name, setName] = useState('');
  const [visibility, setVisibility] = useState<'private' | 'shared'>('private');
  const refresh = () => { setError(null); void listPhotoAlbums().then((page) => setAlbums(page.items), (failure: unknown) => setError(failure instanceof Error ? failure.message : 'Lumina could not load albums.')); };
  useEffect(refresh, []);
  const save = async () => {
    if (!name.trim()) return;
    try {
      if (editing === 'new') await createPhotoAlbum({ name: name.trim(), visibility });
      else if (editing) await updatePhotoAlbum(editing.id, { name: name.trim(), visibility });
      setEditing(null); setName(''); refresh();
    } catch (failure) { toast({ tone: 'error', message: failure instanceof Error ? failure.message : 'Lumina could not save that album.' }); }
  };
  if (albums === null && !error) return <Skeleton count={6} label="Loading albums" shape="block" />;
  if (error) return <ErrorState body={error} onRetry={refresh} title="Albums are unavailable" />;
  return <section aria-labelledby="ph-albums-heading"><div className="ph-section-head"><h2 id="ph-albums-heading">Albums</h2><Button icon={<FolderPlus />} onClick={() => { setName(''); setVisibility('private'); setEditing('new'); }} variant="primary">New album</Button></div>
    {!albums?.length ? <EmptyState body="Group moments into private or household albums." title="No albums yet" /> : <div className="ph-collection-grid">{albums.map((album) => <article className="ph-collection" key={album.id}>
      <button className="ph-collection-open" onClick={() => onRoute({ ...route, view: 'timeline', albumId: album.id, personId: undefined })} type="button">
        <span className="ph-collection-cover">{resolveArtworkUrl(album.cover_url) ? <img alt="" src={resolveArtworkUrl(album.cover_url) ?? undefined} /> : <Images />}</span><strong>{album.name}</strong><span>{album.photo_count.toLocaleString()} photos</span>
      </button>
      {album.can_edit ? <div className="ph-collection-actions"><TextButton onClick={() => { setName(album.name); setVisibility(album.visibility); setEditing(album); }}>Edit</TextButton><TextButton onClick={() => void deletePhotoAlbum(album.id).then(refresh, (failure: unknown) => toast({ tone: 'error', message: failure instanceof Error ? failure.message : 'Could not delete that album.' }))}>Delete</TextButton></div> : null}
    </article>)}</div>}
    <Dialog footer={<><Button onClick={() => setEditing(null)} variant="quiet">Cancel</Button><Button disabled={!name.trim()} onClick={() => void save()} variant="primary">Save</Button></>} onClose={() => setEditing(null)} open={editing !== null} size="sm" title={editing === 'new' ? 'New album' : 'Edit album'}><div className="ph-album-form"><label className="ph-field">Album name<Input autoFocus maxLength={120} onChange={(event) => setName(event.target.value)} value={name} /></label><label className="ph-field">Who can see this<Select aria-label="Album visibility" onChange={(event) => setVisibility(event.target.value as 'private' | 'shared')} value={visibility}><option value="private">Only me</option><option value="shared">Household</option></Select></label></div></Dialog>
  </section>;
}

function PeopleView({ route, onRoute, user }: { route: PhotosRoute; onRoute: (route: PhotosRoute) => void; user: UserProfile }) {
  const toast = useToast();
  const [people, setPeople] = useState<PhotoPerson[] | null>(null);
  const [nextPeopleOffset, setNextPeopleOffset] = useState<number | null>(null);
  const [settings, setSettings] = useState<FaceSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  const [editing, setEditing] = useState<PhotoPerson | null>(null);
  const [merging, setMerging] = useState<PhotoPerson | null>(null);
  const [mergeTarget, setMergeTarget] = useState('');
  const [consent, setConsent] = useState(false);
  const [consentOpen, setConsentOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [name, setName] = useState('');
  const mounted = useRef(true);
  const refreshInFlight = useRef(false);
  const refreshController = useRef<AbortController | null>(null);
  const refreshToken = useRef(0);
  const peopleRef = useRef<PhotoPerson[] | null>(null);
  const nextPeopleOffsetRef = useRef<number | null>(null);
  const replacePeople = useCallback((items: PhotoPerson[], nextOffset: number | null) => {
    peopleRef.current = items;
    nextPeopleOffsetRef.current = nextOffset;
    setPeople(items);
    setNextPeopleOffset(nextOffset);
  }, []);
  const refresh = useCallback(async (background = false) => {
    if (refreshInFlight.current) return;
    const token = ++refreshToken.current;
    const controller = new AbortController();
    refreshController.current = controller;
    refreshInFlight.current = true;
    if (!background) { setRefreshing(true); setError(null); }
    try {
      const desiredCount = Math.min(Math.max(peopleRef.current?.length ?? 100, 100), 1000);
      const peopleRequest = async () => {
        const items: PhotoPerson[] = [];
        let offset: number | null = 0;
        while (offset !== null && items.length < desiredCount) {
          const page = await listPhotoPeople(showHidden, controller.signal, offset, Math.min(200, desiredCount - items.length));
          items.push(...page.items);
          offset = page.next_offset;
        }
        return { items, nextOffset: offset };
      };
      const [{ items, nextOffset }, nextSettings] = await Promise.all([peopleRequest(), getFaceSettings(controller.signal)]);
      if (!mounted.current || token !== refreshToken.current) return;
      const visibleItems = showHidden ? items : items.filter((person) => !person.hidden);
      const previous = peopleRef.current;
      if (previous && previous.length > visibleItems.length && nextPeopleOffsetRef.current !== null) {
        const refreshedIds = new Set(visibleItems.map((person) => person.id));
        replacePeople([...visibleItems, ...previous.filter((person) => !refreshedIds.has(person.id))], nextPeopleOffsetRef.current);
      } else {
        replacePeople(visibleItems, nextOffset);
      }
      setSettings(nextSettings);
    } catch (failure) {
      if (mounted.current && token === refreshToken.current && !controller.signal.aborted && !background) setError(failure instanceof Error ? failure.message : 'Lumina could not load people.');
    } finally {
      if (token === refreshToken.current) {
        refreshInFlight.current = false;
        refreshController.current = null;
        if (mounted.current && !background) setRefreshing(false);
      }
    }
  }, [replacePeople, showHidden]);
  useEffect(() => {
    mounted.current = true;
    peopleRef.current = null;
    nextPeopleOffsetRef.current = null;
    setPeople(null);
    setNextPeopleOffset(null);
    void refresh();
    return () => {
      mounted.current = false;
      refreshToken.current += 1;
      refreshInFlight.current = false;
      refreshController.current?.abort();
      refreshController.current = null;
    };
  }, [refresh]);
  const loadMorePeople = async () => {
    const offset = nextPeopleOffsetRef.current;
    if (offset === null || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await listPhotoPeople(showHidden, undefined, offset, 100);
      const current = peopleRef.current ?? [];
      const currentIds = new Set(current.map((person) => person.id));
      replacePeople([...current, ...page.items.filter((person) => !currentIds.has(person.id))], page.next_offset);
    } catch (failure) { toast({ tone: 'error', message: failure instanceof Error ? failure.message : 'Lumina could not load more people.' }); }
    finally { setLoadingMore(false); }
  };
  const pendingJobs = settings ? (settings.jobs.queued ?? 0) + (settings.jobs.running ?? 0) : 0;
  const modelBusy = settings?.model.state === 'downloading' || settings?.model.state === 'verifying';
  useEffect(() => {
    if (!settings?.enabled || (!pendingJobs && !modelBusy)) return undefined;
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void refresh(true); }, 4000);
    const visible = () => { if (document.visibilityState === 'visible') void refresh(true); };
    document.addEventListener('visibilitychange', visible);
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, [modelBusy, pendingJobs, refresh, settings?.enabled]);
  const save = async () => { if (!editing || !name.trim()) return; try { await updatePhotoPerson(editing.id, { name: name.trim() }); setEditing(null); void refresh(true); } catch (failure) { toast({ tone: 'error', message: failure instanceof Error ? failure.message : 'Lumina could not name that person.' }); } };
  if (error) return <ErrorState body={error} onRetry={refresh} title="People are unavailable" />;
  if (people === null || settings === null) return <Skeleton count={8} label="Loading people" shape="avatar" />;
  const setEnabled = async (enabled: boolean, acknowledge = false) => {
    setBusy(true);
    try { setSettings(await updateFaceSettings({ enabled, auto_group: settings.auto_group, acknowledge_biometric_processing: acknowledge })); setConsentOpen(false); }
    catch (failure) { toast({ tone: 'error', message: failure instanceof Error ? failure.message : 'Lumina could not update face analysis.' }); }
    finally { setBusy(false); }
  };
  const merge = async () => {
    if (!merging || !mergeTarget) return;
    setBusy(true);
    try { await mergePhotoPeople(merging.id, mergeTarget); setMerging(null); setMergeTarget(''); void refresh(true); }
    catch (failure) { toast({ tone: 'error', message: failure instanceof Error ? failure.message : 'Lumina could not merge those groups.' }); }
    finally { setBusy(false); }
  };
  const failed = settings.jobs.failed ?? 0;
  const scan = async () => {
    setBusy(true);
    try { const { queued } = await scanPhotoFaces(); toast({ tone: 'success', message: queued ? `${queued.toLocaleString()} photos queued.` : 'No photos need analysis.' }); await refresh(true); }
    catch (failure) { toast({ tone: 'error', message: failure instanceof Error ? failure.message : 'Lumina could not start analysis.' }); }
    finally { setBusy(false); }
  };
  const downloadFaceModel = async () => {
    setBusy(true);
    try {
      const model = await downloadLocalModel(settings.model.id);
      setSettings((current) => current && ({ ...current, model: { ...current.model, state: model.state, reason: model.reason, bytes_done: model.bytes_done ?? 0, bytes_total: model.bytes_total ?? 0 } }));
    } catch (failure) { toast({ tone: 'error', message: failure instanceof Error ? failure.message : 'Lumina could not download the face model.' }); }
    finally { setBusy(false); }
  };
  const modelLine = settings.model.state === 'ready'
    ? failed ? `${failed.toLocaleString()} photo${failed === 1 ? '' : 's'} failed analysis` : pendingJobs ? `${pendingJobs.toLocaleString()} photos waiting or being analyzed` : 'Analysis is up to date'
    : settings.model.state === 'downloading' && settings.model.bytes_total ? `Downloading face model · ${Math.round((settings.model.bytes_done / settings.model.bytes_total) * 100)}%`
      : settings.model.state === 'verifying' ? 'Checking the face model download' : settings.model.state === 'failed' ? 'Face model download failed' : 'Face model is not installed';
  return <section aria-labelledby="ph-people-heading"><div className="ph-section-head"><div><h2 id="ph-people-heading">People</h2><p>Optional face grouping runs locally and names stay inside your household.</p></div></div>
    <div className="ph-face-settings">
      <Switch checked={settings.enabled} busy={busy} hint="Analyzes your own photo previews on this Lumina server." label="Find people in photos" onChange={(next) => { if (next && !settings.consented_at) { setConsent(false); setConsentOpen(true); } else void setEnabled(next); }} />
      {settings.enabled ? <>
        <Switch checked={settings.auto_group} busy={busy} hint="Suggest groups conservatively; you decide names and corrections." label="Group similar faces" onChange={(next) => { setBusy(true); void updateFaceSettings({ enabled: true, auto_group: next }).then(setSettings, (failure: unknown) => toast({ tone: 'error', message: failure instanceof Error ? failure.message : 'Lumina could not update face grouping.' })).finally(() => setBusy(false)); }} />
        <div className="ph-face-status"><span>{modelLine}{settings.model.reason ? ` · ${settings.model.reason}` : ''}</span>{settings.model.state === 'absent' || settings.model.state === 'failed' ? user.role === 'admin' ? <Button busy={busy} onClick={() => void downloadFaceModel()} variant="primary">{settings.model.state === 'failed' ? 'Retry model download' : 'Download face model'}</Button> : <span>Ask a vault owner to download the face model.</span> : null}<Button busy={busy} disabled={settings.model.state !== 'ready'} onClick={() => void scan()}>{failed ? 'Retry failed' : 'Scan photos'}</Button><Button onClick={() => setDeleteOpen(true)} variant="danger">Delete face data</Button></div>
      </> : null}
    </div>
    {settings.enabled ? <div className="ph-people-tools"><Checkbox checked={showHidden} label="Show hidden groups" onChange={(event) => setShowHidden(event.target.checked)} /><Button busy={refreshing} onClick={() => void refresh()} variant="quiet">Refresh people</Button></div> : null}
    {!settings.enabled ? <EmptyState body="Turn on local face analysis above if you want optional, editable people groups." title="Face grouping is off" /> : !people.length ? <EmptyState body={showHidden ? 'No face groups have been created yet.' : 'Groups appear here after analysis finds recurring faces. Lumina never assigns names automatically.'} title={showHidden ? 'No people groups' : 'No people groups yet'} /> : <><div className="ph-people-grid">{people.map((person) => <article className={`ph-person${person.hidden ? ' is-hidden' : ''}`} key={person.id}>
      <button onClick={() => onRoute({ ...route, view: 'timeline', personId: person.id, albumId: undefined })} type="button">{resolveArtworkUrl(person.thumbnail_url) ? <img alt="" src={resolveArtworkUrl(person.thumbnail_url) ?? undefined} /> : <span><UserRound /></span>}<strong>{person.name || 'Unnamed person'}</strong><small>{person.photo_count.toLocaleString()} photos</small></button>
      <div className="ph-person-actions"><TextButton onClick={() => { setEditing(person); setName(person.name ?? ''); }}>{person.name ? 'Rename' : 'Add name'}</TextButton>{people.length > 1 && !person.hidden ? <TextButton onClick={() => { setMerging(person); setMergeTarget(''); }}>Merge</TextButton> : null}<TextButton onClick={() => { void updatePhotoPerson(person.id, { hidden: !person.hidden }).then(() => refresh(true), (failure: unknown) => toast({ tone: 'error', message: failure instanceof Error ? failure.message : 'Lumina could not update that group.' })); }}>{person.hidden ? 'Unhide' : 'Hide'}</TextButton></div>
    </article>)}</div>{nextPeopleOffset !== null ? <div className="ph-load-more"><Button busy={loadingMore} onClick={() => void loadMorePeople()} variant="quiet">Load more people</Button></div> : null}</>}
    <Dialog footer={<><Button onClick={() => setEditing(null)} variant="quiet">Cancel</Button><Button disabled={!name.trim()} onClick={() => void save()} variant="primary">Save</Button></>} onClose={() => setEditing(null)} open={editing !== null} size="sm" title="Name this person"><label className="ph-field">Name<Input autoFocus maxLength={120} onChange={(event) => setName(event.target.value)} value={name} /></label></Dialog>
    <Dialog busy={busy} footer={<><Button onClick={() => setMerging(null)} variant="quiet">Cancel</Button><Button disabled={!mergeTarget} onClick={() => void merge()} variant="primary">Merge groups</Button></>} onClose={() => setMerging(null)} open={merging !== null} size="sm" title="Merge people groups"><p>This correction combines every face in “{merging?.name || 'Unnamed person'}” with the group you choose. The chosen group stays.</p><label className="ph-field">Keep this group<Select aria-label="Group to keep" onChange={(event) => setMergeTarget(event.target.value)} value={mergeTarget}><option value="">Choose a person</option>{people.filter((person) => person.id !== merging?.id).map((person) => <option key={person.id} value={person.id}>{person.name || `Unnamed person · ${person.photo_count} photos`}</option>)}</Select></label></Dialog>
    <Dialog busy={busy} footer={<><Button onClick={() => setConsentOpen(false)} variant="quiet">Cancel</Button><Button disabled={!consent} onClick={() => void setEnabled(true, true)} variant="primary">Turn on</Button></>} onClose={() => setConsentOpen(false)} open={consentOpen} size="md" title="Turn on local face analysis?"><div className="ph-consent"><p>Lumina will process face measurements from your private photo previews on this server to suggest groups. It does not identify anyone, send images away, or add names. You can correct groups or delete all face data at any time.</p><Checkbox checked={consent} label="I understand this uses biometric information to group similar faces." onChange={(event) => setConsent(event.target.checked)} /></div></Dialog>
    <ConfirmDialog body="This removes face measurements, groups, names, and analysis history for your photos. Your photos remain unchanged." busy={busy} confirmLabel="Delete face data" danger onCancel={() => setDeleteOpen(false)} onConfirm={() => { setBusy(true); void deletePhotoFaceData().then(() => { setDeleteOpen(false); void refresh(true); }, (failure: unknown) => toast({ tone: 'error', message: failure instanceof Error ? failure.message : 'Lumina could not delete face data.' })).finally(() => setBusy(false)); }} open={deleteOpen} title="Delete all face data?" />
  </section>;
}

function TimelineToolbar({ route, onRoute, camera, onCamera, dateFrom, onDateFrom, dateTo, onDateTo }: { route: PhotosRoute; onRoute: (route: PhotosRoute) => void; camera: string; onCamera: (value: string) => void; dateFrom: string; onDateFrom: (value: string) => void; dateTo: string; onDateTo: (value: string) => void }) {
  return <div className="ph-toolbar">
    <label className="ph-search"><Search aria-hidden="true" /><Input aria-label="Search photos" onChange={(event) => onRoute({ ...route, q: event.target.value || undefined })} placeholder="Search photos" value={route.q ?? ''} /></label>
    <Button icon={<Heart />} onClick={() => onRoute({ ...route, favorite: route.favorite ? undefined : true })} pressed={route.favorite} variant="quiet">Favorites</Button>
    <label className="ph-sort"><span>Sort</span><Select aria-label="Sort photos" onChange={(event) => onRoute({ ...route, sort: event.target.value as 'captured' | 'added' })} value={route.sort ?? 'captured'}><option value="captured">Date taken</option><option value="added">Recently added</option></Select></label>
    <details className="ph-filters"><summary><Camera />Filters</summary><div className="ph-filter-panel">
      <label>Camera<Input onChange={(event) => onCamera(event.target.value)} placeholder="Camera model" value={camera} /></label>
      <label>From<Input onChange={(event) => onDateFrom(event.target.value)} type="date" value={dateFrom} /></label>
      <label>To<Input onChange={(event) => onDateTo(event.target.value)} type="date" value={dateTo} /></label>
    </div></details>
  </div>;
}

export type PhotosSurfaceProps = { user: UserProfile; route: PhotosRoute; onRoute: (route: PhotosRoute) => void };

export function PhotosSurface({ user, route, onRoute }: PhotosSurfaceProps) {
  const [importing, setImporting] = useState(false);
  const [density, setDensity] = useState(260);
  const deferredCamera = useDeferredValue(route.camera?.trim());
  const query: PhotoQuery = {
    q: route.q, favorite: route.favorite, album_id: route.albumId, person_id: route.personId, sort: route.sort ?? 'captured',
    deleted: route.view === 'trash', camera: deferredCamera || undefined, date_from: route.dateFrom, date_to: route.dateTo,
  };
  const feed = usePhotoFeed(query, route.view === 'timeline' || route.view === 'trash');
  const tabs: Array<{ view: PhotosRoute['view']; label: string; icon: ReactNode }> = [
    { view: 'timeline', label: 'Timeline', icon: <CalendarDays /> }, { view: 'albums', label: 'Albums', icon: <Images /> }, { view: 'people', label: 'People', icon: <UserRound /> }, { view: 'trash', label: 'Trash', icon: <Trash2 /> },
  ];
  return (
    <div className="surface gallery ph-surface">
      <header className="ph-masthead"><div><h1>Photos</h1><p>{route.albumId ? 'Album moments' : route.personId ? 'Photos with this person' : `A private library for ${user.display_name || user.username}`}</p></div><Button icon={<Upload />} onClick={() => setImporting(true)} variant="primary">Import</Button></header>
      <nav aria-label="Photos views" className="ph-tabs">{tabs.map((tab) => <button aria-current={route.view === tab.view ? 'page' : undefined} key={tab.view} onClick={() => onRoute({ surface: 'photos', view: tab.view, sort: route.sort })} type="button">{tab.icon}<span>{tab.label}</span></button>)}</nav>
      {route.view === 'timeline' || route.view === 'trash' ? <>
        {route.view === 'timeline' ? <TimelineToolbar camera={route.camera ?? ''} dateFrom={route.dateFrom ?? ''} dateTo={route.dateTo ?? ''} onCamera={(value) => onRoute({ ...route, camera: value || undefined })} onDateFrom={(value) => onRoute({ ...route, dateFrom: value || undefined })} onDateTo={(value) => onRoute({ ...route, dateTo: value || undefined })} onRoute={onRoute} route={route} /> : <div className="ph-trash-head"><h2>Trash</h2><p>Restore photos you want to keep.</p></div>}
        <PhotoTimeline density={density} error={feed.error} loading={feed.loading} onDensity={setDensity} onLoadMore={feed.loadMore} onPatch={feed.patch} onReload={feed.reload} onRetry={feed.retry} photos={feed.items} total={feed.total} trash={route.view === 'trash'} cursor={feed.cursor} />
      </> : route.view === 'albums' ? <AlbumView onRoute={onRoute} route={route} /> : <PeopleView onRoute={onRoute} route={route} user={user} />}
      <ImportDialog onClose={() => setImporting(false)} onImported={feed.reload} open={importing} user={user} />
    </div>
  );
}

export { addPhotoToAlbum, removePhotoFromAlbum };
