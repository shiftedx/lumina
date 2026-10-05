/** The Requests tab's building blocks: artwork, the status ribbon, the card, a rail, the infinite grid and the calm problem states. */
import { Check, ChevronLeft, ChevronRight, Clapperboard, Plus } from 'lucide-react';
import { type CSSProperties, type ReactNode, useEffect, useRef, useState } from 'react';

import { prefersReducedMotion } from '../gallery/galleryModel';
import { Button, EmptyState, ErrorState, Skeleton } from '../../ui';
import { type CatalogItem, type CatalogPage, type CatalogStatus, errorCode } from './requestsApi';
import { useNow, useRequests } from './requestsContext';
import { canAskFor, countdown, errorCopy, formatLabel, KIND_LABEL, statusBadge } from './requestsModel';

/** Absolute TMDB/AniList artwork that fades in once loaded; the title set in type when there is none or it fails. */
export function Art({ src, alt = '', className = '', fallback, eager = false }: { src?: string | null; alt?: string; className?: string; fallback?: string; eager?: boolean }) {
  const [state, setState] = useState<{ src: string | null | undefined; status: 'loading' | 'loaded' | 'failed' }>({ src, status: 'loading' });
  const status = state.src === src ? state.status : 'loading';
  if (!src || status === 'failed') {
    return <span aria-label={alt || undefined} className={`rq-art-fallback ${className}`} role={alt ? 'img' : undefined}>{fallback ? <span aria-hidden="true">{fallback}</span> : null}</span>;
  }
  return <img alt={alt} className={`rq-img ${status === 'loaded' ? 'is-loaded' : ''} ${className}`} decoding="async" loading={eager ? 'eager' : 'lazy'} onError={() => setState({ src, status: 'failed' })} onLoad={() => setState({ src, status: 'loaded' })} src={src} />;
}

/** AniList's poster accent tints the art frame only (brand rule): a runtime --g-accent on the frame. */
export const accentStyle = (item: CatalogItem): CSSProperties | undefined => (item.anime?.color && /^#[0-9a-f]{6}$/i.test(item.anime.color) ? ({ '--g-accent': item.anime.color } as CSSProperties) : undefined);

/** A small progress ring (0–100). */
export function Ring({ percent }: { percent: number }) {
  return (
    <svg aria-hidden="true" className="rq-ring" viewBox="0 0 20 20">
      <circle cx="10" cy="10" r="8" />
      <circle className="rq-ring-fill" cx="10" cy="10" pathLength="100" r="8" strokeDasharray={`${percent} 100`} />
    </svg>
  );
}

export function StatusRibbon({ status }: { status: CatalogStatus }) {
  const badge = statusBadge(status);
  if (!badge) return null;
  return (
    <span className={`rq-ribbon is-${badge.state}`}>
      {badge.percent !== null ? <Ring percent={badge.percent} /> : badge.state === 'available' ? <Check aria-hidden="true" /> : null}
      {badge.label}
    </span>
  );
}

/** One poster card: opens the title (or, once in the vault, the Library title); Request and Trailer on hover or focus. */
export function CatalogCard({ item, size = 'normal' }: { item: CatalogItem; size?: 'normal' | 'large' }) {
  const { openTitle, openLibrary, ask, statusOf } = useRequests();
  const now = useNow();
  const status = statusOf(item);
  const shown = { ...item, status };
  const available = status.state === 'available' && status.library_title_id;
  const next = size === 'large' ? item.anime?.next_episode : undefined;
  const sub = [item.year, item.kind === 'anime' && item.anime?.format ? formatLabel(item.anime.format) : KIND_LABEL[item.kind]].filter(Boolean).join(' · ');
  return (
    <div className={`rq-card is-${size}`}>
      <button aria-label={[item.title, sub, statusBadge(status)?.label, available ? 'open in Library' : null].filter(Boolean).join(', ')} className="rq-card-open" data-focus-item onClick={() => (available ? openLibrary(status.library_title_id!) : openTitle(item))} type="button">
        <span className="rq-frame" style={accentStyle(item)}>
          <Art fallback={item.title} src={item.poster_url} />
          <StatusRibbon status={status} />
          {next ? <span className="rq-airing">Ep {next.number} {countdown(next.airing_at, now)}</span> : null}
        </span>
        <span className="rq-card-title">{item.title}</span>
        <span className="rq-card-meta">{sub}</span>
        {size === 'large' && item.anime?.studios.length ? <span className="rq-card-meta">{item.anime.studios.slice(0, 2).join(', ')}</span> : null}
      </button>
      <span className="rq-card-actions">
        {canAskFor(shown) ? <button aria-label={`Request ${item.title}`} className="rq-quick" onClick={() => ask(item)} type="button"><Plus aria-hidden="true" /></button> : null}
        <button aria-label={`Trailer for ${item.title}`} className="rq-quick" onClick={() => openTitle(item, { trailer: true })} type="button"><Clapperboard aria-hidden="true" /></button>
      </span>
    </div>
  );
}

const POINTER = '(hover: hover) and (pointer: fine)';

/** A horizontally scrolling, snapping rail with arrow buttons on pointer devices and d-pad focus (data-focus-row). */
export function Rail({ id, title, kicker, seeAll, children, prominent = false, count }: { id: string; title: string; kicker?: string; seeAll?: () => void; children: ReactNode; prominent?: boolean; count: number }) {
  const row = useRef<HTMLUListElement>(null);
  const [ends, setEnds] = useState({ left: false, right: false });
  const [pointer] = useState(() => typeof window !== 'undefined' && Boolean(window.matchMedia?.(POINTER).matches));
  const measure = () => {
    const element = row.current;
    if (element) setEnds({ left: element.scrollLeft > 0, right: element.scrollLeft + element.clientWidth < element.scrollWidth - 1 });
  };
  useEffect(() => { if (pointer) measure(); }, [pointer, count]);
  const scroll = (direction: 1 | -1) => row.current?.scrollBy({ left: direction * row.current.clientWidth * 0.9, behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
  const headingId = `rq-rail-${id}`;
  return (
    <section aria-labelledby={headingId} className={`rq-rail ${prominent ? 'is-prominent' : ''}`}>
      <div className="rq-rail-head">
        <div>
          {kicker ? <p className="g-label rq-kicker">{kicker}</p> : null}
          <h2 id={headingId}>{title}</h2>
        </div>
        {seeAll ? <button className="g-text-button" data-focus-item onClick={seeAll} type="button">See all</button> : null}
        {pointer && (ends.left || ends.right) ? (
          <span className="rq-rail-buttons">
            <button aria-label={`Scroll ${title} left`} className="g-icon-button" disabled={!ends.left} onClick={() => scroll(-1)} tabIndex={-1} type="button"><ChevronLeft aria-hidden="true" /></button>
            <button aria-label={`Scroll ${title} right`} className="g-icon-button" disabled={!ends.right} onClick={() => scroll(1)} tabIndex={-1} type="button"><ChevronRight aria-hidden="true" /></button>
          </span>
        ) : null}
      </div>
      <ul className="rq-row" data-focus-row onScroll={pointer ? measure : undefined} ref={row}>{children}</ul>
    </section>
  );
}

export function CardRail({ id, title, items, seeAll, prominent, kicker }: { id: string; title: string; items: CatalogItem[]; seeAll?: () => void; prominent?: boolean; kicker?: string }) {
  if (!items.length) return null;
  return (
    <Rail count={items.length} id={id} kicker={kicker} prominent={prominent} seeAll={seeAll} title={title}>
      {items.map((item) => <li key={item.key}><CatalogCard item={item} size={prominent ? 'large' : 'normal'} /></li>)}
    </Rail>
  );
}

/** A rail-shaped placeholder: a title bar over a row of poster frames. */
export function RailSkeleton({ label }: { label: string }) {
  return <div className="rq-rail rq-rail-skeleton"><div aria-hidden="true" className="rq-rail-head"><span className="rq-bar" /></div><Skeleton count={8} label={label} shape="poster" /></div>;
}

/** The hero's placeholder: full width at hero height, with the title and action bars where they will land. */
export function HeroSkeleton() {
  return <div aria-hidden="true" className="rq-hero rq-hero-skeleton"><span className="rq-bar is-kicker" /><span className="rq-bar is-title" /><span className="rq-bar" /></div>;
}

/** A poster grid that asks for the next page as its end comes into view, with a Load more button for keyboards and TVs. */
export function InfiniteGrid({ queryKey, load, emptyTitle }: { queryKey: string; load: (page: number, signal: AbortSignal) => Promise<CatalogPage>; emptyTitle: string }) {
  const [state, setState] = useState<{ key: string; items: CatalogItem[]; page: number; total: number; error: unknown; loading: boolean }>({ key: '', items: [], page: 0, total: 1, error: null, loading: false });
  const sentinel = useRef<HTMLDivElement>(null);
  const loadRef = useRef(load);
  loadRef.current = load;
  const controller = useRef<AbortController | null>(null);
  const fetchPage = (page: number, fresh: boolean) => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setState((value) => ({ ...(fresh ? { key: queryKey, items: [], page: 0, total: 1 } : value), key: queryKey, error: null, loading: true }));
    loadRef.current(page, current.signal).then(
      (result) => { if (!current.signal.aborted) setState((value) => ({ key: queryKey, items: dedupe([...value.items, ...result.items]), page: result.page, total: result.total_pages, error: null, loading: false })); },
      (error) => { if (!current.signal.aborted) setState((value) => ({ ...value, error, loading: false })); },
    );
  };
  useEffect(() => { fetchPage(1, true); return () => controller.current?.abort(); }, [queryKey]); // eslint-disable-line react-hooks/exhaustive-deps -- the key names the query
  const more = state.key === queryKey && !state.loading && !state.error && state.page < state.total;
  useEffect(() => {
    const node = sentinel.current;
    if (!more || !node || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver((entries) => { if (entries.some((entry) => entry.isIntersecting)) fetchPage(state.page + 1, false); }, { rootMargin: '0px 0px 600px 0px' });
    observer.observe(node);
    return () => observer.disconnect();
  }); // eslint-disable-line react-hooks/exhaustive-deps -- re-armed after every page
  if (state.error && !state.items.length) return <LoadProblem error={state.error} onRetry={() => fetchPage(1, true)} what="these titles" />;
  if (state.key === queryKey && !state.loading && !state.items.length) return <EmptyState title={emptyTitle} />;
  return (
    <>
      <ul className="rq-grid" data-focus-row>{state.items.map((item) => <li key={item.key}><CatalogCard item={item} /></li>)}</ul>
      {state.loading ? <Skeleton count={6} label="Loading more titles" shape="poster" /> : null}
      {state.error ? <ErrorState onRetry={() => fetchPage(state.page + 1, false)} title="Lumina couldn't load more titles." /> : null}
      {more ? <div className="rq-more" ref={sentinel}><Button data-focus-item onClick={() => fetchPage(state.page + 1, false)}>Load more</Button></div> : null}
    </>
  );
}

const dedupe = (items: CatalogItem[]) => [...new Map(items.map((item) => [item.key, item])).values()];

/** Requests switched off, no TMDB key, or an ordinary failure: a calm explanation, admins get the way to fix it. */
export function LoadProblem({ error, onRetry, what }: { error: unknown; onRetry: () => void; what: string }) {
  const { isAdmin, openSettings } = useRequests();
  const code = errorCode(error);
  if (code === 'requests_disabled' || code === 'tmdb_not_configured') {
    const title = code === 'requests_disabled' ? 'Requests are resting' : 'Requests need one more thing';
    const body = code === 'requests_disabled'
      ? (isAdmin ? 'Turn Requests on and connect Sonarr and Radarr, and everyone in the household can ask for films, series and anime here.' : 'An admin hasn’t turned Requests on for this household yet.')
      : (isAdmin ? 'Lumina needs a TMDB key to show what’s out there. Add one, then come back.' : 'An admin needs to finish setting up Requests before the catalog can show here.');
    return <EmptyState action={isAdmin ? <Button onClick={openSettings} variant="primary">Open Settings → Requests</Button> : undefined} body={body} size="page" title={title} />;
  }
  return <ErrorState body={code ? errorCopy(code, '') || undefined : undefined} onRetry={onRetry} title={`Lumina couldn't load ${what}.`} />;
}
