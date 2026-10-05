/**
 * The All landing: the masthead with the Library's totals, the "New in your library"
 * spotlight, one two-row chapter per non-empty category in its native shape (2:3 posters, 1:1 covers, 16:9 stills), then
 * household Collections. The first two chapters load with the page and the rest as they come within a viewport; Back
 * returns to the landing as it was left.
 */
import { Library } from 'lucide-react';
import { type KeyboardEvent, type ReactNode, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { recordMetric, sinceNavigation } from '../../perfMetrics';
import type { LibraryItem, LibrarySections, TitleSummary } from '../../types';
import { moveFocus } from '../media/focusNav';
import { EmptyShelf } from '../media/MediaCards';
import { AlbumCard } from './AlbumCard';
import {
  type AllStore, type Chapter, CHAPTER_WORD, type ChapterId, type ChapterShape, CHAPTERS, chapterColumns, chapterLimit, countLabel, fetchChapter,
  fetchSpotlight, gridGap, restoreAllStore, saveAllStore, sectionsKicker, spotlightKicker, useElementWidth, visibleChapters,
} from './allStore';
import { FeatureTile } from './FeatureTile';
import type { LoadPriority } from './imageLoader';
import type { LibraryLens } from './libraryLens';
import { followInApp, placePath } from './LibraryTabs';
import { PosterCard } from './PosterCard';
import { StillCard } from './StillCard';
import { useMediaQuery } from './WallGrid';

export type AllLandingProps = {
  /** The member's sections, stored or fresh; null before the first answer. */
  sections: LibrarySections | null;
  sectionsFailed: boolean;
  /** Ask for sections again: Try again, and once when a chapter comes back empty. */
  onRetrySections: () => void;
  /** The tab row, above the masthead. */
  lenses?: ReactNode;
  /** Household collections, the last chapter. */
  collections?: ReactNode;
  onOpenTitle: (title: TitleSummary) => void;
  onPlay: (item: LibraryItem) => void;
  onSeeAll: (lens: LibraryLens) => void;
};

/** Focus rows run tab row, spotlight, each chapter's See all then its tile rows, Collections. */
const TARGETS = ':is(a[href], button):not(:disabled):not(.g-art-menu-button)';
/** The first two chapters load with the page; the rest as they come within a viewport. */
const EAGER = 2;
type Watched = ChapterId | 'collections';

/** Two rows of frames, or one when the chapter's count fills no more than a row: the rows the cards will take (CLS). */
function ChapterFrames({ columns, shape, caption, rows = 2 }: { columns: number; shape: ChapterShape; caption: boolean; rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }, (_, row) => (
        <div aria-hidden="true" className={`g-chapter-row is-${shape}`} key={row} style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
          {Array.from({ length: columns }, (_, column) => <span className={`g-chapter-frame${caption ? ' has-caption' : ''}`} key={column} />)}
        </div>
      ))}
    </>
  );
}

export function AllLanding({ sections, sectionsFailed, onRetrySections, lenses, collections, onOpenTitle, onPlay, onSeeAll }: AllLandingProps) {
  const [restored] = useState(restoreAllStore);
  const [spotlight, setSpotlight] = useState<TitleSummary[] | null>(restored?.spotlight ?? null);
  const [spotlightFailed, setSpotlightFailed] = useState(false);
  const [spotlightAttempt, setSpotlightAttempt] = useState(0);
  const [slices, setSlices] = useState<AllStore['slices']>(restored?.slices ?? {});
  const [failed, setFailed] = useState<ReadonlySet<ChapterId>>(() => new Set());
  const [emptied, setEmptied] = useState<ReadonlySet<ChapterId>>(() => new Set());
  const [near, setNear] = useState<ReadonlySet<Watched>>(() => new Set());
  const root = useRef<HTMLDivElement>(null);
  const measure = useRef<HTMLDivElement>(null);
  const width = useElementWidth(measure);
  const phone = useMediaQuery('(max-width: 599px)');
  const tenFoot = useMediaQuery('(min-width: 1920px)');
  const gap = gridGap(window.innerWidth);
  const columnsOf = (shape: ChapterShape) => chapterColumns(shape, width, gap, phone, tenFoot);
  const chapters = sections ? visibleChapters(sections).filter((chapter) => !emptied.has(chapter.id)) : [];
  const inFlight = useRef(new Map<ChapterId, number>());
  const askedSections = useRef(false);
  const scrollY = useRef(restored?.scrollY ?? 0);
  const focus = useRef<AllStore['focus']>(restored?.focus ?? null);
  const latest = useRef({ spotlight, slices });
  latest.current = { spotlight, slices };

  // The spotlight is always asked for; a restored one stays on screen meanwhile.
  useEffect(() => {
    const controller = new AbortController();
    setSpotlightFailed(false);
    fetchSpotlight(controller.signal).then(setSpotlight, () => { if (!controller.signal.aborted) setSpotlightFailed(true); });
    return () => controller.abort();
  }, [spotlightAttempt]);

  // Leaving the landing cancels the chapter requests still out; what was in flight is asked again on a remount.
  const leaving = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    leaving.current = controller;
    return () => {
      controller.abort();
      inFlight.current.clear();
    };
  }, []);
  // Chapters: the first two at once, the rest within a viewport; a stale (restored) or too-small slice asks again.
  useEffect(() => {
    // Not before the first measure: a zero width would ask for the minimum and then again at the real columns.
    if (!width) return;
    chapters.forEach((chapter, index) => {
      if ((index >= EAGER && !near.has(chapter.id)) || failed.has(chapter.id)) return;
      const limit = chapterLimit(columnsOf(chapter.shape));
      const slice = slices[chapter.id];
      if ((slice?.fresh && slice.limit >= limit) || (inFlight.current.get(chapter.id) ?? 0) >= limit) return;
      inFlight.current.set(chapter.id, limit);
      const settle = () => { if (inFlight.current.get(chapter.id) === limit) inFlight.current.delete(chapter.id); };
      // On success the entry stays: an effect from a render before this slice commits still sees it in flight
      // (its `slices` is older than the ref), so it never asks for the same slice twice.
      const signal = leaving.current?.signal;
      fetchChapter(chapter.id, limit, signal).then((next) => {
        if (!next.items.length) {
          // The category emptied since sections answered: drop the chapter and ask sections again, once.
          setEmptied((current) => new Set(current).add(chapter.id));
          if (!askedSections.current) {
            askedSections.current = true;
            onRetrySections();
          }
          return;
        }
        setSlices((current) => {
          const had = current[chapter.id];
          return had?.fresh && had.limit > next.limit ? current : { ...current, [chapter.id]: next };
        });
      }, () => {
        if (signal?.aborted) return;
        settle();
        setFailed((current) => new Set(current).add(chapter.id));
      });
    });
  });

  // One observer for every chapter and Collections, one viewport ahead.
  const observer = useRef<IntersectionObserver | null>(null);
  const watched = useRef(new Map<Element, Watched>());
  const refs = useRef(new Map<Watched, (element: HTMLElement | null) => void>());
  const watch = (id: Watched) => {
    let ref = refs.current.get(id);
    if (!ref) {
      ref = (element: HTMLElement | null) => {
        for (const [known, owner] of watched.current) {
          if (owner !== id) continue;
          observer.current?.unobserve(known);
          watched.current.delete(known);
        }
        if (!element) return;
        watched.current.set(element, id);
        observer.current?.observe(element);
      };
      refs.current.set(id, ref);
    }
    return ref;
  };
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') {
      setNear(new Set<Watched>([...CHAPTERS.map((chapter) => chapter.id), 'collections']));
      return undefined;
    }
    const io = new IntersectionObserver((entries) => {
      const seen = entries.filter((entry) => entry.isIntersecting).map((entry) => watched.current.get(entry.target)).filter((id): id is Watched => id !== undefined);
      if (seen.length) setNear((current) => (seen.every((id) => current.has(id)) ? current : new Set([...current, ...seen])));
    }, { rootMargin: '100% 0px' });
    observer.current = io;
    for (const element of watched.current.keys()) io.observe(element);
    return () => {
      io.disconnect();
      observer.current = null;
    };
  }, []);

  // Kept for Back: the landing as it was left, its scroll and what opened the next page.
  useEffect(() => {
    // After a title opens, the page it opened may scroll the window while this landing is still mounted: not ours to keep.
    const onScroll = () => { if (!left.current) scrollY.current = window.scrollY; };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', onScroll);
      saveAllStore({ ...latest.current, scrollY: scrollY.current, focus: focus.current });
    };
  }, []);
  // Back: the same place at once, and again after the shell focuses the page heading (which can scroll). Once, and
  // not before the first measure: a zero width draws too few tiles, and a Suspense re-reveal must not jump back.
  const pendingRestore = useRef(restored);
  useLayoutEffect(() => {
    const store = pendingRestore.current;
    if (!store || !width) return;
    pendingRestore.current = null;
    const put = () => {
      window.scrollTo(0, store.scrollY);
      const key = store.focus;
      if (!key) return;
      // Within its own chapter (or the spotlight): the same title can be in both.
      const scope = root.current?.querySelector(key.chapter ? `#g-chapter-${key.chapter}` : '.g-spotlight');
      const target = [...(scope?.querySelectorAll<HTMLElement>('[data-focus-key]') ?? [])].find((element) => element.dataset.focusKey === key.key)
        ?? (key.chapter ? scope?.querySelector<HTMLElement>('.g-see-all') : null);
      target?.focus({ preventScroll: true });
    };
    put();
    requestAnimationFrame(put);
  }, [width]);

  // wall_first_screen_ms "all": masthead, tabs, the spotlight settled and the first chapter heading.
  const firstScreen = useRef(restored !== null);
  useEffect(() => {
    if (firstScreen.current || !sections || (spotlight === null && !spotlightFailed)) return;
    firstScreen.current = true;
    requestAnimationFrame(() => recordMetric('wall_first_screen_ms', 'all', sinceNavigation()));
  });

  // Saved now, not only on unmount: a quick Back can mount the next landing before this one unmounts.
  const left = useRef(false);
  const remember = (key: string, chapter: ChapterId | null) => {
    scrollY.current = window.scrollY;
    focus.current = { key, chapter };
    left.current = true;
    saveAllStore({ ...latest.current, scrollY: scrollY.current, focus: focus.current });
  };
  const keys = (event: KeyboardEvent<HTMLDivElement>) => {
    const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (event.key === 'Home' && (event.ctrlKey || event.metaKey)) {
      const tabs = root.current?.querySelector('.g-tabs');
      (tabs?.querySelector<HTMLElement>('[aria-current="page"]') ?? tabs?.querySelector<HTMLElement>('a[href]'))?.focus();
      event.preventDefault();
      return;
    }
    const row = active?.closest<HTMLElement>('.g-spotlight, .g-chapter-row');
    if (row && (event.key === 'Home' || event.key === 'End')) {
      const tiles = [...row.querySelectorAll<HTMLElement>(TARGETS)];
      (event.key === 'Home' ? tiles[0] : tiles.at(-1))?.focus();
      event.preventDefault();
      return;
    }
    // The side tiles stack beside the lead, whose centre sits between them: Up/Down step between the two first.
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && !event.altKey && active?.matches('.g-spotlight .is-side')) {
      const box = active.getBoundingClientRect();
      const down = event.key === 'ArrowDown';
      const next = [...(root.current?.querySelectorAll<HTMLElement>('.g-spotlight .is-side') ?? [])]
        .find((tile) => { const other = tile.getBoundingClientRect(); return down ? other.top >= box.bottom : other.bottom <= box.top; });
      if (next) {
        next.focus();
        event.preventDefault();
        return;
      }
    }
    moveFocus(event, { targets: TARGETS });
  };

  const chapterBody = (chapter: Chapter, index: number, columns: number): ReactNode => {
    const slice = slices[chapter.id];
    const caption = !phone || chapter.shape === 'still';
    if (failed.has(chapter.id)) {
      return (
        <div className="g-inline-error" data-focus-row role="alert">
          <p>{`Lumina could not load your ${CHAPTER_WORD[chapter.id]}.`}</p>
          <button className="g-button g-button-text" onClick={() => setFailed((current) => { const next = new Set(current); next.delete(chapter.id); return next; })} type="button">Try again</button>
        </div>
      );
    }
    if (!slice) return <ChapterFrames caption={caption} columns={columns} rows={sections ? Math.min(2, Math.max(1, Math.ceil(chapter.count(sections) / columns))) : 2} shape={chapter.shape} />;
    const shown: Array<TitleSummary | LibraryItem> = [...slice.items].slice(0, 2 * columns);
    const rows = [shown.slice(0, columns), shown.slice(columns)].filter((row) => row.length > 0);
    const priority = (row: number): LoadPriority => (index === 0 && row === 0 ? 1 : index < EAGER ? 2 : 3);
    const sizes = `${Math.ceil(width / columns)}px`;
    return rows.map((row, r) => (
      <div className={`g-chapter-row is-${chapter.shape}`} data-focus-row key={r} style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
        {row.map((entry, c) => {
          const common = { position: r * 1000 + c, priority: priority(r), sizes };
          if (slice.kind === 'items') {
            const item = entry as LibraryItem;
            return <StillCard {...common} caption item={item} key={item.id} kind={chapter.id === 'youtube' ? 'video' : 'recording'} onPlay={(played) => { remember(played.id, chapter.id); onPlay(played); }} shape="still" />;
          }
          const title = entry as TitleSummary;
          const open = (opened: TitleSummary) => { remember(opened.id, chapter.id); onOpenTitle(opened); };
          return chapter.shape === 'square'
            ? <AlbumCard {...common} caption={caption} data-focus-key={title.id} key={title.id} onOpen={open} title={title} />
            : <PosterCard {...common} caption={caption} data-focus-key={title.id} key={title.id} onOpen={open} title={title} />;
        })}
      </div>
    ));
  };

  const kicker = sections ? sectionsKicker(sections) : '';
  // The band and the chapters arrive together, once the spotlight has answered; frames stand in until then (none before the counts arrive: they would be repainted at the real height). A band shown early that
  // collapses (nothing new) or grows would shift painted chapters (CLS), so there is no band skeleton.
  const settled = spotlight !== null || spotlightFailed;
  const empty = sections !== null && !visibleChapters(sections).length && sections.saved_audio === 0 && spotlight !== null && !spotlight.length;
  return (
    <div className="surface gallery g-all" onKeyDown={keys} ref={root}>
      {lenses}
      <header className="g-masthead">
        <h1>Library</h1>
        <p className="g-label g-kicker">{kicker || '\u00a0'}</p>
      </header>
      {sectionsFailed && !sections ? (
        <div className="g-inline-error" data-focus-row role="alert">
          <p>Lumina could not load your library.</p>
          <button className="g-button g-button-text" onClick={onRetrySections} type="button">Try again</button>
        </div>
      ) : null}
      {empty ? <EmptyShelf body="Import a media folder in Settings › Library & storage, or save videos from Explore." icon={Library} title="Your library is empty" /> : null}
      <div ref={measure} style={{ display: 'flow-root' }}>
        {!spotlight?.length && !spotlightFailed ? null : (
          <section aria-labelledby="g-spotlight-title" className="g-spotlight-band">
            <h2 className="g-label g-spotlight-title" id="g-spotlight-title">New in your library</h2>
            {spotlight?.length ? (
              <div className={`g-spotlight is-count-${spotlight.length}`} data-focus-row>
                {spotlight.map((title, index) => (
                  <FeatureTile
                    className={index === 0 ? 'is-lead' : 'is-side'}
                    index={index}
                    key={title.id}
                    kicker={spotlightKicker(title)}
                    onOpen={(opened) => { remember(opened.id, null); onOpenTitle(opened); }}
                    position={index}
                    priority={1}
                    sizes={phone ? '86vw' : index === 0 ? '66vw' : '33vw'}
                    tabIndex={0}
                    title={title}
                  />
                ))}
              </div>
            ) : null}
            {spotlightFailed && !spotlight?.length ? (
              <div className="g-inline-error" data-focus-row role="alert">
                <p>Lumina could not load what's new.</p>
                <button className="g-button g-button-text" onClick={() => setSpotlightAttempt((attempt) => attempt + 1)} type="button">Try again</button>
              </div>
            ) : null}
          </section>
        )}
        {sections && !settled ? (['movies', 'shows'] as const).map((id, index) => (
          <section aria-hidden="true" className={`g-chapter is-skeleton${index === 0 ? ' is-first' : ''}`} key={id}>
            <ChapterFrames caption={!phone} columns={columnsOf('poster')} shape="poster" />
          </section>
        )) : null}
        {(settled ? chapters : []).map((chapter, index) => {
          const word = CHAPTER_WORD[chapter.id];
          return (
            <section aria-labelledby={`g-chapter-${chapter.id}-title`} className={`g-chapter${index === 0 ? ' is-first' : ''}`} id={`g-chapter-${chapter.id}`} key={chapter.id} ref={watch(chapter.id)}>
              <div className="g-chapter-head" data-focus-row>
                <h2 id={`g-chapter-${chapter.id}-title`}>{chapter.heading}</h2>
                {sections ? <span className="g-label">{countLabel(chapter.count(sections), chapter.noun)}</span> : null}
                <a
                  aria-label={`See all ${word}`}
                  className="g-text-button g-see-all"
                  data-focus-key={`see-all:${chapter.id}`}
                  href={placePath(chapter.id)}
                  onClick={(event) => followInApp(event, () => { remember(`see-all:${chapter.id}`, chapter.id); onSeeAll(chapter.id); })}
                >See all</a>
              </div>
              {chapterBody(chapter, index, columnsOf(chapter.shape))}
            </section>
          );
        })}
        {collections && settled && (sections || sectionsFailed) ? (
          <section aria-labelledby="g-chapter-collections-title" className={`g-chapter g-collections${chapters.length ? '' : ' is-first'}`} data-focus-row id="g-chapter-collections" ref={watch('collections')}>
            <div className="g-chapter-head"><h2 id="g-chapter-collections-title">Collections</h2></div>
            {near.has('collections') ? collections : <span className="g-collections-frame" />}
          </section>
        ) : null}
      </div>
    </div>
  );
}
