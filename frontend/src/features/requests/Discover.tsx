/** Discover: the cinematic hero, then "This season in anime" and the other rails, in the server's order. */
import { Clapperboard, Plus } from 'lucide-react';
import { useState } from 'react';

import { parseRoute } from '../../app/routes';
import { Button, useToast } from '../../ui';
import { type CatalogItem, getCatalogHome, getCatalogTitle, type Rail, type Trailer } from './requestsApi';
import { useLoad, useRequests } from './requestsContext';
import { canAskFor, catalogId, KIND_LABEL, metaLine } from './requestsModel';
import { Art, CardRail, HeroSkeleton, LoadProblem, RailSkeleton, StatusRibbon } from './parts';
import { TrailerStage } from './TrailerStage';

/** A rail's See all (`/requests/anime?section=next_season`) as an in-tab navigation; anything else is ignored. */
export function useSeeAll() {
  const { go } = useRequests();
  return (path?: string) => {
    if (!path) return undefined;
    const url = new URL(path, 'http://lumina.invalid');
    const route = parseRoute(url.pathname, url.search);
    return route.surface === 'requests' ? () => go(route) : undefined;
  };
}

export function Hero({ items }: { items: CatalogItem[] }) {
  const { ask, statusOf } = useRequests();
  const toast = useToast();
  const [index, setIndex] = useState(0);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [trailers, setTrailers] = useState<Trailer[] | null>(null);
  const [loadingTrailer, setLoadingTrailer] = useState(false);
  const item = items[index % items.length];
  if (!item) return null;
  const status = statusOf(item);
  const paused = hovered || focused || trailers !== null;
  const show = (next: number) => { setIndex((next + items.length) % items.length); setTrailers(null); };

  async function playTrailer() {
    const id = catalogId(item);
    if (!id) return;
    setLoadingTrailer(true);
    try {
      const detail = await getCatalogTitle(item.kind, id);
      if (detail.trailers.length) setTrailers(detail.trailers);
      else toast({ tone: 'info', message: `There's no trailer for ${item.title} yet.` });
    } catch {
      toast({ tone: 'error', message: "Lumina couldn't load the trailer." });
    } finally {
      setLoadingTrailer(false);
    }
  }

  return (
    <section
      aria-label="Featured"
      aria-roledescription="carousel"
      className={`rq-hero ${paused ? 'is-paused' : ''}`}
      onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocused(false); }}
      onFocus={() => setFocused(true)}
      onPointerEnter={() => setHovered(true)}
      onPointerLeave={() => setHovered(false)}
    >
      <div aria-label={`${index + 1} of ${items.length}`} aria-roledescription="slide" className="rq-hero-slide" key={item.key} role="group">
        {trailers ? <TrailerStage onClose={() => setTrailers(null)} poster={item.backdrop_url} title={item.title} trailers={trailers} /> : (
          <>
            <span className="rq-hero-art"><Art eager src={item.backdrop_url ?? item.poster_url} /></span>
            <span aria-hidden="true" className="rq-hero-scrim" />
          </>
        )}
        <div className="rq-hero-text">
          <p className="g-label rq-kicker">{KIND_LABEL[item.kind]}</p>
          <h2 className="rq-hero-title">{item.logo_url ? <Art alt={item.title} className="rq-hero-logo" eager fallback={item.title} src={item.logo_url} /> : item.title}</h2>
          <p className="g-label rq-hero-meta">{metaLine(item)}</p>
          {item.overview ? <p className="rq-hero-overview">{item.overview}</p> : null}
          <div className="rq-hero-actions" data-focus-row>
            {canAskFor({ ...item, status }) ? <Button data-focus-item icon={<Plus />} onClick={() => ask(item)} variant="primary">Request</Button> : <StatusRibbon status={status} />}
            {trailers ? null : <Button busy={loadingTrailer} data-focus-item icon={<Clapperboard />} onClick={() => void playTrailer()}>Watch trailer</Button>}
          </div>
        </div>
      </div>
      {items.length > 1 ? (
        <div className="rq-pips">
          {items.map((entry, position) => (
            <button aria-current={position === index || undefined} aria-label={`Show ${entry.title}`} className={`rq-pip ${position === index ? 'is-current' : ''}`} key={entry.key} onClick={() => show(position)} type="button">
              {/* The fill runs for one slide's time; its end advances the carousel (paused on hover/focus, absent under reduced motion). */}
              <span className="rq-pip-fill" onAnimationEnd={() => show(index + 1)} />
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}

const PROMINENT = 'anime_this_season';

export function Discover() {
  const home = useLoad('home', (signal) => getCatalogHome(signal));
  const seeAll = useSeeAll();
  if (home.error) return <LoadProblem error={home.error} onRetry={home.reload} what="Discover" />;
  if (!home.data) return <><HeroSkeleton /><RailSkeleton label="Loading Discover" /><RailSkeleton label="Loading more rails" /><RailSkeleton label="Loading more rails" /></>;
  const rails: Rail[] = home.data.rails.filter((rail) => rail.items.length);
  return (
    <>
      {home.data.hero.length ? <Hero items={home.data.hero} /> : null}
      {rails.map((rail) => (
        <CardRail
          id={rail.key}
          items={rail.items}
          key={rail.key}
          kicker={rail.key === PROMINENT ? 'Now airing' : undefined}
          prominent={rail.key === PROMINENT}
          seeAll={seeAll(rail.see_all)}
          title={rail.title}
        />
      ))}
    </>
  );
}
