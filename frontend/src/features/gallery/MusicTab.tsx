/**
 * The Music tab: masthead with album and artist counts, the view switch, the Saved audio
 * shelf, and the Albums or Artists wall (GalleryWall) or the saved-audio still wall. The
 * LibraryBrowser mounts it at /library/music.
 */
import { type FormEvent, type ReactNode, useEffect, useState } from 'react';

import { Search } from '../../luminaIcons';
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
const RECENCY_VISIBLE = 4;

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
  const saved = useFetched('music:saved-audio', () => listLibrary({ kind: 'audio', sort: 'recent', limit: SHELF_LIMIT }));
  const items = saved.data?.items ?? [];
  if (!items.length) return null;
  return (
    <section aria-labelledby="g-saved-audio" className="g-shelf">
      <header className="g-shelf-head">
        <h2 className="g-h2" id="g-saved-audio">Saved audio</h2>
        <button className="g-text-button g-button-text" onClick={onSeeAll} type="button">See all</button>
      </header>
      <ul className="g-shelf-row">
        {items.map((item, index) => <li key={item.id}><StillCard item={item} kind="audio" onPlay={onPlay} position={index} priority={3} shape="square" sizes="(max-width: 599px) 28vw, 168px" /></li>)}
      </ul>
    </section>
  );
}

function AlbumShelf({ title, albums, onOpen }: { title: string; albums: TitleSummary[]; onOpen: (title: TitleSummary) => void }) {
  if (!albums.length) return null;
  const id = `g-${title.replaceAll(' ', '-')}`;
  return <section aria-labelledby={id} className="g-shelf g-music-recency">
    <header className="g-shelf-head"><h2 className="g-h2" id={id}>{title}</h2></header>
    <ul className="g-shelf-row">{albums.slice(0, RECENCY_VISIBLE).map((album, index) => <li key={album.id}><AlbumCard onOpen={onOpen} position={index} priority={2} sizes="(max-width: 599px) 42vw, (max-width: 1023px) 30vw, 280px" title={album} /></li>)}</ul>
  </section>;
}

function AlbumShelves({ onOpen }: { onOpen: (title: TitleSummary) => void }) {
  const played = useFetched('music:recently-played-albums', () => listRecentlyPlayedAlbums({ limit: SHELF_LIMIT }));
  const added = useFetched('music:recently-added-albums', () => listTitles({ type: 'album', sort: 'created', limit: SHELF_LIMIT }));
  return <>{played.error || added.error ? <p role="status">Some recent albums could not be loaded. The complete catalog is still available below.</p> : null}<AlbumShelf albums={played.data?.items ?? []} onOpen={onOpen} title="Recently played" /><AlbumShelf albums={added.data?.items ?? []} onOpen={onOpen} title="Recently added" /></>;
}

function MusicSearch({ query, onChange, onOpen }: { query: string; onChange: (query: string) => void; onOpen: (title: TitleSummary) => void }) {
  const [draft, setDraft] = useState(query);
  useEffect(() => setDraft(query), [query]);
  const { data, error, loading } = useFetched(`music:search:${query}`, () => query ? searchLibrary(query, 30, 'music') : Promise.resolve(null));
  const titles = (data?.matches ?? []).flatMap((match) => match.kind === 'title' && match.media_title && (match.media_title.type === 'album' || match.media_title.type === 'artist') ? [match.media_title] : []);
  const unique = titles.filter((title, index) => titles.findIndex((other) => other.id === title.id) === index);
  const submit = (event: FormEvent) => { event.preventDefault(); onChange(draft.trim().slice(0, 200)); };
  return <section className="g-music-search" aria-label="Search music"><form className="g-search" onSubmit={submit} role="search"><Search aria-hidden="true" /><input aria-label="Search music" className="g-input" maxLength={200} onChange={(event) => setDraft(event.target.value)} placeholder="Search music" type="search" value={draft} /><button className="g-button g-button-text" type="submit">Search</button></form>
    {query ? <section aria-busy={loading} aria-label={`Music results for “${query}”`} className="g-music-results"><header className="g-results-head"><h2 className="g-h2">Results for “{query}”</h2><button className="g-text-button g-button-text" onClick={() => onChange('')} type="button">Clear</button></header>{error ? <p className="g-inline-error" role="alert">Search is unavailable right now.</p> : loading && !data ? <p className="g-empty-result">Searching…</p> : data && !unique.length ? <p className="g-empty-result">Nothing in your music matches “{query}”.</p> : null}{unique.length ? <ul className="g-shelf-row">{unique.map((title, index) => <li key={title.id}><AlbumCard onOpen={onOpen} position={index} priority={2} sizes="(max-width: 599px) 28vw, 168px" title={title} /></li>)}</ul> : null}</section> : null}
  </section>;
}

export function MusicTab({ state, onWallChange, lenses, sections, onOpen, onPlay, canDelete, onItemChanged, preferredView = 'albums', onBrowseViewChange }: MusicTabProps) {
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
          {savedCount > 0 || view === 'saved' ? <button aria-label="Saved audio" aria-pressed={view === 'saved'} className="g-text-button g-button-text g-music-saved-link" onClick={() => show('saved')} type="button"><span aria-hidden="true">Saved audio{savedCount ? ` · ${savedCount}` : ''}</span></button> : null}
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
    const params = new URLSearchParams(wall);
    const catalogOpen = params.get('catalog') === '1' || [...params.keys()].some((key) => key !== 'catalog');
    const toggleCatalog = () => { catalogOpen ? params.delete('catalog') : params.set('catalog', '1'); onWallChange(serializeMusicState('albums', params.toString())); };
    return <div className="surface gallery g-music-browse">{lenses}{header(null)}<details className="g-music-catalog" open={catalogOpen}><summary onClick={(event) => { event.preventDefault(); toggleCatalog(); }}><span><span className="g-label g-kicker">Music library</span><h2 className="g-music-catalog-heading">All albums</h2></span><span className="g-label">{catalogOpen ? 'Show less' : `${sections?.albums ?? 0} albums`}</span></summary>{catalogOpen ? <GalleryWall header={() => null} onOpen={onOpen} onPlayAt={() => undefined} onWallChange={(next) => onWallChange(serializeMusicState('albums', ['catalog=1', next].filter(Boolean).join('&')))} state={wall} wall="albums" /> : null}</details></div>;
  }
  return <GalleryWall header={header} key={view} lenses={lenses} onOpen={onOpen} onPlayAt={() => undefined} onWallChange={(next) => onWallChange(serializeMusicState(view, next))} state={wall} wall={view} />;
}
