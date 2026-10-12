/**
 * The Music tab: masthead with album and artist counts, the view switch, the Saved audio
 * shelf, and the Albums or Artists wall (GalleryWall) or the saved-audio still wall. The
 * LibraryBrowser mounts it at /library/music.
 */
import type { ReactNode } from 'react';

import { listLibrary } from '../../api';
import type { LibraryItem, LibrarySections, TitleSummary } from '../../types';
import { useFetched } from '../titles/titleModel';
import { countNoun } from './galleryModel';
import { GalleryWall } from './GalleryWall';
import { StillCard } from './StillCard';
import { StillWall } from './StillWall';
import './music.css';

export type MusicTabProps = {
  /** The tab's query string without '?': view=albums|artists|saved plus that view's own wall keys. */
  state?: string;
  onWallChange: (state: string) => void;
  /** The tab row, above the masthead. */
  lenses?: ReactNode;
  /** The last sections answer (kicker counts, the Saved audio button and shelf); null before it arrives. */
  sections: LibrarySections | null;
  onOpen: (title: TitleSummary) => void;
  onPlay: (item: LibraryItem) => void;
  canDelete: (item: LibraryItem) => boolean;
  onItemChanged: (item: LibraryItem) => void;
};

export type MusicView = 'albums' | 'artists' | 'saved';
const VIEWS: ReadonlyArray<[MusicView, string]> = [['albums', 'Albums'], ['artists', 'Artists'], ['saved', 'Saved audio']];
const SHELF_LIMIT = 24;

/** The tab's view and the rest of its address, which belongs to that view's wall; an unknown view is Albums. */
export function parseMusicState(state: string | undefined): { view: MusicView; wall: string } {
  const params = new URLSearchParams(state ?? '');
  const asked = params.get('view');
  params.delete('view');
  return { view: VIEWS.find(([value]) => value === asked)?.[0] ?? 'albums', wall: params.toString() };
}

/** Canonical address: `view` first, and left out for Albums. */
export const serializeMusicState = (view: MusicView, wall = ''): string => [view === 'albums' ? '' : `view=${view}`, wall].filter(Boolean).join('&');

/** Up to 24 saved audio items, newest first, as square cards in one scroller. Hidden while loading or failed. */
function SavedAudioShelf({ onPlay, onSeeAll }: { onPlay: (item: LibraryItem) => void; onSeeAll: () => void }) {
  const saved = useFetched('music:saved-audio', () => listLibrary({ kind: 'audio', sort: 'recent', limit: SHELF_LIMIT }));
  const items = saved.data?.items ?? [];
  if (!items.length) return null;
  return (
    <section aria-labelledby="g-saved-audio" className="g-shelf">
      <header className="g-shelf-head">
        <h2 className="g-label" id="g-saved-audio">Saved audio</h2>
        <button className="g-text-button g-button-text" onClick={onSeeAll} type="button">See all</button>
      </header>
      <ul className="g-shelf-row">
        {items.map((item, index) => <li key={item.id}><StillCard item={item} kind="audio" onPlay={onPlay} position={index} priority={3} shape="square" sizes="(max-width: 599px) 28vw, 168px" /></li>)}
      </ul>
    </section>
  );
}

export function MusicTab({ state, onWallChange, lenses, sections, onOpen, onPlay, canDelete, onItemChanged }: MusicTabProps) {
  const { view, wall } = parseMusicState(state);
  const savedCount = sections?.saved_audio ?? 0;
  const show = (next: MusicView) => { if (next !== view) onWallChange(serializeMusicState(next)); };
  const counts = sections ? `${countNoun(sections.albums, ['album', 'albums'])} · ${countNoun(sections.artists, ['artist', 'artists'])}` : null;
  const header = (sortedBy: string | null) => (
    <>
      <header className="g-masthead">
        <h1>Music</h1>
        <p className="g-label g-kicker">{[counts, sortedBy ? `Sorted by ${sortedBy}` : null].filter(Boolean).join(' · ')}</p>
      </header>
      <div aria-label="Show" className="g-chips g-view-switch" role="group">
        {VIEWS.filter(([value]) => value !== 'saved' || savedCount > 0 || view === 'saved').map(([value, label]) => (
          <button aria-pressed={view === value} className="g-chip g-button-text" key={value} onClick={() => show(value)} type="button">{label}</button>
        ))}
      </div>
      {view === 'albums' && savedCount > 0 ? <SavedAudioShelf onPlay={onPlay} onSeeAll={() => show('saved')} /> : null}
    </>
  );
  if (view === 'saved') {
    return <StillWall canDelete={canDelete} count={sections?.saved_audio ?? null} header={header(null)} kind="audio" lenses={lenses} onItemChanged={onItemChanged} onPlay={onPlay} onWallChange={(next) => onWallChange(serializeMusicState('saved', next))} shape="square" wall={wall} />;
  }
  return <GalleryWall header={header} key={view} lenses={lenses} onOpen={onOpen} onPlayAt={() => undefined} onWallChange={(next) => onWallChange(serializeMusicState(view, next))} state={wall} wall={view} />;
}
