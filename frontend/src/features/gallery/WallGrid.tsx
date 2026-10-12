/**
 * The virtualised wall: rows of posters against the window scroll, only the viewport ± 3 rows plus the
 * focused row in the DOM, one tab stop with a roving tabindex, and a wide feature tile on every sixth row of the
 * default wall.
 */
import { type CSSProperties, type KeyboardEvent, type MouseEvent, type Ref, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react';

import { recordMetric, sinceNavigation, startLongTaskCount } from '../../perfMetrics';
import type { TitleSummary } from '../../types';
import { AlbumCard } from './AlbumCard';
import { FeatureTile } from './FeatureTile';
import { prefersReducedMotion, rowCells, rowCount, rowOf, rowStart, verticalNeighbour, visibleRows, wallColumns, wallFeatured, type WallKind, type WallLayout, type WallQuery } from './galleryModel';
import { type LoadPriority, setScrollVelocity } from './imageLoader';
import { PosterCard } from './PosterCard';
import type { WallStore } from './wallPages';

/** Rows of empty frames while the first page is on its way. */
const LOADING_ROWS = 3;
const OVERSCAN = 3;
const SCROLL_IDLE_MS = 500;

export type WallGridHandle = { jumpTo: (index: number, behavior?: ScrollBehavior) => void; focusCurrent: () => void };

/** A media query as state; false where matchMedia is missing (jsdom). */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback((notify: () => void) => {
    const list = window.matchMedia?.(query);
    list?.addEventListener('change', notify);
    return () => list?.removeEventListener('change', notify);
  }, [query]);
  return useSyncExternalStore(subscribe, () => window.matchMedia?.(query).matches ?? false, () => false); // server render: not a match
}

type WallGridProps = {
  store: WallStore;
  query: WallQuery;
  label: string;
  metricLabel: WallKind;
  /** 2:3 posters or 1:1 album and artist covers. */
  shape?: 'poster' | 'square';
  /** The wall ever features wide tiles (WallDef.featured); only poster walls do. */
  featured?: boolean;
  onOpen: (title: TitleSummary) => void;
  /** Up from the first row: the toolbar takes focus. */
  onExitUp: () => void;
  /** Typing a letter under name sort jumps as the rail does. */
  onLetter?: (letter: string) => void;
  /** Pixels hidden under the top bar and the sticky toolbar. */
  stickyOffset: () => number;
  handle?: Ref<WallGridHandle>;
  /** Select mode (bulk edit): a click picks the title instead of opening it. */
  select?: { on: boolean; selected: ReadonlySet<string>; pick: (title: TitleSummary, index: number, shift: boolean) => void };
};

export function WallGrid({ store, query, label, metricLabel, shape = 'poster', featured = true, onOpen, onExitUp, onLetter, stickyOffset, handle, select }: WallGridProps) {
  useSyncExternalStore(store.subscribe, store.snapshot); // re-render as pages land
  const box = useRef<HTMLDivElement>(null);
  const phone = useMediaQuery('(max-width: 599px)');
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const element = box.current;
    if (!element) return undefined;
    const measure = () => setWidth(element.clientWidth || window.innerWidth);
    measure();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const gap = window.innerWidth >= 1024 ? 24 : window.innerWidth >= 600 ? 16 : 8;
  const columns = wallColumns(width, gap, phone);
  const layout: WallLayout = { columns, featured: featured && shape === 'poster' && wallFeatured(query, columns, phone) };
  const posterWidth = Math.max(1, (width - gap * (columns - 1)) / columns);
  const rowHeight = Math.round(posterWidth * (shape === 'poster' ? 1.5 : 1) + (phone ? 0 : 44));
  const pitch = rowHeight + gap;
  const loaded = store.total !== null;
  const total = store.total ?? rowStart(LOADING_ROWS, layout);
  const rows = rowCount(total, layout);

  // Tagged with the pitch and row count it was measured at: a range from before the width was known is not ensured.
  const [range, setRange] = useState({ first: 0, last: -1, from: 0, to: -1, pitch: 0, rows: 0 });
  const direction = useRef<1 | -1>(1);
  const scrolled = useRef(false);
  const measureRange = useRef(() => undefined as void);
  measureRange.current = () => {
    const top = box.current?.getBoundingClientRect().top ?? 0;
    const next = { ...visibleRows(-top, window.innerHeight, pitch, rows, OVERSCAN), pitch, rows };
    setRange((current) => (current.first === next.first && current.last === next.last && current.from === next.from && current.to === next.to && current.pitch === pitch && current.rows === rows ? current : next));
  };
  useLayoutEffect(() => measureRange.current(), [pitch, rows]);

  useEffect(() => {
    let lastY = window.scrollY;
    let lastAt = performance.now();
    let idle: ReturnType<typeof setTimeout> | undefined;
    let stopCount: (() => number) | null = null;
    const onScroll = () => {
      const now = performance.now();
      const y = window.scrollY;
      if (now > lastAt) setScrollVelocity(Math.abs(y - lastY) / window.innerHeight / ((now - lastAt) / 1000));
      if (y !== lastY) direction.current = y > lastY ? 1 : -1;
      lastY = y;
      lastAt = now;
      scrolled.current = true;
      if (!store.frozen) store.scrollY = y;
      stopCount ??= startLongTaskCount();
      clearTimeout(idle);
      idle = setTimeout(() => {
        if (stopCount) recordMetric('long_tasks', 'wall_scroll', stopCount());
        stopCount = null;
      }, SCROLL_IDLE_MS);
      measureRange.current();
    };
    const onResize = () => measureRange.current();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onResize);
      clearTimeout(idle);
      if (stopCount) recordMetric('long_tasks', 'wall_scroll', stopCount());
    };
  }, [store]);

  useEffect(() => {
    if (range.pitch === pitch && range.rows === rows && range.to >= range.from) store.ensure(rowStart(range.from, layout), rowStart(range.to + 1, layout) - 1);
  }, [store, range.from, range.to, range.pitch, range.rows, pitch, rows, layout.columns, layout.featured]); // layout is rebuilt from these each render

  const [focus, setFocus] = useState(() => store.focusIndex ?? 0);
  const pendingFocus = useRef(false);
  const focusInside = useRef(false);
  const laidOut = useRef(columns);
  // Rows re-flowed on resize: the focused poster was remounted in another row; keep focus on it and keep it in view.
  useLayoutEffect(() => {
    if (laidOut.current === columns) return;
    laidOut.current = columns;
    if (focusInside.current && loaded) moveTo(focus);
  }, [columns]); // only a column change re-flows
  useLayoutEffect(() => {
    if (!pendingFocus.current) return;
    const target = box.current?.querySelector<HTMLElement>(`button[data-index="${focus}"]`);
    if (!target) return;
    pendingFocus.current = false;
    target.focus({ preventScroll: true });
  });

  // Back from a title page or out of search: the same offset at once, then refresh what is on screen.
  useLayoutEffect(() => {
    if (!width || !store.frozen) return;
    const { scrollY, focusIndex } = store;
    store.frozen = false;
    window.scrollTo(0, scrollY);
    requestAnimationFrame(() => {
      const element = box.current;
      if (!element) return;
      // The shell focuses the page heading on arrival, which can scroll; put the wall back, then focus the poster.
      window.scrollTo(0, scrollY);
      const seen = visibleRows(-element.getBoundingClientRect().top, window.innerHeight, pitch, rows, 0);
      store.revalidate(rowStart(seen.first, layout), rowStart(seen.last + 1, layout) - 1);
      if (focusIndex !== null) element.querySelector<HTMLElement>(`button[data-index="${focusIndex}"]`)?.focus({ preventScroll: true });
    });
  }, [store, width]); // runs once the width is known; pitch and rows come from that same render

  const firstScreen = useRef<'waiting' | 'painted' | 'done'>(loaded ? 'done' : 'waiting');
  useEffect(() => {
    if (firstScreen.current !== 'waiting' || !loaded) return;
    firstScreen.current = 'painted';
    requestAnimationFrame(() => recordMetric('wall_first_screen_ms', metricLabel, sinceNavigation()));
  });
  useEffect(() => {
    const element = box.current;
    if (!loaded || !element || firstScreen.current !== 'painted' || typeof MutationObserver === 'undefined') return undefined;
    const check = () => {
      const arts = [...element.querySelectorAll('.g-row[data-row="0"] .g-art, .g-row[data-row="1"] .g-art, .g-row[data-row="2"] .g-art')];
      if (firstScreen.current !== 'painted' || !arts.length || !arts.every((art) => art.querySelector('.g-art-image.is-shown, .g-card'))) return;
      firstScreen.current = 'done';
      recordMetric('wall_sharp_ms', metricLabel, sinceNavigation());
      observer.disconnect();
    };
    const observer = new MutationObserver(check);
    observer.observe(element, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
    check(); // every first-screen image may have settled before the observer existed
    return () => observer.disconnect();
  }, [loaded, metricLabel]);

  function moveTo(index: number) {
    const next = Math.max(0, Math.min(total - 1, index));
    setFocus(next);
    pendingFocus.current = true;
    const element = box.current;
    if (!element) return;
    const top = element.getBoundingClientRect().top + window.scrollY + rowOf(next, layout) * pitch;
    const offset = stickyOffset();
    if (top < window.scrollY + offset) window.scrollTo({ top: top - offset, behavior: 'auto' });
    else if (top + rowHeight > window.scrollY + window.innerHeight) window.scrollTo({ top: top + rowHeight + gap - window.innerHeight, behavior: 'auto' });
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!loaded || !total || event.altKey || event.metaKey) return;
    const row = rowOf(focus, layout);
    const column = focus - rowStart(row, layout);
    const lastRow = rowCount(total, layout) - 1;
    const pageRows = Math.max(1, range.last - range.first);
    const inRow = (target: number) => Math.min(rowStart(target, layout) + column, rowStart(target + 1, layout) - 1, total - 1);
    let next: number | null;
    switch (event.key) {
      case 'ArrowLeft': next = focus - 1; break;
      case 'ArrowRight': next = focus + 1; break;
      case 'ArrowDown': next = verticalNeighbour(focus, 1, layout, total); break;
      case 'ArrowUp':
        next = verticalNeighbour(focus, -1, layout, total);
        if (next === null) {
          event.preventDefault();
          onExitUp();
          return;
        }
        break;
      case 'PageDown': next = inRow(Math.min(lastRow, row + pageRows)); break;
      case 'PageUp': next = inRow(Math.max(0, row - pageRows)); break;
      case 'Home': next = event.ctrlKey ? 0 : rowStart(row, layout); break;
      case 'End': next = event.ctrlKey ? total - 1 : Math.min(rowStart(row + 1, layout), total) - 1; break;
      default:
        if (onLetter && !event.ctrlKey && /^[a-z#]$/i.test(event.key)) {
          event.preventDefault();
          onLetter(event.key.toUpperCase());
        }
        return;
    }
    if (next === null || next < 0 || next >= total) return;
    event.preventDefault();
    moveTo(next);
  }

  useImperativeHandle(handle, () => ({
    jumpTo(index, behavior = prefersReducedMotion() ? 'auto' : 'smooth') {
      const element = box.current;
      if (!element || index < 0) return;
      window.scrollTo({ top: element.getBoundingClientRect().top + window.scrollY + rowOf(index, layout) * pitch - stickyOffset(), behavior });
      setFocus(index);
      pendingFocus.current = true;
    },
    focusCurrent() {
      box.current?.querySelector<HTMLElement>(`button[data-index="${focus}"]`)?.focus();
    },
  }));

  const open = (index: number, title: TitleSummary, event?: MouseEvent) => {
    if (select?.on) { select.pick(title, index, Boolean(event?.shiftKey)); return; }
    store.freeze(window.scrollY, index);
    onOpen(title);
  };
  const firstLoad = !scrolled.current;
  const priorityOf = (row: number): LoadPriority => {
    if (row >= range.first && row <= range.last) return firstLoad && row < LOADING_ROWS ? 1 : 2;
    const ahead = direction.current > 0 ? row > range.last && row <= range.last + 2 : row < range.first && row >= range.first - 2;
    return ahead ? 3 : 4;
  };
  const focusRow = rows ? rowOf(Math.min(focus, total - 1), layout) : -1;
  const shown: number[] = [];
  for (let row = range.from; row <= range.to; row += 1) shown.push(row);
  if (focusRow >= 0 && (focusRow < range.from || focusRow > range.to)) shown.push(focusRow);
  const sizes = `${Math.round(posterWidth)}px`;
  const tileSizes = `${Math.round(posterWidth * 3 + gap * 2)}px`;

  return (
    <div
      aria-busy={!loaded}
      aria-label={label}
      className="g-grid"
      onBlur={(event) => {
        const left = event.target;
        if (box.current?.contains(event.relatedTarget as Node | null)) return;
        // A poster removed by a re-flow or a scroll blurs too; only focus moving out of the wall (left still attached) ends it.
        setTimeout(() => {
          if (!left.isConnected || box.current?.contains(document.activeElement)) return;
          focusInside.current = false;
          pendingFocus.current = false;
        });
      }}
      onFocus={() => { focusInside.current = true; }}
      onKeyDown={onKeyDown}
      ref={box}
      role="group"
      style={{ height: Math.max(0, rows * pitch - gap) }}>
      {shown.map((row) => (
        <div className="g-row" data-row={row} key={row} style={{ top: row * pitch, height: rowHeight, gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, columnGap: gap }}>
          {rowCells(row, layout, total).map((cell) => {
            const title = loaded ? store.itemAt(cell.index) : undefined;
            const style: CSSProperties = { gridColumn: `${cell.column + 1} / span ${cell.span}` };
            if (!title) return <span aria-hidden="true" className="g-slot" data-index={cell.index} key={cell.index} style={style} />;
            const priority = priorityOf(row);
            const position = row * 1000 + cell.column;
            const tabIndex = cell.index === focus ? 0 : -1;
            const onFocus = () => setFocus(cell.index);
            const onOpenCell = (opened: TitleSummary, event?: MouseEvent<HTMLButtonElement>) => open(cell.index, opened, event);
            const picking = select?.on ? { selectable: true, selected: select.selected.has(title.id) } : null;
            if (cell.span === 3) return <FeatureTile index={cell.index} key={cell.index} onFocus={onFocus} onOpen={onOpenCell} position={position} priority={priority} sizes={tileSizes} style={style} tabIndex={tabIndex} title={title} {...picking} />;
            const Card = shape === 'square' ? AlbumCard : PosterCard;
            return <Card caption={!phone} className="g-cell" data-index={cell.index} key={cell.index} onFocus={onFocus} onOpen={onOpenCell} position={position} priority={priority} sizes={sizes} style={style} tabIndex={tabIndex} title={title} {...(shape === 'square' ? null : picking)} />;
          })}
        </div>
      ))}
    </div>
  );
}
