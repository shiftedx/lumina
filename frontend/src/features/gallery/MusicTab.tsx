/**
 * The Music tab: masthead with album and artist counts, the view switch, the Saved audio
 * shelf, and the Albums or Artists wall (GalleryWall) or the saved-audio still wall. The
 * LibraryBrowser mounts it at /library/music.
 */
import { type FormEvent, type ReactNode, useEffect, useState } from 'react';

import { Search } from '../../luminaIcons';
import { ContextualSearch } from '../../app/ContextualSearch';
import { listLibrary, listRecentlyPlayedAlbums, listTitles, searchLibrary } from '../../api';
import type { LibraryItem, LibrarySections, TitleSummary } from '../../types';
import { useFetched } from '../titles/titleModel';
import { countNoun } from './galleryModel';
import { AlbumCard } from './AlbumCard';
import { GalleryWall } from './GalleryWall';
import { StillCard } from './StillCard';
import { StillWall } from './StillWall';
import './music.css';

export type MusicTabProps = {
  tools?: ReactNode;
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
  preferredView?: 'albums' | 'artists';
  onBrowseViewChange?: (view: 'albums' | 'artists') => void;
};

export type MusicView = 'albums' | 'artists' | 'saved';
const VIEWS: ReadonlyArray<[MusicView, string]> = [['albums', 'Albums'], ['artists', 'Artists'], ['saved', 'Saved audio']];
const SHELF_LIMIT = 24;

/** The tab's view and the rest of its address, which belongs to that view's wall; an unknown view is Albums. */
export function parseMusicState(state: string | undefined): { view: MusicView; wall: string; hasExplicitView: boolean } {
  const params = new URLSearchParams(state ?? '');
  const asked = params.get('view');
  params.delete('view');
  return { view: VIEWS.find(([value]) => value === asked)?.[0] ?? 'albums', wall: params.toString(), hasExplicitView: asked !== null && VIEWS.some(([value]) => value === asked) };
}

/** An explicit selection survives a remembered default. */
export const serializeMusicState = (view: MusicView, wall = ''): string => [`view=${view}`, wall].filter(Boolean).join('&');

/** Up to 24 saved audio items, newest first, as square cards in one scroller. Hidden while loading or failed. */
function SavedAudioShelf({ onPlay, onSeeAll }: { onPlay: (item: LibraryItem) => void; onSeeAll: () => void }) {
  const [attempt, setAttempt] = useState(0);
  useEffect(() => { const refresh = () => { if (document.visibilityState === 'visible') setAttempt((value) => value + 1); }; document.addEventListener('visibilitychange', refresh); return () => document.removeEventListener('visibilitychange', refresh); }, []);
  const saved = useFetched('music:saved-audio', () => listLibrary({ kind: 'audio', sort: 'recent', limit: SHELF_LIMIT }), attempt);
  const items = saved.data?.items ?? [];
  return (
    <section aria-labelledby="g-saved-audio" className="g-shelf">
      <header className="g-shelf-head"><h2 className="g-h2" id="g-saved-audio">Saved audio</h2><button className="g-text-button g-button-text" onClick={onSeeAll} type="button">See all</button></header>
      {saved.loading && !saved.data ? <p role="status">Loading saved audio…</p> : saved.error ? <div role="alert"><p>Saved audio could not be loaded.</p><button className="g-text-button g-button-text" onClick={() => setAttempt((value) => value + 1)} type="button">Try again</button></div> : !items.length ? <p>No saved audio yet.</p> : <ul className="g-shelf-row">{items.map((item, index) => <li key={item.id}><StillCard item={item} kind="audio" onPlay={onPlay} position={index} priority={3} shape="square" sizes="(max-width: 599px) 28vw, 168px" /></li>)}</ul>}
    </section>
  );
}

function AlbumShelf({ title, albums, onOpen }: { title: string; albums: TitleSummary[]; onOpen: (title: TitleSummary) => void }) {
  const id = `g-${title.replaceAll(' ', '-')}`;
  return <section aria-labelledby={id} className="g-shelf g-music-recency">
    <header className="g-shelf-head"><h2 className="g-h2" id={id}>{title}</h2></header>
    {albums.length ? <ul className="g-shelf-row">{albums.map((album, index) => <li key={album.id}><AlbumCard onOpen={onOpen} position={index} priority={2} sizes="(max-width: 599px) 42vw, (max-width: 1023px) 30vw, 280px" title={album} /></li>)}</ul> : <p>{title === 'Recently played' ? 'No recently played albums yet.' : 'No recently added albums yet.'}</p>}
  </section>;
}

function AlbumShelves({ onOpen }: { onOpen: (title: TitleSummary) => void }) {
  const [attempt, setAttempt] = useState(0);
  useEffect(() => { const refresh = () => { if (document.visibilityState === 'visible') setAttempt((value) => value + 1); }; document.addEventListener('visibilitychange', refresh); return () => document.removeEventListener('visibilitychange', refresh); }, []);
  const played = useFetched('music:recently-played-albums', () => listRecentlyPlayedAlbums({ limit: SHELF_LIMIT }), attempt);
  const added = useFetched('music:recently-added-albums', () => listTitles({ type: 'album', sort: 'created', limit: SHELF_LIMIT }), attempt);
  return <>{played.error || added.error ? <div role="alert"><p>Some recent albums could not be loaded. The complete catalog is still available below.</p><button className="g-text-button g-button-text" onClick={() => setAttempt((value) => value + 1)} type="button">Try again</button></div> : null}{played.loading || added.loading ? <p role="status">Loading recent albums…</p> : null}{played.data ? <AlbumShelf albums={played.data.items} onOpen={onOpen} title="Recently played" /> : null}{added.data ? <AlbumShelf albums={added.data.items} onOpen={onOpen} title="Recently added" /> : null}</>;
}

function MusicSearch({ query, onChange, onOpen }: { query: string; onChange: (query: string) => void; onOpen: (title: TitleSummary) => void }) {
  const [draft, setDraft] = useState(query);
  useEffect(() => setDraft(query), [query]);
  const { data, error, loading } = useFetched(`music:search:${query}`, () => query ? searchLibrary(query, 30, 'music') : Promise.resolve(null));
  const titles = (data?.matches ?? []).flatMap((match) => match.kind === 'title' && match.media_title && (match.media_title.type === 'album' || match.media_title.type === 'artist') ? [match.media_title] : []);
  const unique = titles.filter((title, index) => titles.findIndex((other) => other.id === title.id) === index);
  const submit = (event: FormEvent) => { event.preventDefault(); onChange(draft.trim().slice(0, 200)); };
  return <section className="g-music-search" aria-label="Search music"><ContextualSearch label="Search music"><form className="g-search" onSubmit={submit} role="search"><Search aria-hidden="true" /><input aria-label="Search music" className="g-input" maxLength={200} onChange={(event) => setDraft(event.target.value)} placeholder="Search music" type="search" value={draft} /><button className="g-button g-button-text" type="submit">Search</button></form></ContextualSearch>
    {query ? <section aria-busy={loading} aria-label={`Music results for “${query}”`} className="g-music-results"><header className="g-results-head"><h2 className="g-h2">Results for “{query}”</h2><button className="g-text-button g-button-text" onClick={() => onChange('')} type="button">Clear</button></header>{error ? <p className="g-inline-error" role="alert">Search is unavailable right now.</p> : loading && !data ? <p className="g-empty-result">Searching…</p> : data && !unique.length ? <p className="g-empty-result">Nothing in your music matches “{query}”.</p> : null}{unique.length ? <ul className="g-shelf-row">{unique.map((title, index) => <li key={title.id}><AlbumCard onOpen={onOpen} position={index} priority={2} sizes="(max-width: 599px) 28vw, 168px" title={title} /></li>)}</ul> : null}</section> : null}
  </section>;
}

export function MusicTab({ tools, state, onWallChange, lenses, sections, onOpen, onPlay, canDelete, onItemChanged, preferredView = 'albums', onBrowseViewChange }: MusicTabProps) {
  const parsed = parseMusicState(state);
  const view = parsed.hasExplicitView || state ? parsed.view : preferredView;
  const wall = parsed.wall;
  const search = new URLSearchParams(wall).get('q') ?? '';
  const savedCount = sections?.saved_audio ?? 0;
  const show = (next: MusicView) => {
    if (next === view) return;
    if (next === 'albums' || next === 'artists') onBrowseViewChange?.(next);
    onWallChange(serializeMusicState(next));
  };
  const counts = sections ? `${countNoun(sections.albums, ['album', 'albums'])} · ${countNoun(sections.artists, ['artist', 'artists'])}` : null;
  const setSearch = (query: string) => {
    const next = new URLSearchParams(wall);
    query ? next.set('q', query) : next.delete('q');
    onWallChange(serializeMusicState(view, next.toString()));
  };
  const header = (sortedBy: string | null) => (
    <>
      <header className="g-music-head">
        <h1 className="sr-only">Music</h1>
        <div className="g-music-head-row">
          <div aria-label="Show" className="g-chips g-view-switch" role="group">
            {VIEWS.filter(([value]) => value !== 'saved').map(([value, label]) => (
          <button aria-pressed={view === value} className="g-chip g-button-text" key={value} onClick={() => show(value)} type="button">{label}</button>
            ))}
          </div>
          <div className="g-music-head-actions">{savedCount > 0 || view === 'saved' ? <button aria-label="Saved audio" aria-pressed={view === 'saved'} className="g-text-button g-button-text g-music-saved-link" onClick={() => show('saved')} type="button"><span aria-hidden="true">Saved audio{savedCount ? ` · ${savedCount}` : ''}</span></button> : null}{tools}</div>
        </div>
        <p className="g-label g-music-library-count">{[counts, sortedBy ? `Sorted by ${sortedBy}` : null].filter(Boolean).join(' · ')}</p>
      </header>
      <MusicSearch onChange={setSearch} onOpen={onOpen} query={search} />
      {view === 'albums' ? <><AlbumShelves onOpen={onOpen} />{savedCount > 0 ? <SavedAudioShelf onPlay={onPlay} onSeeAll={() => show('saved')} /> : null}</> : null}
    </>
  );
  if (view === 'saved') {
    return <StillWall canDelete={canDelete} count={sections?.saved_audio ?? null} header={header(null)} kind="audio" lenses={lenses} onItemChanged={onItemChanged} onPlay={onPlay} onWallChange={(next) => onWallChange(serializeMusicState('saved', next))} shape="square" wall={wall} />;
  }
  if (search) return <div className="surface gallery">{lenses}{header(null)}</div>;
  if (view === 'albums') {
    return <div className="surface gallery g-music-browse">{lenses}{header(null)}<section aria-labelledby="g-all-albums" className="g-music-catalog"><h2 className="g-music-catalog-heading" id="g-all-albums">All albums</h2><GalleryWall header={() => null} onOpen={onOpen} onPlayAt={() => undefined} onWallChange={(next) => onWallChange(serializeMusicState('albums', next))} state={wall} wall="albums" /></section></div>;
  }
  return <GalleryWall header={header} key={view} lenses={lenses} onOpen={onOpen} onPlayAt={() => undefined} onWallChange={(next) => onWallChange(serializeMusicState(view, next))} state={wall} wall={view} />;
}
