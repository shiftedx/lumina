/** A catalog title: backdrop hero with the trailer playing inline, seasons, cast, anime facts, relations and more like it. */
import { ArrowLeft, Clapperboard, Library, Plus } from 'lucide-react';
import { useEffect, useState } from 'react';

import { Button, Skeleton } from '../../ui';
import { type CatalogDetail, getCatalogTitle, type Kind } from './requestsApi';
import { useLoad, useNow, useRequests } from './requestsContext';
import { canAskFor, countdown, formatLabel, metaLine, stateLabel, unrequestableCopy } from './requestsModel';
import { accentStyle, Art, CardRail, LoadProblem, Rail, StatusRibbon } from './parts';
import { TrailerStage } from './TrailerStage';

// a length heuristic for the More toggle (about five lines at reading width); measure overflow if it misfires.
const LONG_OVERVIEW = 360;

// A card's Trailer quick action opens the title with its trailer already playing.
let trailerOnOpen: string | null = null;
export const playTrailerOnOpen = (key: string) => { trailerOnOpen = key; };

function AnimeFacts({ detail }: { detail: CatalogDetail }) {
  const now = useNow();
  const anime = detail.anime;
  if (!anime) return null;
  const facts: Array<[string, string]> = [
    ['Studio', anime.studios.join(', ')], ['Format', anime.format ? formatLabel(anime.format) : ''], ['Episodes', anime.episodes ? String(anime.episodes) : ''],
    ['Season', anime.season && anime.season_year ? `${anime.season[0]}${anime.season.slice(1).toLowerCase()} ${anime.season_year}` : ''],
    ['Score', anime.score ? `${anime.score}%` : ''],
    ['Next episode', anime.next_episode ? `Ep ${anime.next_episode.number}, ${countdown(anime.next_episode.airing_at, now)}` : ''],
  ];
  return <dl className="rq-facts">{facts.filter(([, value]) => value).map(([term, value]) => <div key={term}><dt className="g-label">{term}</dt><dd>{value}</dd></div>)}</dl>;
}

export function TitlePage({ kind, id }: { kind: Kind; id: number }) {
  const { ask, openLibrary, statusOf } = useRequests();
  const title = useLoad(`${kind}:${id}`, (signal) => getCatalogTitle(kind, id, signal));
  const [trailer, setTrailer] = useState(false);
  const [more, setMore] = useState(false);
  const detail = title.data;
  useEffect(() => {
    if (detail && trailerOnOpen === detail.key) { trailerOnOpen = null; if (detail.trailers.length) setTrailer(true); }
  }, [detail]);
  if (title.error) return <LoadProblem error={title.error} onRetry={title.reload} what="this title" />;
  if (!detail) return <Skeleton label="Loading the title" shape="still" />;
  const status = statusOf(detail);
  const shown = { ...detail, status };
  const director = detail.crew.find((person) => person.job === 'Director' || person.job === 'Creator');
  const seasons = detail.seasons.filter((season) => season.number > 0);
  return (
    <article className="rq-title">
      <div className="rq-title-hero">
        {trailer ? <TrailerStage onClose={() => setTrailer(false)} poster={detail.backdrop_url} title={detail.title} trailers={detail.trailers} /> : (
          <>
            <span className="rq-hero-art is-still"><Art eager src={detail.backdrop_url} /></span>
            <span aria-hidden="true" className="rq-hero-scrim" />
          </>
        )}
      </div>
      <div className="rq-title-body">
        <span className="rq-frame rq-title-poster" style={accentStyle(detail)}><Art alt={`${detail.title} poster`} eager fallback={detail.title} src={detail.poster_url} /></span>
        <div className="rq-title-copy">
          <div className="rq-title-head">
            <button className="g-text-button rq-back" onClick={() => window.history.back()} type="button"><ArrowLeft aria-hidden="true" />Back</button>
            <h1 className="rq-title-name" tabIndex={-1}>{detail.logo_url ? <Art alt={detail.title} className="rq-hero-logo" eager fallback={detail.title} src={detail.logo_url} /> : detail.title}</h1>
            <p className="g-label rq-hero-meta">{[metaLine(detail), detail.runtime ? `${detail.runtime} min` : null, detail.certification].filter(Boolean).join(' · ')}</p>
          </div>
          {detail.tagline ? <p className="rq-tagline">{detail.tagline}</p> : null}
          {detail.overview ? (
            <>
              <p className={`rq-overview ${more ? 'is-open' : ''}`} id="rq-overview">{detail.overview}</p>
              {detail.overview.length > LONG_OVERVIEW ? <button aria-controls="rq-overview" aria-expanded={more} className="g-text-button rq-more-text" onClick={() => setMore(!more)} type="button">{more ? 'Less' : 'More'}</button> : null}
            </>
          ) : null}
          {director || detail.networks.length ? <p className="rq-muted">{[director ? `${director.job} ${director.name}` : null, detail.networks.join(', ') || null].filter(Boolean).join(' · ')}</p> : null}
          <div className="rq-hero-actions" data-focus-row>
            {canAskFor(shown) ? <Button data-focus-item icon={<Plus />} onClick={() => ask(detail)} variant="primary">{status.state === 'partially_available' ? 'Request more' : 'Request'}</Button> : null}
            {status.state === 'available' && status.library_title_id ? <Button data-focus-item icon={<Library />} onClick={() => openLibrary(status.library_title_id!)} variant="primary">Open in Library</Button> : null}
            {status.state !== 'none' && status.state !== 'available' ? <StatusRibbon status={status} /> : null}
            {detail.trailers.length && !trailer ? <Button data-focus-item icon={<Clapperboard />} onClick={() => setTrailer(true)}>Trailer</Button> : null}
          </div>
          {detail.requestable === false ? <p className="rq-muted">{unrequestableCopy(detail.unrequestable_reason)}</p> : null}
          <AnimeFacts detail={detail} />
        </div>
      </div>
      {seasons.length ? (
        <Rail count={seasons.length} id="seasons" title="Seasons">
          {seasons.map((season) => {
            const state = (season as { status?: Parameters<typeof stateLabel>[0] }).status ?? (status.state === 'available' ? 'available' : 'none');
            return (
              <li className="rq-season" key={season.number}>
                <span className="rq-frame"><Art fallback={season.name} src={season.poster_url ?? detail.poster_url} /></span>
                <span className="rq-card-title">{season.name}</span>
                <span className="rq-card-meta">{[`${season.episode_count} ep`, season.air_date?.slice(0, 4)].filter(Boolean).join(' · ')}</span>
                {state !== 'none' ? <span className={`rq-season-state is-${state}`}>{stateLabel(state)}</span> : null}
              </li>
            );
          })}
        </Rail>
      ) : null}
      {detail.cast.length ? (
        <Rail count={detail.cast.length} id="cast" title="Cast">
          {detail.cast.slice(0, 24).map((person) => (
            <li className="rq-person" key={`${person.name}:${person.character ?? ''}`}>
              <span className="rq-person-photo"><Art fallback={person.name.split(' ').map((part) => part[0]).slice(0, 2).join('')} src={person.profile_url} /></span>
              <span className="rq-card-title">{person.name}</span>
              {person.character ? <span className="rq-card-meta">{person.character}</span> : null}
            </li>
          ))}
        </Rail>
      ) : null}
      <CardRail id="relations" items={detail.anime_relations ?? []} title="Related" />
      <CardRail id="recommendations" items={detail.recommendations} title="You might also like" />
      <CardRail id="similar" items={detail.similar} title="Similar" />
    </article>
  );
}
