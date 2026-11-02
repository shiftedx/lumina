/**
 * The still wall for saved web video, Recordings and the Music tab's saved audio: the masthead
 * (or the caller's header), a sticky toolbar (Sort, and Source for web videos), and StillCards paged 60 at a time through
 * /api/library as a sentinel one viewport ahead comes into view. It deletes with the existing toast and Undo and
 * reports every changed item.
 */
import { Disc3, MonitorPlay, Radio, Search } from '../../luminaIcons';
import { type FormEvent, type ReactNode, useCallback, useEffect, useRef, useState } from 'react';

import { deleteLibraryFile, type LibraryPageQuery, listLibrary, restoreLibraryFile } from '../../api';
import { ContextualSearch } from '../../app/ContextualSearch';
import { libraryThumbnail } from '../../luminaModel';
import { recordMetric, sinceNavigation } from '../../perfMetrics';
import type { LibraryItem } from '../../types';
import { moveFocus } from '../media/focusNav';
import { EmptyShelf } from '../media/MediaCards';
import { Button, Select, useToast } from '../../ui';
import { queueMedia } from '../watch/WatchQueue';
import { chapterColumns, countLabel, gridGap, useElementWidth } from './allStore';
import { GalleryArt } from './GalleryArt';
import { fallbackColour } from './galleryModel';
import { HeroFrame } from './HeroFrame';
import { StillCard, type StillKind } from './StillCard';
import { useMediaQuery } from './WallGrid';
import '../library/youtubeBrowse.css';

export type StillWallProps = {
  kind: StillKind;
  shape: 'still' | 'square';
  /** The wall's query string without '?': sort=recent|title, source=youtube|twitch|kick|other_web, and search for video. */
  wall?: string;
  onWallChange: (wall: string) => void;
  /** The tab row, above the masthead. */
  lenses?: ReactNode;
  /** Replaces the wall's own masthead: the Music tab passes its masthead and view switch. */
  header?: ReactNode;
  /** The Videos/Channels switch, first in the toolbar. */
  viewSwitch?: ReactNode;
  /** The masthead count from sections; null before it arrives. */
  count: number | null;
  onPlay: (item: LibraryItem) => void;
  canDelete: (item: LibraryItem) => boolean;
  /** An item the wall deleted or restored (More menu, Undo); the shell's library state follows. */
  onItemChanged: (item: LibraryItem) => void;
};

export const STILL_PAGE = 60;
export type StillQuery = { sort: 'recent' | 'title'; source: '' | 'youtube' | 'twitch' | 'kick' | 'other_web'; channel?: string; search?: string };
const SOURCES: ReadonlyArray<readonly [StillQuery['source'], string]> = [['', 'All web sources'], ['youtube', 'YouTube'], ['twitch', 'Twitch'], ['kick', 'Kick'], ['other_web', 'Other web sources']];
const HEADINGS: Readonly<Record<StillKind, string>> = { video: 'YouTube', recording: 'Recordings', audio: 'Saved audio' };
const NOUNS: Readonly<Record<StillKind, readonly [string, string]>> = { video: ['video', 'videos'], recording: ['recording', 'recordings'], audio: ['item', 'items'] };
const EMPTY = {
  video: { icon: MonitorPlay, title: 'No saved videos yet', body: 'Save videos from Explore or Channels to watch them here.' },
  recording: { icon: Radio, title: 'No recordings yet', body: 'Recordings of live streams you record appear here.' },
  audio: { icon: Disc3, title: 'No saved audio yet', body: 'Save a video as audio from its download menu.' },
} as const;
/** wall_first_screen_ms labels; saved audio records under the Music tab's own walls. */
const METRIC: Partial<Record<StillKind, string>> = { video: 'youtube', recording: 'recordings' };

/** The wall's address state: sort=recent|title plus source and search for videos; the rest is dropped. */
export function parseStillQuery(kind: StillKind, wall: string | undefined): StillQuery {
  const params = new URLSearchParams(wall ?? '');
  const source = params.get('source');
  const search = params.get('search')?.trim().slice(0, 200) || undefined;
  return {
    sort: params.get('sort') === 'title' ? 'title' : 'recent',
    source: kind === 'video' && (source === 'youtube' || source === 'twitch' || source === 'kick' || source === 'other_web') ? source : '',
    ...(kind === 'video' && params.has('channel') ? { channel: params.get('channel')!.slice(0, 200) } : {}),
    ...(kind === 'video' && search ? { search } : {}),
  };
}

export function serializeStillQuery(query: StillQuery): string {
  const params = new URLSearchParams();
  if (query.sort !== 'recent') params.set('sort', query.sort);
  if (query.source) params.set('source', query.source);
  if (query.channel !== undefined) params.set('channel', query.channel);
  if (query.search) params.set('search', query.search.trim().slice(0, 200));
  return params.toString();
}

type Paged = { key: string | null; items: LibraryItem[]; cursor: string | null; loading: boolean; error: string | null };

/** Server-filtered pages for one query; a new query resets the window (moved from LibraryBrowser). */
export function usePagedLibrary(query: LibraryPageQuery | null) {
  const key = query ? JSON.stringify(query) : null;
  const [state, setState] = useState<Paged>({ key: null, items: [], cursor: null, loading: false, error: null });
  const request = useRef(0);
  const load = useCallback((cursor: string | null) => {
    if (!key) return;
    const id = ++request.current;
    setState((current) => ({ ...current, key, items: cursor ? current.items : [], loading: true, error: null }));
    listLibrary({ ...JSON.parse(key) as LibraryPageQuery, cursor }).then((page) => {
      if (id === request.current) setState((current) => ({ key, items: [...current.items, ...page.items], cursor: page.next_cursor, loading: false, error: null }));
    }, (failure: unknown) => {
      if (id === request.current) setState((current) => ({ ...current, loading: false, error: failure instanceof Error ? failure.message : 'Lumina could not load this view.' }));
    });
  }, [key]);
  useEffect(() => { load(null); }, [load]);
  const patch = useCallback((item: LibraryItem) => setState((current) => ({ ...current, items: current.items.map((entry) => entry.id === item.id ? item : entry) })), []);
  const current = state.key === key ? state : { key, items: [], cursor: null, loading: Boolean(key), error: null };
  return { ...current, loadMore: () => { if (current.cursor && !current.loading) load(current.cursor); }, retry: () => load(null), patch };
}

// non-virtualised DOM; virtualise (reuse WallGrid with an item store) when any member's tab exceeds 2,000 items.
export function StillWall({ kind, shape, wall, onWallChange, lenses, header, viewSwitch, count, onPlay, canDelete, onItemChanged }: StillWallProps) {
  const query = parseStillQuery(kind, wall);
  const paged = usePagedLibrary({ kind, sort: query.sort, search: kind === 'video' ? query.search : undefined, source: kind === 'video' ? query.source || 'saved' : undefined, status: kind === 'video' ? 'available' : undefined, group: query.channel, limit: STILL_PAGE });
  const [searchDraft, setSearchDraft] = useState(query.search ?? '');
  useEffect(() => setSearchDraft(query.search ?? ''), [query.search]);
  const phone = useMediaQuery('(max-width: 599px)');
  const narrow = useMediaQuery('(max-width: 419px)');
  const tenFoot = useMediaQuery('(min-width: 1920px)');
  const measure = useRef<HTMLDivElement>(null);
  const width = useElementWidth(measure);
  const columns = narrow && shape === 'still' ? 1 : chapterColumns(shape, width, gridGap(window.innerWidth), phone, tenFoot);
  const grid = { gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` };
  const toast = useToast();
  const visible = paged.items.filter((item) => item.status !== 'missing');

  const loadMore = useRef(paged.loadMore);
  loadMore.current = paged.loadMore;
  const sentinel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = sentinel.current;
    if (!element || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver((entries) => { if (entries.some((entry) => entry.isIntersecting)) loadMore.current(); }, { rootMargin: '100% 0px' });
    observer.observe(element);
    return () => observer.disconnect();
  }, [paged.items.length, paged.cursor]);

  const firstScreen = useRef(false);
  useEffect(() => {
    const label = METRIC[kind];
    if (!label || firstScreen.current || paged.loading || !paged.items.length) return;
    firstScreen.current = true;
    requestAnimationFrame(() => recordMetric('wall_first_screen_ms', label, sinceNavigation()));
  });

  const change = (next: StillQuery) => {
    window.scrollTo({ top: 0 });
    onWallChange(serializeStillQuery(next));
  };
  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    change({ ...query, search: searchDraft.trim().slice(0, 200) || undefined });
  };
  const changed = (item: LibraryItem) => { paged.patch(item); onItemChanged(item); };
  async function queue(item: LibraryItem) {
    const added = await queueMedia({ kind: 'library', library_item_id: item.id }, 'end');
    toast(added ? { tone: 'success', message: 'Added to your watchlist.' } : { tone: 'error', message: 'Lumina could not update your watchlist. Try again.' });
  }
  async function remove(item: LibraryItem) {
    try {
      changed(await deleteLibraryFile(item.id));
      toast({ tone: 'success', message: `Deleted “${item.title}”. It stays restorable for a while.`, action: { label: 'Undo', onAction: () => void restore(item) } });
    } catch (failure) {
      toast({ tone: 'error', message: failure instanceof Error ? failure.message : 'Lumina could not delete this file.' });
    }
  }
  async function restore(item: LibraryItem) {
    try {
      changed(await restoreLibraryFile(item.id));
      toast({ tone: 'success', message: `Restored “${item.title}”.` });
    } catch (failure) {
      toast({ tone: 'error', message: failure instanceof Error ? failure.message : 'Lumina could not restore this file.' });
    }
  }

  const sortedBy = `Sorted by ${query.sort === 'title' ? 'title' : 'recently added'}`;
  const kicker = [query.search ? `Saved video results for “${query.search}”` : query.source ? 'Filtered' : count === null ? null : countLabel(count, NOUNS[kind]), sortedBy].filter(Boolean).join(' · ');
  let body: ReactNode;
  if (paged.loading && !paged.items.length) {
    body = <div aria-busy="true" aria-label="Loading" className="g-still-grid" role="status" style={grid}>{Array.from({ length: 2 * columns }, (_, index) => <span className="g-still-slot" key={index} />)}</div>;
  } else if (paged.error && !paged.items.length) {
    body = <div className="g-inline-error" role="alert"><p>Lumina could not load more.</p><button className="g-button g-button-text" onClick={paged.retry} type="button">Try again</button></div>;
  } else if (!visible.length && !paged.cursor) {
    body = query.search || query.source ? (
      <div className="g-empty-result"><p>{query.search ? `Nothing in your saved videos matches “${query.search}”.` : 'Nothing matches these filters.'}</p><button className="g-button g-button-text" onClick={() => change({ ...query, source: '', search: undefined })} type="button">Clear filters</button></div>
    ) : <EmptyShelf {...EMPTY[kind]} />;
  } else {
    body = (
      <>
        <div className="g-still-grid" style={grid}>
          {visible.map((item, index) => (
            <StillCard
              item={item}
              key={item.id}
              kind={kind}
              onDelete={canDelete(item) ? (target) => void remove(target) : undefined}
              onQueue={(target) => void queue(target)}
              onPlay={onPlay}
              position={Math.floor(index / columns) * 1000 + (index % columns)}
              priority={index < 2 * columns ? 1 : 2}
              shape={shape}
              sizes={`${Math.ceil(width / columns)}px`}
            />
          ))}
        </div>
        {paged.error ? <div className="g-inline-error" role="alert"><p>Lumina could not load more.</p><button className="g-button g-button-text" onClick={paged.loadMore} type="button">Try again</button></div> : null}
      </>
    );
  }

  const hero = kind === 'video' && !query.search && !paged.error ? visible[0] : undefined;
  const heroArt = hero ? libraryThumbnail(hero) : null;
  const heroChannel = hero?.uploader?.trim() || null;
  return (
    <div className={`surface gallery g-still-wall is-${shape}${kind === 'video' ? ' g-wall is-video-browse g-youtube-browse' : ''}${hero ? ' has-browse-hero' : ''}`} onKeyDown={(event) => moveFocus(event, { targets: ':is(button, [role="combobox"]):not(:disabled)', horizontalExit: '[role="combobox"]' })}>
      {kind === 'video' ? <div className="g-browse-context">{lenses}<ContextualSearch label="Search saved videos"><form className="g-search" onSubmit={submitSearch} role="search"><Search aria-hidden="true" /><input aria-label="Search saved videos" className="g-input" maxLength={200} onChange={(event) => setSearchDraft(event.currentTarget.value)} onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); setSearchDraft(''); change({ ...query, search: undefined }); } }} placeholder="Search saved videos" type="search" value={searchDraft} /></form></ContextualSearch></div> : lenses}
      {hero ? <section aria-label={hero.title} className="g-browse-hero">
        <HeroFrame className="g-browse-hero-art" placement="home" src={heroArt}><GalleryArt alt="" art={heroArt ? { url: heroArt, widths: [] } : null} card={null} colour={{ colour: fallbackColour(hero.id), fromPalette: true }} kind="still" priority={1} sizes="100vw" /></HeroFrame>
        <div className="g-browse-hero-copy"><p className="g-browse-hero-context">{[query.channel !== undefined ? query.channel || 'No channel' : 'Recently saved', heroChannel].filter(Boolean).join(' · ')}</p><h2>{hero.title}</h2>{typeof hero.metadata_json?.description === 'string' ? <p>{hero.metadata_json.description}</p> : null}<div className="g-youtube-hero-actions"><Button onClick={() => onPlay(hero)}>Play</Button>{heroChannel && query.channel === undefined ? <Button className="g-youtube-creator-action" onClick={() => change({ ...query, channel: heroChannel })} variant="quiet">More from {heroChannel}</Button> : null}</div></div>
      </section> : null}
      {header ?? (query.channel !== undefined ? (
        <header className="g-masthead g-youtube-library-head"><h1 className={hero ? 'sr-only' : undefined}>{query.channel || 'No channel'}</h1>{query.search ? <h2>Results for “{query.search}”</h2> : null}<button className="g-text-button" onClick={() => change({ ...query, channel: undefined })} type="button">Clear</button></header>
      ) : <header className={`g-masthead${kind === 'video' ? ' g-youtube-library-head' : ''}`}><h1 className={hero ? 'sr-only' : undefined}>{HEADINGS[kind]}</h1>{kind === 'video' && hero ? <h2>Saved videos</h2> : null}{kind === 'video' && query.search ? <h2>Results for “{query.search}”</h2> : null}<p className="g-label g-kicker">{kicker}</p></header>)}
      <div className="g-toolbar">
        {viewSwitch}
        <label className="g-sort">
          <span className="g-label">Sort</span>
          <Select aria-label="Sort" onChange={(event) => change({ ...query, sort: event.target.value as StillQuery['sort'] })} value={query.sort}>
            <option value="recent">Recently added</option>
            <option value="title">Title A–Z</option>
          </Select>
        </label>
        {kind === 'video' ? (
          <label className="g-sort">
            <span className="g-label">Source</span>
            <Select aria-label="Source" onChange={(event) => change({ ...query, source: event.target.value as StillQuery['source'] })} value={query.source}>
              {SOURCES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </Select>
          </label>
        ) : null}
      </div>
      <div ref={measure}>{body}</div>
      {paged.cursor ? <div aria-hidden="true" className="g-sentinel" ref={sentinel} /> : null}
    </div>
  );
}
