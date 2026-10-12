/**
 * The Library walls: Movies, Shows, Anime and the Music tab's Albums
 * and Artists. Editorial masthead (or the Music tab's own), sticky toolbar (sort, quick chips, "Find the one where…",
 * Filters, each as the wall offers them), the virtualised grid or the search results, and the A–Z rail. The wall's
 * state lives in the address.
 */
import { type FormEvent, type KeyboardEvent, type PointerEvent, type ReactNode, lazy, Suspense, useEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { Search, SlidersHorizontal, X } from 'lucide-react';

import { searchLibrary } from '../../api';
import { undoBatch } from '../titles/editor/editorActions';
import type { LocalSearchMatch, SearchScope, TitleSort, TitleSummary, TitleType } from '../../types';
import { Button, useToast } from '../../ui';
import { formatDuration } from '../../utils';
import { moveFocus } from '../media/focusNav';
import { EmptyShelf } from '../media/MediaCards';
import { episodeCode, useFetched } from '../titles/titleModel';
import { rangeIds, selectionNote, toggleId } from './bulkModel';
import { CHIPS, FilterDrawer } from './FilterDrawer';
import { GalleryArt } from './GalleryArt';
import { activeFacetCount, cardColour, countNoun, DEFAULT_WALL, fallbackColour, serializeWallQuery, wallFiltered, wallKey, type WallKind, wallListQuery, type WallQuery, wallQueryFor, WALLS } from './galleryModel';
import { PosterCard } from './PosterCard';
import { forgetTitle } from './titleCache';
import { useMediaQuery, WallGrid, type WallGridHandle } from './WallGrid';
import { useWallStore } from './wallPages';

// The bulk dialog pulls in the editor's chips and CSS; load it when Select mode first needs it.
const BulkEditDialog = lazy(() => import('./BulkEditDialog').then((module) => ({ default: module.BulkEditDialog })));

/** value, option label, and the words after "Sorted by" in the masthead. */
const SORTS: ReadonlyArray<[TitleSort, string, string]> = [['created', 'Recently added', 'recently added'], ['name', 'Title A–Z', 'title'], ['year', 'Year', 'year'], ['rating', 'Rating', 'rating']];
export const RAIL = ['#', ...'ABCDEFGHIJKLMNOPQRSTUVWXYZ'];
/** The title hits "Find the one where…" keeps per wall; the scope has already narrowed the category. */
const HIT_TYPES: Partial<Record<WallKind, readonly TitleType[]>> = { movies: ['movie'], shows: ['series'], anime: ['movie', 'series'] };

export type GalleryWallProps = {
  /** Which wall. */
  wall: WallKind;
  /** The wall's state: its query string without '?' (routes.ts keeps it opaque). */
  state?: string;
  /** The tab row, shown above the masthead. */
  lenses?: ReactNode;
  /** Replaces the masthead (the Music tab's, with its view switch and shelf); given the words after "Sorted by". */
  header?: (sortedBy: string) => ReactNode;
  onWallChange: (state: string) => void;
  onOpen: (title: TitleSummary) => void;
  onPlayAt: (itemId: string, startSeconds: number) => void;
  /** The member may edit details (UserProfile.can_edit_details): Movies, Shows and Anime get a Select mode. */
  canEdit?: boolean;
};

export function GalleryWall({ wall, state, lenses, header, onWallChange, onOpen, onPlayAt, canEdit = false }: GalleryWallProps) {
  const def = WALLS[wall];
  const query = wallQueryFor(wall, state);
  const key = wallKey(wall, query);
  const store = useWallStore(key, wallListQuery(wall, query));
  const phone = useMediaQuery('(max-width: 599px)');
  const grid = useRef<WallGridHandle>(null);
  const toolbar = useRef<HTMLDivElement>(null);
  const sortSelect = useRef<HTMLSelectElement>(null);
  const filtersButton = useRef<HTMLButtonElement>(null);
  const searchField = useRef<HTMLInputElement>(null);
  const searchButton = useRef<HTMLButtonElement>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [draft, setDraft] = useState(query.q);
  const [draftFor, setDraftFor] = useState(query.q);
  if (draftFor !== query.q) {
    setDraftFor(query.q);
    setDraft(query.q);
  }
  const [announcement, setAnnouncement] = useState('');
  const announceNext = useRef(false);
  const toast = useToast();
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [bulkOpen, setBulkOpen] = useState(false);
  const lastPick = useRef<number | null>(null);
  const selectButton = useRef<HTMLButtonElement>(null);
  const pick = (title: TitleSummary, index: number, shift: boolean) => {
    const from = lastPick.current; // read now: the updater runs after lastPick moves on
    setSelected((current) => (shift && from !== null ? rangeIds(current, from, index, (at) => store.itemAt(at)) : toggleId(current, title.id)));
    lastPick.current = index;
  };
  const stopSelecting = () => {
    flushSync(() => { setSelecting(false); setSelected(new Set()); });
    lastPick.current = null;
    selectButton.current?.focus();
  };
  // A filter, sort or search change shows other titles, so a selection must not carry over to them.
  const queryKey = serializeWallQuery(query);
  useEffect(() => { setSelected((current) => (current.size ? new Set() : current)); lastPick.current = null; }, [queryKey]);
  const refreshWall = (ids: ReadonlySet<string>) => {
    for (const id of ids) forgetTitle(id);
    store.revalidate(0, store.total ?? 0);
  };
  const bulkDone = (message: string, batchId: string) => {
    const edited = selected;
    setBulkOpen(false);
    setSelecting(false);
    setSelected(new Set());
    lastPick.current = null;
    refreshWall(edited);
    const undo = async () => { await undoBatch(batchId, toast, () => refreshWall(edited)); };
    toast({ tone: 'success', message, action: batchId ? { label: 'Undo', onAction: undo } : undefined });
  };
  const [bubble, setBubble] = useState<{ letter: string; y: number } | null>(null);

  useEffect(() => {
    if (!announceNext.current || store.total === null) return;
    announceNext.current = false;
    setAnnouncement(countNoun(store.total, def.noun));
  });

  const change = (next: WallQuery) => onWallChange(serializeWallQuery(next, def.sorts[0]));
  /** Sort, chips and filters: the list starts again at the top, focus stays on the control, the count is announced. */
  const refilter = (next: WallQuery) => {
    announceNext.current = true;
    window.scrollTo({ top: 0 });
    change(next);
  };
  const find = (q: string) => {
    if (q === query.q) return;
    if (q && !query.q) store.freeze(window.scrollY, null); // Clear restores the wall's scroll; focus stays on the search field
    change({ ...query, q });
  };
  const stickyOffset = () => {
    const element = toolbar.current;
    return element ? (parseFloat(getComputedStyle(element).top) || 0) + element.offsetHeight : 0;
  };
  /** Up from the first row, and focus after Clear filters: the toolbar's last control (Filters, else Sort). */
  const toolbarEnd = () => (filtersButton.current ?? sortSelect.current)?.focus();
  const letters = new Set(store.letters.map((entry) => entry.letter));
  const jump = (letter: string, behavior?: ScrollBehavior) => {
    const anchor = store.letters.find((entry) => entry.letter === letter);
    if (anchor) grid.current?.jumpTo(anchor.index, behavior);
  };
  /** Keyboards and TV remotes: Down from the masthead, a toolbar button or the Find field reaches the grid. */
  const downToGrid = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'ArrowDown' || (event.target as HTMLElement).matches('select, textarea')) return;
    event.preventDefault();
    grid.current?.focusCurrent();
  };
  /**
   * Left/Right step across the toolbar's controls: out of the Sort select always, out of the Find field once the caret
   * is at its start (Left) or end (Right). At the toolbar's ends they do nothing, so the select never changes value.
   */
  const toolbarKeys = (event: KeyboardEvent<HTMLElement>) => {
    downToGrid(event);
    const field = event.target as HTMLElement;
    const left = event.key === 'ArrowLeft';
    const leaves = (left || event.key === 'ArrowRight') && (field instanceof HTMLSelectElement
      || (field instanceof HTMLInputElement && field.selectionStart === field.selectionEnd && (left ? field.selectionStart === 0 : field.selectionEnd === field.value.length)));
    moveFocus(event, { targets: ':is(button, input, select):not(:disabled)', verticalExit: 'input', horizontalExit: leaves ? 'select, input' : 'select' });
    if (leaves) event.preventDefault();
  };
  /** Phone: dragging along the rail jumps continuously, with the letter in a bubble beside the thumb. */
  const dragRail = (event: PointerEvent<HTMLElement>) => {
    if (event.pointerType === 'mouse' || !event.currentTarget.hasPointerCapture?.(event.pointerId)) return;
    const box = event.currentTarget.getBoundingClientRect();
    const slot = Math.min(RAIL.length - 1, Math.max(0, Math.floor(((event.clientY - box.top) / box.height) * RAIL.length)));
    const letter = RAIL[slot];
    if (letter !== bubble?.letter && letters.has(letter)) jump(letter, 'auto');
    setBubble({ letter, y: event.clientY - box.top });
  };

  const filterCount = activeFacetCount(query) + (phone && def.chips ? CHIPS.filter(([chip]) => query[chip]).length : 0);
  const sortedBy = SORTS.find(([value]) => value === query.sort)?.[2] ?? 'recently added';
  const kicker = [store.total === null ? null : countNoun(store.total, def.noun), wallFiltered(query) ? 'Filtered' : null, `Sorted by ${sortedBy}`].filter(Boolean).join(' · ');
  const showRail = query.sort === 'name' && !query.q && Boolean(store.total);

  const searchForm = (
    <form className="g-search" onSubmit={(event: FormEvent) => { event.preventDefault(); find(draft.trim().slice(0, 200)); }} role="search">
      <Search aria-hidden="true" />
      <input
        aria-label="Find the one where…"
        className="g-input"
        autoFocus={phone}
        enterKeyHint="search"
        maxLength={200}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== 'Escape') return;
          event.preventDefault();
          setDraft('');
          find('');
        }}
        placeholder="Find the one where…"
        ref={searchField}
        type="search"
        value={draft}
      />
      {phone ? <button aria-label="Close search" className="g-icon-button" onClick={() => { flushSync(() => setSearchOpen(false)); searchButton.current?.focus(); }} type="button"><X aria-hidden="true" /></button> : null}
    </form>
  );

  let body: ReactNode;
  if (query.q && def.search) {
    body = <SearchResults onClear={() => { find(''); (searchField.current ?? searchButton.current)?.focus(); }} onOpen={onOpen} onPlayAt={onPlayAt} phone={phone} q={query.q} scope={def.search} types={HIT_TYPES[wall] ?? []} />;
  } else if (store.total === 0 && wallFiltered(query)) {
    body = (
      <div className="g-empty-result">
        <p>Nothing matches these filters.</p>
        <button className="g-button g-button-text" onClick={() => { refilter({ ...DEFAULT_WALL, sort: query.sort }); toolbarEnd(); }} type="button">Clear filters</button>
      </div>
    );
  } else if (store.total === 0) {
    body = <EmptyShelf body={def.empty.body} icon={def.empty.icon} title={def.empty.title} />;
  } else {
    body = (
      <>
        <WallGrid featured={def.featured} handle={grid} key={key} label={def.heading} metricLabel={wall} onExitUp={toolbarEnd} onLetter={query.sort === 'name' ? (letter) => jump(letter) : undefined} onOpen={onOpen} query={query} select={selecting ? { on: true, selected, pick } : undefined} shape={def.shape} stickyOffset={stickyOffset} store={store} />
        {store.failed ? (
          <div className="g-inline-error" role="alert">
            <p>Lumina could not load these titles.</p>
            <button className="g-button g-button-text" onClick={() => store.retry()} type="button">Try again</button>
          </div>
        ) : null}
      </>
    );
  }

  return (
    <div className={`surface gallery g-wall${showRail ? ' has-rail' : ''}`}>
      {lenses}
      {header ? header(sortedBy) : (
        <header className="g-masthead" onKeyDown={downToGrid}>
          <h1>{def.heading}</h1>
          <p className="g-label g-kicker">{kicker}</p>
          {canEdit && def.shape !== 'square' ? (
            <button aria-pressed={selecting} className="g-button g-button-text" onClick={() => (selecting ? stopSelecting() : setSelecting(true))} ref={selectButton} type="button">Select</button>
          ) : null}
        </header>
      )}
      <div className={`g-toolbar${searchOpen ? ' is-searching' : ''}`} onKeyDown={toolbarKeys} ref={toolbar}>
        <label className="g-sort g-select">
          <span className="g-label">Sort</span>
          <select onChange={(event) => refilter({ ...query, sort: event.target.value as TitleSort })} ref={sortSelect} value={query.sort}>
            {def.sorts.map((value) => <option key={value} value={value}>{SORTS.find(([sort]) => sort === value)?.[1]}</option>)}
          </select>
        </label>
        {phone || !def.chips ? null : (
          <div aria-label="Quick filters" className="g-chips" role="group">
            {CHIPS.map(([chip, label]) => <button aria-pressed={query[chip]} className="g-chip g-button-text" key={chip} onClick={() => refilter({ ...query, [chip]: !query[chip] })} type="button">{label}</button>)}
          </div>
        )}
        {!def.search ? null : phone && !searchOpen ? <button aria-label="Search" className="g-icon-button g-search-open" onClick={() => setSearchOpen(true)} ref={searchButton} type="button"><Search aria-hidden="true" /></button> : searchForm}
        {!def.facets ? null : phone ? (
          <button aria-label={filterCount ? `Filters (${filterCount})` : 'Filters'} className="g-icon-button g-filters" onClick={() => setFiltersOpen(true)} ref={filtersButton} type="button">
            <SlidersHorizontal aria-hidden="true" />
            {filterCount ? <span aria-hidden="true" className="g-filters-count">{filterCount}</span> : null}
          </button>
        ) : (
          <button className="g-button g-button-text g-filters" onClick={() => setFiltersOpen(true)} ref={filtersButton} type="button">
            <SlidersHorizontal aria-hidden="true" />
            {filterCount ? `Filters (${filterCount})` : 'Filters'}
          </button>
        )}
      </div>
      <p className="sr-only" role="status">{announcement}</p>
      {body}
      {showRail ? (
        <nav
          aria-label="Jump to letter"
          className="g-rail"
          onPointerCancel={() => setBubble(null)}
          onPointerDown={(event) => {
            if (event.pointerType === 'mouse') return;
            event.currentTarget.setPointerCapture?.(event.pointerId);
            dragRail(event);
          }}
          onPointerMove={dragRail}
          onPointerUp={() => setBubble(null)}
        >
          {RAIL.map((letter) => <button aria-disabled={!letters.has(letter)} className="g-rail-text" key={letter} tabIndex={letters.has(letter) ? 0 : -1} onClick={() => { if (letters.has(letter)) jump(letter); }} type="button">{letter}</button>)}
          {bubble ? <span aria-hidden="true" className="g-rail-bubble" style={{ top: bubble.y }}>{bubble.letter}</span> : null}
        </nav>
      ) : null}
      {selecting ? (
        <div aria-label="Selection" className="g-select-bar" role="region">
          <span role="status">{selected.size} selected</span>
          {selectionNote(selected.size) ? <span className="g-label">{selectionNote(selected.size)}</span> : null}
          <Button disabled={!selected.size} onClick={() => setBulkOpen(true)} variant="primary">Edit details…</Button>
          <Button onClick={stopSelecting}>Done</Button>
        </div>
      ) : null}
      {bulkOpen ? <Suspense fallback={null}><BulkEditDialog onClose={() => setBulkOpen(false)} onDone={bulkDone} titleIds={[...selected]} /></Suspense> : null}
      {filtersOpen && def.facets ? (
        <FilterDrawer
          onApply={(next) => { if (serializeWallQuery(next) !== serializeWallQuery(query)) refilter(next); }}
          onClose={() => { setFiltersOpen(false); filtersButton.current?.focus(); }}
          phone={phone}
          query={query}
          wall={wall}
        />
      ) : null}
    </div>
  );
}

type SearchResultsProps = {
  scope: SearchScope; types: readonly TitleType[]; q: string; phone: boolean;
  onClear: () => void; onOpen: (title: TitleSummary) => void; onPlayAt: (itemId: string, startSeconds: number) => void;
};

/** "Find the one where…" results: title posters, then moments that open the player at their time. */
function SearchResults({ scope, types, q, phone, onClear, onOpen, onPlayAt }: SearchResultsProps) {
  const [attempt, setAttempt] = useState(0);
  // A search the member replaced (or left) is cancelled rather than left to finish unseen.
  const inFlight = useRef<AbortController | null>(null);
  useEffect(() => () => inFlight.current?.abort(), []);
  const { data, error, loading } = useFetched(`${scope}:${q}`, () => {
    inFlight.current?.abort();
    inFlight.current = new AbortController();
    return searchLibrary(q, 30, scope, { signal: inFlight.current.signal });
  }, attempt);
  const matches = data?.matches ?? [];
  const found = matches.flatMap((match) => (match.kind === 'title' && match.media_title && types.includes(match.media_title.type) ? [match.media_title] : []));
  const hits = found.filter((title, index) => found.findIndex((other) => other.id === title.id) === index);
  const scenes = matches.filter((match) => match.kind === 'moment' && match.item && match.start_ms != null);
  return (
    <section aria-busy={loading} aria-label={`Results for “${q}”`} className="g-results">
      <header className="g-results-head">
        <h2 className="g-h2">Results for “{q}”</h2>
        <button className="g-text-button g-button-text" onClick={onClear} type="button">Clear</button>
      </header>
      {error ? (
        <div className="g-inline-error" role="alert">
          <p>Search is unavailable right now.</p>
          <button className="g-button g-button-text" onClick={() => setAttempt((value) => value + 1)} type="button">Try again</button>
        </div>
      ) : data && !hits.length && !scenes.length ? <p className="g-empty-result">Nothing in your {scope} matches “{q}”.</p> : loading && !data ? <p className="g-empty-result">Searching…</p> : null}
      {hits.length ? (
        <div className="g-results-grid">
          {hits.map((title, index) => <PosterCard caption={!phone} key={title.id} onOpen={onOpen} position={index} priority={2} sizes="(max-width: 599px) 33vw, 180px" title={title} />)}
        </div>
      ) : null}
      {scenes.length ? <div className="g-scenes">{scenes.map((match) => <SceneCard key={match.id} match={match} onPlayAt={onPlayAt} />)}</div> : null}
    </section>
  );
}

function SceneCard({ match, onPlayAt }: { match: LocalSearchMatch; onPlayAt: (itemId: string, startSeconds: number) => void }) {
  const title = match.media_title ?? null;
  const seconds = Math.floor((match.start_ms ?? 0) / 1000);
  const clock = formatDuration(seconds);
  const name = title?.series_name ?? title?.name ?? match.title;
  const code = title?.type === 'episode' ? episodeCode(title) : '';
  return (
    <button aria-label={`Play ${name} from ${clock}`} className="g-scene" onClick={() => { if (match.item) onPlayAt(match.item.id, seconds); }} type="button">
      <GalleryArt alt="" art={title?.poster} card={{ name, year: null }} colour={title ? cardColour(title, 'still') : { colour: fallbackColour(match.id), fromPalette: true }} kind="still" priority={2} sizes="(max-width: 599px) 100vw, 320px" />
      {/* Moments carry no transcript line yet, so the quote is the match's own title. */}
      <span className="g-scene-quote">{match.title}</span>
      <span className="g-label">{[code, clock].filter(Boolean).join(' · ')}</span>
    </button>
  );
}
