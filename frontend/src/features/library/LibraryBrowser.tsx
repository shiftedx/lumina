/**
 * The Library: the tab row over each lens's body. All is the chaptered landing; Movies,
 * Shows and Anime are gallery walls; Music is the Music tab; YouTube and Recordings are still walls; Deleted lists the
 * files waiting out their retention window.
 */
import { RefreshCw, RotateCcw } from 'lucide-react';
import { memo, type ReactNode, useLayoutEffect, useRef, useState } from 'react';

import { refreshLibrary, restoreLibraryFile, scanLibraries } from '../../api';
import { canDeleteLibraryFile, libraryBadge, libraryByline, libraryThumbnail } from '../../luminaModel';
import type { LibraryItem, LibrarySections, TitleSummary } from '../../types';
import { AllLanding } from '../gallery/AllLanding';
import { GalleryArt } from '../gallery/GalleryArt';
import { fallbackColour } from '../gallery/galleryModel';
import { GalleryWall } from '../gallery/GalleryWall';
import { type LibraryPlace, LibraryTabs, useLibrarySections } from '../gallery/LibraryTabs';
import { MusicTab } from '../gallery/MusicTab';
import { StillWall, usePagedLibrary } from '../gallery/StillWall';
import { forgetWallStores } from '../gallery/wallPages';
import { EmptyShelf } from '../media/MediaCards';
import { Button, EmptyState, ErrorState, Masthead, Skeleton, useToast } from '../../ui';
import { ChannelsView, LibraryViewSwitch } from './ChannelsView';
import { scanSummary } from '../palette/actions';
import './library.css';

export type LibraryBrowserProps = {
  videoOnly?: boolean;
  view: LibraryPlace;
  /** The lens's state in the address, without '?'. */
  wall?: string;
  currentUser: { id: string; role: string; can_edit_details?: boolean };
  /** Household collections: the All landing's last chapter. */
  collections?: ReactNode;
  /** The Collections pages, drawn under the lens row. */
  collectionsPage?: (lenses: ReactNode) => ReactNode;
  onViewChange: (place: LibraryPlace) => void;
  onWallChange: (wall: string) => void;
  onOpenTitle: (title: TitleSummary) => void;
  onPlay: (item: LibraryItem) => void;
  /** Play a Library item from a time: a "Find the one where…" moment. */
  onPlayAt: (itemId: string, startSeconds: number) => void;
  onItemChanged: (item: LibraryItem) => void;
};

function LibraryBrowserView(props: LibraryBrowserProps) {
  const { view, currentUser, onViewChange, videoOnly = false } = props;
  const { sections, failed, refresh } = useLibrarySections(currentUser.id);
  const [refreshing, setRefreshing] = useState(false);
  const [revision, setRevision] = useState(0);
  const refreshButton = useRef<HTMLButtonElement>(null);
  const toast = useToast();
  const isAdmin = currentUser.role === 'admin';
  useLayoutEffect(() => { if (revision) refreshButton.current?.focus({ preventScroll: true }); }, [revision]);
  async function refreshNow() {
    if (refreshing) return;
    setRefreshing(true);
    try {
      const scan = isAdmin ? await scanLibraries() : null;
      await refreshLibrary();
      forgetWallStores();
      refresh();
      setRevision((value) => value + 1);
      toast({ tone: scan && !scan.started.length && !scan.queued.length ? 'info' : 'success', message: scan ? `Library refreshed. ${scanSummary(scan)}` : 'Library refreshed.' });
    } catch (failure) {
      toast({ tone: 'error', message: `Refresh failed: ${failure instanceof Error ? failure.message : 'Try again.'}` });
    } finally {
      setRefreshing(false);
    }
  }
  const tabs = <div className="g-library-toolbar">
    <LibraryTabs current={view} isAdmin={isAdmin} onOpen={onViewChange} sections={sections} videoOnly={videoOnly} />
    <Button busy={refreshing} icon={<RefreshCw />} onClick={() => void refreshNow()} ref={refreshButton} title={isAdmin ? 'Refresh the library and rescan imported folders' : 'Refresh the library'}>Refresh</Button>
  </div>;
  return <LibraryContents {...props} failed={failed} key={`${currentUser.id}:${revision}`} refresh={refresh} sections={sections} tabs={tabs} />;
}

function LibraryContents({ view, wall, currentUser, collections, collectionsPage, onViewChange, onWallChange, onOpenTitle, onPlay, onPlayAt, onItemChanged, sections, failed, refresh, tabs, videoOnly = false }: LibraryBrowserProps & { sections: LibrarySections | null; failed: boolean; refresh: () => void; tabs: ReactNode }) {
  const canDelete = (item: LibraryItem) => canDeleteLibraryFile(item, currentUser);
  if (view === 'all') return <AllLanding title={videoOnly ? 'Video' : 'Library'} collections={collections} lenses={tabs} onOpenTitle={onOpenTitle} onPlay={onPlay} onRetrySections={refresh} onSeeAll={onViewChange} sections={videoOnly && sections ? { ...sections, albums: 0, artists: 0, saved_audio: 0 } : sections} sectionsFailed={failed} />;
  if (view === 'movies' || view === 'shows' || view === 'anime') return <GalleryWall canEdit={Boolean(currentUser.can_edit_details)} key={view} lenses={tabs} onOpen={onOpenTitle} onPlayAt={onPlayAt} onWallChange={onWallChange} state={wall} wall={view} />;
  if (view === 'music') return <MusicTab canDelete={canDelete} lenses={tabs} onItemChanged={onItemChanged} onOpen={onOpenTitle} onPlay={onPlay} onWallChange={onWallChange} sections={sections} state={wall} />;
  if (view === 'collections') return collectionsPage?.(tabs) ?? <div className="surface gallery">{tabs}<Skeleton count={4} label="Loading collections…" shape="still" /></div>;
  if (view === 'youtube' && new URLSearchParams(wall ?? '').get('view') === 'channels') {
    return <ChannelsView lenses={tabs} onWallChange={onWallChange} state={wall} viewSwitch={<LibraryViewSwitch current="channels" onWallChange={onWallChange} />} />;
  }
  if (view === 'youtube' || view === 'recordings') {
    return <StillWall canDelete={canDelete} count={sections ? sections[view] : null} key={view} kind={view === 'youtube' ? 'video' : 'recording'} lenses={tabs} onItemChanged={onItemChanged} onPlay={onPlay} onWallChange={onWallChange} shape="still" viewSwitch={view === 'youtube' ? <LibraryViewSwitch current="videos" onWallChange={onWallChange} /> : undefined} wall={wall} />;
  }
  return <DeletedList currentUser={currentUser} lenses={tabs} onItemChanged={onItemChanged} />;
}

/** Deleted: files waiting out their recovery window, restorable once each. */
function DeletedList({ currentUser, lenses, onItemChanged }: { currentUser: { id: string; role: string; can_edit_details?: boolean }; lenses: ReactNode; onItemChanged: (item: LibraryItem) => void }) {
  const paged = usePagedLibrary({ status: 'missing', sort: 'recent', limit: 60 });
  const toast = useToast();
  const [restoring, setRestoring] = useState<string | null>(null);
  const items = paged.items.filter((item) => item.status === 'missing');
  const canRestore = (item: LibraryItem) => item.media_state === 'quarantined' && (currentUser.role === 'admin' || item.user_id === currentUser.id);
  async function restore(item: LibraryItem) {
    if (restoring) return;
    setRestoring(item.id);
    try {
      const restored = await restoreLibraryFile(item.id);
      paged.patch(restored);
      onItemChanged(restored);
      toast({ tone: 'success', message: `Restored “${item.title}”.` });
    } catch (failure) {
      toast({ tone: 'error', message: `Restore failed: ${failure instanceof Error ? failure.message : 'Lumina could not restore this file.'}` });
    } finally {
      setRestoring(null);
    }
  }
  let body: ReactNode;
  if (paged.loading && !paged.items.length) body = <Skeleton count={3} label="Loading deleted files…" shape="row" />;
  else if (paged.error && !paged.items.length) body = <ErrorState onRetry={paged.retry} title="Lumina could not load deleted files." />;
  else if (!items.length && !paged.cursor) body = <EmptyState body="Files you delete from Lumina’s storage wait here until the retention window ends." title="Nothing deleted." />;
  else {
    body = (
      <>
        <ol className="g-deleted-rows">
          {items.map((item) => {
            const thumbnail = libraryThumbnail(item);
            return (
              <li key={item.id}>
                <GalleryArt alt="" art={thumbnail ? { url: thumbnail, widths: [] } : null} card={{ name: item.title }} className="g-deleted-thumb" colour={{ colour: fallbackColour(item.id), fromPalette: true }} kind="still" priority={2} sizes="112px" />
                <span className="g-deleted-copy"><span className="g-deleted-title">{item.title}</span><span className="g-label">{[libraryByline(item), libraryBadge(item)].filter(Boolean).join(' · ')}</span></span>
                {canRestore(item) ? <Button busy={restoring === item.id} icon={<RotateCcw />} onClick={() => void restore(item)}>Restore</Button> : null}
              </li>
            );
          })}
        </ol>
        {paged.cursor ? <Button disabled={paged.loading} onClick={paged.loadMore} variant="quiet">Load more</Button> : null}
      </>
    );
  }
  return (
    <div className="surface gallery g-deleted">
      {lenses}
      <Masthead lede="Files deleted from Lumina's storage wait here until the recovery window ends, then they are removed for good." title="Deleted" />
      {body}
    </div>
  );
}

export const LibraryBrowser = memo(LibraryBrowserView);
