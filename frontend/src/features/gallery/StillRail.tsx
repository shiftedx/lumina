/**
 * A horizontal rail of RemoteStillCards, the 16:9 sibling of the episode row. One tab stop:
 * Left/Right/Home/End move between cards here; Up/Down fall through to the page's moveFocus, which lands on the nearest
 * card or Record button of the next rail. "See all" turns the rail into an in-page wall. Rails below the fold skip
 * rendering until near and load their images at low priority.
 */
import { type KeyboardEvent, type MouseEvent, type ReactNode, useCallback, useEffect, useRef, useState } from 'react';

import type { RemoteEntry, WallPage } from '../../types';
import { liveCountLabel } from '../../utils';
import type { LoadPriority } from './imageLoader';
import { type RecoArt, RecoArtScope } from '../reco/recoFeedback';
import { RemoteStillCard } from './RemoteStillCard';
import { REMOTE_CARD_SIZES } from './remoteModel';
import { useMediaQuery } from './WallGrid';

export type StillRailProps = {
  /** URL key for "See all" (`?rail=<key>`), [a-z0-9_-]{1,40}. */
  railKey: string;
  heading: string;
  /** Label beside the heading ("24 live"). */
  kicker?: string | null;
  /** Provider-reported live channels: written "1,240 live" (compact on phones); nothing when null. */
  liveCount?: number | null;
  /** The expanded wall's next page (cursor null = first); the wall loads more as its end scrolls near. */
  loadPage?: (cursor: string | null) => Promise<WallPage>;
  items: readonly RemoteEntry[];
  onOpen: (item: RemoteEntry) => void;
  actionsFor?: (item: RemoteEntry) => ReactNode;
  describedByFor?: (item: RemoteEntry) => string | undefined;
  /** Makes a card a recommendation: its art menu then offers Not interested and the rest. */
  recoFor?: (item: RemoteEntry) => RecoArt | null;
  showProvider?: boolean;
  /** Keys (webpage_url || id) to draw as ENDED (kept while focused). */
  endedKeys?: ReadonlySet<string>;
  /** Above the fold: images at high priority and no content-visibility. */
  eager?: boolean;
  /** Rendered as the in-page wall of every item ("See all" opened). */
  expanded?: boolean;
  /** "See all": expand in place (`onSeeAll(railKey)`), or follow `seeAllHref` (Explore's Live now → /live). */
  onSeeAll?: (railKey: string) => void;
  seeAllHref?: string;
  /** Cards shown before "See all". */
  max?: number;
  shape?: 'still' | 'short';
};

const keyOf = (item: RemoteEntry, index: number): string => item.webpage_url || item.id || `card-${index}`;

/** True once the element comes within one viewport (the loader's low priority until then). */
function useNear(eager: boolean) {
  const ref = useRef<HTMLElement>(null);
  const [near, setNear] = useState(eager);
  useEffect(() => {
    const element = ref.current;
    if (near || !element || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver((entries) => { if (entries.some((entry) => entry.isIntersecting)) setNear(true); }, { rootMargin: '100% 0px' });
    observer.observe(element);
    return () => observer.disconnect();
  }, [near]);
  return { ref, near };
}

type More = { items: RemoteEntry[]; cursor: string | null; done: boolean; loading: boolean; failed: boolean };

/** Pages a wall in as its sentinel nears the viewport: one request at a time, a page is never fetched twice, stops at next_cursor null. */
function useMore(loadPage: StillRailProps['loadPage'], active: boolean) {
  const [more, setMore] = useState<More>({ items: [], cursor: null, done: false, loading: false, failed: false });
  const busy = useRef(false);
  const state = useRef(more);
  state.current = more;
  const load = useCallback(() => {
    if (!loadPage || busy.current || state.current.done) return;
    busy.current = true;
    setMore((current) => ({ ...current, loading: true, failed: false }));
    loadPage(state.current.cursor).then(
      (page) => setMore((current) => ({ items: [...current.items, ...page.items], cursor: page.next_cursor, done: page.next_cursor === null, loading: false, failed: false })),
      () => setMore((current) => ({ ...current, loading: false, failed: true })),
    ).finally(() => { busy.current = false; });
  }, [loadPage]);
  const sentinel = useRef<HTMLDivElement>(null);
  const waiting = active && Boolean(loadPage) && !more.done && !more.failed;
  useEffect(() => {
    const element = sentinel.current;
    if (!waiting || !element || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver((entries) => { if (entries.some((entry) => entry.isIntersecting)) load(); }, { rootMargin: '100% 0px' });
    observer.observe(element);
    return () => observer.disconnect();
  }, [waiting, load, more.items.length]);
  return { more, load, sentinel };
}

export function StillRail({ railKey, heading, kicker, liveCount, loadPage, items, onOpen, actionsFor, describedByFor, recoFor, showProvider = false, endedKeys, eager = false, expanded = false, onSeeAll, seeAllHref, max = 24, shape = 'still' }: StillRailProps) {
  const id = `rail-${railKey}`;
  const phone = useMediaQuery('(max-width: 599px)');
  const { ref, near } = useNear(eager);
  const scroller = useRef<HTMLDivElement>(null);
  const { more, load, sentinel } = useMore(loadPage, expanded);
  const shown = expanded ? (more.items.length ? [...new Map([...items, ...more.items].map((item, index) => [keyOf(item, index), item])).values()] : items) : items.slice(0, max);
  const count = liveCountLabel(liveCount, phone) ?? kicker;
  const [active, setActive] = useState<string | null>(null);
  const activeKey = shown.some((item, index) => keyOf(item, index) === active) ? active : shown[0] ? keyOf(shown[0], 0) : null;

  useEffect(() => {
    if (expanded) scroller.current?.querySelector<HTMLElement>('button.g-still')?.focus();
  }, [expanded]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (expanded || event.altKey || event.ctrlKey || event.metaKey) return;
    const buttons = [...(scroller.current?.querySelectorAll<HTMLElement>('.g-remote-card > button.g-still') ?? [])];
    const from = buttons.findIndex((button) => button.closest('.g-remote-card')?.contains(event.target as Node));
    let to: number | null = null;
    if (event.key === 'ArrowRight') to = Math.min(buttons.length - 1, from + 1);
    else if (event.key === 'ArrowLeft') to = Math.max(0, from - 1);
    else if (event.key === 'Home') to = 0;
    else if (event.key === 'End') to = buttons.length - 1;
    if (to === null || from < 0) return;
    event.preventDefault(); // moveFocus skips a handled key
    buttons[to]?.focus();
    buttons[to]?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
  };

  const seeAll = (event: MouseEvent<HTMLAnchorElement>) => {
    if (!onSeeAll || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onSeeAll(railKey);
  };
  const priority: LoadPriority = eager ? 2 : near ? 3 : 4;
  return (
    <section aria-labelledby={id} className={`g-rail${eager ? '' : ' is-lazy'}${expanded ? ' is-expanded' : ''}`} data-rail-key={railKey} ref={ref}>
      <header className="g-rail-head">
        <h2 id={id}>{heading}</h2>
        {count ? <span className="g-label">{count}</span> : null}
        {seeAllHref ? <a className="g-text-button g-rail-all" data-focus-item href={seeAllHref} onClick={seeAll}>See all</a>
          : !expanded && onSeeAll && (items.length > max || (liveCount ?? 0) > items.length) ? <button className="g-text-button g-rail-all" data-focus-item onClick={() => onSeeAll(railKey)} type="button">See all</button> : null}
      </header>
      <div className={expanded ? 'g-rail-wall' : 'g-rail-scroller'} data-focus-row={expanded ? undefined : true} onKeyDown={onKeyDown} ref={scroller}>
        {shown.map((item, index) => {
          const key = keyOf(item, index);
          return (
            <RecoArtScope key={key} value={recoFor?.(item) ?? null}>
            <RemoteStillCard
              actions={actionsFor?.(item)}
              caption={!(phone && expanded)}
              describedBy={describedByFor?.(item)}
              ended={endedKeys?.has(key)}
              item={item}
              onFocus={() => setActive(key)}
              onOpen={onOpen}
              position={index}
              priority={priority}
              shape={shape}
              showProvider={showProvider}
              sizes={shape === 'short' ? '180px' : REMOTE_CARD_SIZES}
              tabIndex={expanded ? undefined : key === activeKey ? 0 : -1}
            />
            </RecoArtScope>
          );
        })}
      </div>
      {expanded && loadPage && !more.done ? (
        <div className="g-rail-more g-label" ref={sentinel}>
          {more.failed ? <button className="g-text-button" data-focus-item onClick={load} type="button">Could not load more. Try again</button>
            : <span aria-live="polite" role="status">{more.loading ? 'Loading more…' : ''}</span>}
        </div>
      ) : null}
    </section>
  );
}
