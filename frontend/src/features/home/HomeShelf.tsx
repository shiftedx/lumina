/**
 * One Home shelf: heading, See all, desktop row buttons, a scrolling row of cards in the shelf's
 * shape with load classes by position, four hairline frames while loading, and the inline failure. Also the
 * lazy gate (no request for a shelf more than one viewport below the fold), the fetched-shelf wrapper with the session
 * cache (stale-while-revalidate), and one card per kind of entry (Home builds no card of its own:
 * StillFrame, PosterCard, AlbumCard). Props and DOM are fixed.
 */
import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import { type ReactNode, type RefObject, useEffect, useLayoutEffect, useRef, useState } from 'react';

import type { AppRoute } from '../../app/routes';
import { isAudioItem, libraryThumbnail } from '../../luminaModel';
import type { LibraryItem, TitleSummary, YouTubeSearchResult } from '../../types';
import { formatDuration } from '../../utils';
import { AlbumCard } from '../gallery/AlbumCard';
import { ArtMenu } from '../gallery/ArtMenu';
import { cardColour, fallbackColour, prefersReducedMotion } from '../gallery/galleryModel';
import type { LoadPriority } from '../gallery/imageLoader';
import { PosterCard } from '../gallery/PosterCard';
import { StillFrame, stillLabel, stillLine, stillProgress } from '../gallery/StillCard';
import { useMediaQuery } from '../gallery/WallGrid';
import { RecoCard, useRecoFilter } from '../reco/recoFeedback';
import { titleTarget } from '../reco/recoModel';
import { RecoImpressionScope, useRecoImpressions } from '../reco/useRecoImpressions';
import { cachedShelf, rememberShelf } from './homeCache';
import { captionLabel, titleCaption } from './homeModel';
import { HOME_CATALOGUE, SHELF_SIZES, type ShelfShape, type ShelfStatus } from './homeShelves';
import { type FetchedShelfId, type HomeVisit, loadShelf, type ShelfRow } from './shelfSources';

/** What HomeShelf hands each card: its load class and order, `sizes`, and whether captions show. */
export type CardSlot = { priority: LoadPriority; position: number; sizes: string; caption: boolean };

export type HomeShelfProps<T> = {
  /** The shelf id, or `${id}:${row key}` for one row of a group shelf. Also the section's data-shelf-section. */
  sectionKey: string;
  heading: string;
  shape: ShelfShape;
  /** loading: heading and four hairline frames (aria-busy); failed: the inline error with Try again; ready: the cards. */
  state: 'loading' | 'failed' | 'ready';
  items: readonly T[];
  /** The React key of a card (a live entry's webpage_url, a title's id), so a refresh keeps images and focus. */
  itemKey: (item: T) => string;
  /** One card. Its main control carries data-focus-item; a remove button follows it as its own data-focus-item. */
  renderCard: (item: T, slot: CardSlot) => ReactNode;
  onRetry?: () => void;
  /** The text button right of the heading. */
  seeAll?: { label: string; onClick: () => void } | null;
  /** Plain-text lines under the heading (notices, stale data, recovery). Never a live region. */
  notes?: ReactNode;
  /** Replaces "Lumina could not load {heading}." (the Live shelf's own failure line). */
  failedText?: string;
};

const FRAMES = 4;
/** One viewport height below the fold. */
export const NEAR_MARGIN = '0px 0px 100% 0px';
const POINTER_ROW = '(min-width: 1024px) and (hover: hover) and (pointer: fine)';

export function HomeShelf<T>({ sectionKey, heading, shape, state, items, itemKey, renderCard, onRetry, seeAll, notes, failedText }: HomeShelfProps<T>) {
  const headingId = `h-shelf-${sectionKey.replace(/\W/g, '-')}`;
  const phone = useMediaQuery('(max-width: 599px)');
  const pointerRow = useMediaQuery(POINTER_ROW);
  const section = useRef<HTMLElement>(null);
  const row = useRef<HTMLUListElement>(null);
  /** Whether the shelf starts inside the first viewport, and how many cards its row shows. */
  const [fit, setFit] = useState({ first: true, visible: FRAMES });
  const [ends, setEnds] = useState({ left: false, right: false });
  const count = state === 'ready' ? items.length : 0;
  useLayoutEffect(() => {
    const top = section.current?.getBoundingClientRect().top ?? 0;
    const card = row.current?.firstElementChild as HTMLElement | null;
    const pitch = card ? card.offsetWidth + (parseFloat(getComputedStyle(row.current as Element).columnGap) || 0) : 0;
    setFit({ first: top < window.innerHeight, visible: pitch ? Math.max(1, Math.ceil((row.current?.clientWidth ?? 0) / pitch)) : FRAMES });
  }, [count]);
  const measureEnds = () => {
    const element = row.current;
    if (element) setEnds({ left: element.scrollLeft > 0, right: element.scrollLeft + element.clientWidth < element.scrollWidth - 1 });
  };
  useEffect(() => {
    if (!pointerRow) return undefined;
    measureEnds();
    window.addEventListener('resize', measureEnds);
    return () => window.removeEventListener('resize', measureEnds);
  }, [pointerRow, count]);
  // Row buttons scroll 90 % of the row, smoothly unless motion is reduced.
  const scrollRow = (direction: 1 | -1) => row.current?.scrollBy({ left: direction * row.current.clientWidth * 0.9, behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
  const priorityOf = (index: number): LoadPriority => (index < fit.visible ? (fit.first ? 1 : 2) : index < fit.visible + 2 ? 3 : 4);
  const caption = !(phone && shape === 'poster');
  return (
    <section aria-busy={state === 'loading' || undefined} aria-labelledby={headingId} className={`h-shelf is-${shape}`} data-shelf-section={sectionKey} ref={section}>
      <div className="h-shelf-head">
        <h2 id={headingId} tabIndex={-1}>{heading}</h2>
        {seeAll ? <button className="g-text-button g-button-text" data-focus-item onClick={seeAll.onClick} type="button">{seeAll.label}</button> : null}
        {pointerRow && count && (ends.left || ends.right) ? (
          <span className="h-row-buttons">
            <button aria-label={`Scroll ${heading} left`} className="g-icon-button" disabled={!ends.left} onClick={() => scrollRow(-1)} tabIndex={-1} type="button"><ChevronLeft aria-hidden="true" /></button>
            <button aria-label={`Scroll ${heading} right`} className="g-icon-button" disabled={!ends.right} onClick={() => scrollRow(1)} tabIndex={-1} type="button"><ChevronRight aria-hidden="true" /></button>
          </span>
        ) : null}
      </div>
      {notes}
      {state === 'failed' ? (
        <p className="h-shelf-error">
          {failedText ?? `Lumina could not load ${heading}.`}{' '}
          {onRetry ? <button className="h-inline-button" data-focus-item onClick={onRetry} type="button">Try again</button> : null}
        </p>
      ) : null}
      {state === 'loading' ? <div aria-hidden="true" className="h-row is-frames">{Array.from({ length: FRAMES }, (_, index) => <span className="h-frame" key={index} />)}</div> : null}
      {count ? (
        <ul className="h-row" data-focus-row onScroll={pointerRow ? measureEnds : undefined} ref={row}>
          {items.map((item, index) => <li className="h-card" key={itemKey(item)}>{renderCard(item, { priority: priorityOf(index), position: index, sizes: SHELF_SIZES[shape], caption })}</li>)}
        </ul>
      ) : null}
    </section>
  );
}

/** True once `ref`'s element comes within one viewport of the fold; it stays true. Always true without IntersectionObserver. */
export function useNear<E extends Element>(): [RefObject<E | null>, boolean] {
  const ref = useRef<E>(null);
  const [near, setNear] = useState(() => typeof IntersectionObserver === 'undefined');
  useEffect(() => {
    const node = ref.current;
    if (near || !node) return undefined;
    const observer = new IntersectionObserver((entries) => { if (entries.some((entry) => entry.isIntersecting)) setNear(true); }, { rootMargin: NEAR_MARGIN });
    observer.observe(node);
    return () => observer.disconnect();
  }, [near]);
  return [ref, near];
}

/** A placeholder section (heading and frames, no request) until near; then `children()` (the Live shelf). */
export function NearGate({ sectionKey, heading, shape, children }: { sectionKey: string; heading: string; shape: ShelfShape; children: () => ReactNode }) {
  const [ref, near] = useNear<HTMLDivElement>();
  if (near) return <>{children()}</>;
  return <div className="h-slot" ref={ref}><HomeShelf heading={heading} itemKey={String} items={[]} renderCard={() => null} sectionKey={sectionKey} shape={shape} state="loading" /></div>;
}

export type ShelfActions = {
  onOpenTitle: (title: TitleSummary) => void;
  onOpenLibrary: (item: LibraryItem) => void;
  /** See all: the library, a category tab. */
  onOpenRoute: (route: AppRoute) => void;
  /** Next up's remove button; absent elsewhere. */
  onDismiss?: (title: TitleSummary) => void;
};

export type FetchedShelfProps = {
  id: FetchedShelfId;
  userId: string;
  visit: HomeVisit;
  actions: ShelfActions;
  /** Title ids removed on this visit (Next up dismissals), left out of the rows. */
  hidden?: ReadonlySet<string>;
  onStatus: (id: FetchedShelfId, status: ShelfStatus) => void;
};

/** Shelves already loaded in a visit: Edit -> Done remounts them, and a remount inside the same visit reuses the rows instead of refetching. */
const loadedThisVisit = new WeakMap<object, Set<string>>();

/**
 * A fetched shelf: a placeholder until near, then one load; cached rows paint at once and are replaced when
 * the refetch lands. A failed first load shows the inline error; a failed refresh keeps the rows.
 */
export function FetchedShelf({ id, userId, visit, actions, hidden, onStatus }: FetchedShelfProps) {
  const [ref, near] = useNear<HTMLDivElement>();
  const [rows, setRows] = useState<ShelfRow[] | null>(() => cachedShelf<ShelfRow[]>(userId, id) ?? null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!near) return undefined;
    if (attempt === 0 && rows && loadedThisVisit.get(visit)?.has(`${userId}:${id}`)) return undefined;
    let current = true;
    loadShelf(id, visit, userId).then((next) => {
      if (!current) return;
      (loadedThisVisit.get(visit) ?? loadedThisVisit.set(visit, new Set()).get(visit)!).add(`${userId}:${id}`);
      rememberShelf(userId, id, next);
      setRows(next);
      setFailed(false);
    }, () => { if (current) setFailed(true); });
    return () => { current = false; };
  }, [near, attempt, id, visit, userId]); // eslint-disable-line react-hooks/exhaustive-deps -- `rows` only seeds the skip check
  const shown = rows
    ?.map((row) => (row.titles && hidden?.size ? { ...row, titles: row.titles.filter((title) => !hidden.has(title.id)) } : row))
    .filter((row) => (row.titles ?? row.items ?? []).length) ?? null;
  // Status reflects the server's rows, not the locally hidden view: dismissing the shelf's only item must not report
  // it empty (which would retire the shelf for good, since a dismissed FetchedShelf unmounts under a "fresh" page).
  const hasRows = rows?.some((row) => (row.titles ?? row.items ?? []).length) ?? false;
  const status: ShelfStatus = hasRows ? 'items' : failed ? 'failed' : rows ? 'empty' : 'loading';
  useEffect(() => { onStatus(id, status); }, [id, status]); // eslint-disable-line react-hooks/exhaustive-deps -- report changes only
  const { name, shape } = HOME_CATALOGUE[id];
  return (
    <div className="h-slot" ref={ref}>
      {status === 'items' ? shown!.map((row) => <RowShelf actions={actions} key={row.key} row={row} />)
        : status === 'empty' ? null
          : <HomeShelf heading={name} itemKey={String} items={[]} onRetry={() => { setFailed(false); setAttempt((value) => value + 1); }} renderCard={() => null} sectionKey={id} shape={shape} state={status === 'failed' ? 'failed' : 'loading'} />}
    </div>
  );
}

function RowShelf({ row, actions }: { row: ShelfRow; actions: ShelfActions }) {
  // Only a row the server annotated (Because you watched, Recommended) is a served list: its cards carry the foot.
  const observe = useRecoImpressions(row.titles?.find((title) => title.reco)?.reco?.list_id ?? null);
  const filtered = useRecoFilter(row.titles ?? [], titleTarget, true);
  const titles = row.titles?.some((title) => title.reco) ? filtered : row.titles ?? [];
  const seeAll = row.seeAll ? { label: 'See all', onClick: () => actions.onOpenRoute(row.seeAll as AppRoute) } : null;
  if (row.items) {
    return <HomeShelf heading={row.heading} itemKey={(item) => item.id} items={row.items} renderCard={(item, slot) => itemStill(item, slot, actions.onOpenLibrary)} sectionKey={row.key} seeAll={seeAll} shape="still" state="ready" />;
  }
  return (
    <RecoImpressionScope value={observe}>
      <HomeShelf heading={row.heading} itemKey={(title) => title.id} items={titles} renderCard={(title, slot) => titleCard(title, row.shape, slot, actions)} sectionKey={row.key} seeAll={seeAll} shape={row.shape} state="ready" />
    </RecoImpressionScope>
  );
}

function titleCard(title: TitleSummary, shape: ShelfShape, slot: CardSlot, actions: ShelfActions): ReactNode {
  if (shape === 'poster') {
    const poster = <PosterCard {...slot} data-focus-item onOpen={actions.onOpenTitle} title={title} />;
    // A title the server annotated also offers feedback in its art menu; any other poster is exactly as before.
    return title.reco ? <RecoCard reco={title.reco} target={titleTarget(title)}>{poster}</RecoCard> : poster;
  }
  if (shape === 'square') return <AlbumCard {...slot} data-focus-item onOpen={actions.onOpenTitle} title={title} />;
  const { onDismiss } = actions;
  return (
    <>
      {titleStill(title, slot, () => actions.onOpenTitle(title))}
      {onDismiss ? <RemoveButton label={`Remove ${titleCaption(title).name} from Next up`} onRemove={() => onDismiss(title)} /> : null}
    </>
  );
}

/** A 16:9 title card: an episode shows its still, a movie its backdrop; Continue passes its position. */
export function titleStill(title: TitleSummary, slot: CardSlot, onActivate: () => void, progress: { left: string | null; percent: number | null } | null = null): ReactNode {
  const caption = titleCaption(title, progress?.left ?? null);
  const episode = title.type === 'episode';
  return (
    <StillFrame
      {...slot}
      art={(episode ? title.poster : title.backdrop ?? title.poster) ?? null}
      colour={cardColour(title, episode ? 'still' : 'backdrop')}
      data-focus-item
      label={captionLabel(caption)}
      line={caption.line}
      menu={<ArtMenu subject={{ kind: 'title', title }} />}
      name={caption.name}
      onActivate={onActivate}
      percent={progress?.percent ?? null}
      shape="still"
    />
  );
}

/** A Library item card (Recently saved, a channel_video collection, a remote Continue entry): 1:1 for audio. */
export function itemStill(item: LibraryItem, slot: CardSlot, onPlay: (item: LibraryItem) => void, percent?: number | null): ReactNode {
  const audio = isAudioItem(item);
  const thumbnail = libraryThumbnail(item);
  return (
    <StillFrame
      {...slot}
      art={thumbnail ? { url: thumbnail, widths: [] } : null}
      colour={{ colour: fallbackColour(item.id), fromPalette: true }}
      data-focus-item
      label={stillLabel(item)}
      line={stillLine(item, audio ? 'audio' : item.kind === 'recording' ? 'recording' : 'video')}
      menu={<ArtMenu subject={{ kind: 'library', item }} />}
      name={item.title}
      onActivate={() => onPlay(item)}
      percent={percent === undefined ? stillProgress(item) : percent}
      shape={audio ? 'square' : 'still'}
      sizes={audio ? SHELF_SIZES.square : slot.sizes}
    />
  );
}

/** A web video card (follows, the watchlist): channel and length on line 2. */
export function remoteStill(item: YouTubeSearchResult, slot: CardSlot, onActivate: () => void): ReactNode {
  const name = item.title || 'Untitled video';
  const art = item.artwork_url || item.thumbnail;
  const line = [item.uploader, item.duration ? formatDuration(item.duration) : null].filter(Boolean).join(' · ');
  return (
    <StillFrame
      {...slot}
      art={art ? { url: art, widths: [] } : null}
      colour={{ colour: fallbackColour(item.webpage_url || item.id || name), fromPalette: true }}
      data-focus-item
      label={captionLabel({ name, line })}
      line={line}
      menu={<ArtMenu subject={{ kind: 'remote', item }} />}
      name={name}
      onActivate={onActivate}
      shape="still"
    />
  );
}

/** The hairline × after a Continue or Next up card: its own focus stop. */
export function RemoveButton({ label, onRemove }: { label: string; onRemove: () => void }) {
  return <button aria-label={label} className="h-remove" data-focus-item onClick={onRemove} type="button"><X aria-hidden="true" /></button>;
}
