/** Anime: the season switcher, sort and format chips, this week's airing schedule, a big poster grid, and the extra rails. */
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useState } from 'react';

import { IconButton, Skeleton } from '../../ui';
import { type AnimeSeason, type CatalogItem, type CatalogPage, getAnimeSchedule, getAnimeSeason, getCatalogList } from './requestsApi';
import { useLoad, useNow, useRequests } from './requestsContext';
import { countdown, seasonLabel, seasonOf, shiftSeason } from './requestsModel';
import { Chips } from './Browse';
import { Art, CardRail, InfiniteGrid, RailSkeleton } from './parts';

const SECTIONS = [['this_season', 'This season'], ['next_season', 'Next season'], ['trending', 'Trending'], ['popular', 'Popular'], ['top', 'All-time top'], ['movies', 'Films']] as const;
const SORTS = [['popularity', 'Popular'], ['score', 'Score'], ['title', 'A–Z']] as const;
const FORMATS = [['tv', 'TV'], ['movie', 'Film'], ['ova', 'OVA · ONA']] as const;
type Sort = (typeof SORTS)[number][0];
type Format = (typeof FORMATS)[number][0];

export const formatMatches = (item: CatalogItem, format: Format | undefined) => {
  const value = item.anime?.format ?? '';
  if (!format) return true;
  if (format === 'tv') return value === 'TV' || value === 'TV_SHORT';
  if (format === 'movie') return value === 'MOVIE';
  return value === 'OVA' || value === 'ONA' || value === 'SPECIAL';
};

const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
const dayName = (date: string, today: string) => (date === today ? 'Today' : new Date(`${date}T12:00:00`).toLocaleDateString([], { weekday: 'short', day: 'numeric' }));
/** The day's entries, most popular first, capped unless the day is opened up. */
export const SCHEDULE_CAP = 6;
export const scheduleEntries = <T extends { item: CatalogItem }>(entries: T[], all: boolean) => {
  const sorted = [...entries].sort((a, b) => (b.item.anime?.popularity ?? 0) - (a.item.anime?.popularity ?? 0));
  return all ? sorted : sorted.slice(0, SCHEDULE_CAP);
};
const localDate = (now: number) => { const date = new Date(now); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`; };

/** Seven day columns, today first and marked; each entry opens its title. */
export function AiringSchedule() {
  const { openTitle } = useRequests();
  const now = useNow();
  const schedule = useLoad('anime:schedule', (signal) => getAnimeSchedule(signal));
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const toggle = (date: string) => setOpen((current) => { const next = new Set(current); if (next.has(date)) next.delete(date); else next.add(date); return next; });
  if (schedule.error) return null; // the grid below still works; the schedule is a bonus
  if (!schedule.data) return <Skeleton count={3} label="Loading this week's schedule" shape="row" />;
  const today = localDate(now);
  return (
    <section aria-labelledby="rq-schedule" className="rq-schedule">
      <h2 id="rq-schedule">Airing this week</h2>
      <ol className="rq-schedule-days">
        {schedule.data.days.map((day) => (
          <li className={`rq-schedule-day ${day.date === today ? 'is-today' : ''}`} key={day.date}>
            <h3 aria-current={day.date === today ? 'date' : undefined}>{dayName(day.date, today)}</h3>
            {day.entries.length ? (
              <ul data-focus-row>
                {scheduleEntries(day.entries, open.has(day.date)).map((entry) => (
                  <li key={`${entry.item.key}:${entry.episode}`}>
                    <button className="rq-schedule-entry" data-focus-item onClick={() => openTitle(entry.item)} type="button">
                      <span className="rq-frame"><Art fallback={entry.item.title} src={entry.item.poster_url} /></span>
                      <span className="rq-schedule-copy">
                        <span className="rq-schedule-title">{entry.item.title}</span>
                        <span className="rq-muted">{time(entry.airing_at)} · Ep {entry.episode}</span>
                        <span className="rq-countdown">{countdown(entry.airing_at, now)}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : <p className="rq-muted">Nothing new</p>}
            {day.entries.length > SCHEDULE_CAP ? (
              <button aria-expanded={open.has(day.date)} className="g-text-button rq-schedule-more" data-focus-item onClick={() => toggle(day.date)} type="button">
                {open.has(day.date) ? 'Show fewer' : `Show all ${day.entries.length}`}
              </button>
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

/** The season's six most popular titles, large, above the schedule: the current season is the prominent thing. */
function SeasonSpotlight({ shown }: { shown: { season: AnimeSeason; year: number } }) {
  const list = useLoad(`spotlight:${shown.season}:${shown.year}`, (signal) => getAnimeSeason({ ...shown, sort: 'popularity', page: 1 }, signal));
  if (!list.data) return list.error ? null : <RailSkeleton label="Loading the season's highlights" />;
  const top = [...list.data.items].sort((a, b) => (b.anime?.popularity ?? 0) - (a.anime?.popularity ?? 0)).slice(0, 6);
  return <CardRail id="season-spotlight" items={top} kicker="Most watched" prominent title={`The best of ${seasonLabel(shown)}`} />;
}

function ExtraRail({ section, title }: { section: string; title: string }) {
  const { go } = useRequests();
  const list = useLoad(`anime:${section}`, (signal) => getCatalogList('anime', { section, page: 1 }, signal));
  return list.data ? <CardRail id={`anime-${section}`} items={list.data.items} seeAll={() => go({ surface: 'requests', view: 'anime', section })} title={title} /> : null;
}

export function AnimePage({ section, genre }: { section?: string; genre?: string }) {
  const { go } = useRequests();
  const current = SECTIONS.some(([key]) => key === section) ? section! : 'this_season';
  const now = seasonOf(new Date());
  const [offset, setOffset] = useState(current === 'next_season' ? 1 : 0);
  const [sort, setSort] = useState<Sort>('popularity');
  const [format, setFormat] = useState<Format | undefined>();
  const seasonal = current === 'this_season' || current === 'next_season';
  const shown = shiftSeason(now, offset);
  const setSection = (next?: string) => {
    setOffset(next === 'next_season' ? 1 : 0);
    go({ surface: 'requests', view: 'anime', ...(next && next !== 'this_season' ? { section: next } : {}), ...(genre ? { genre } : {}) }, true);
  };
  const loadSeason = (page: number, signal: AbortSignal): Promise<CatalogPage> => getAnimeSeason({ ...shown, sort, page }, signal).then((result) => ({
    // the season endpoint has no total; a short page ends it. The format filter is client-side over each page.
    items: result.items.filter((item) => formatMatches(item, format)), page, total_pages: result.items.length ? page + 1 : page,
  }));
  return (
    <>
      <Chips label="Anime section" onChange={setSection} options={SECTIONS} value={current} />
      {seasonal ? (
        <>
          <div className="rq-season-switch">
            <IconButton icon={<ChevronLeft />} label={`Show ${seasonLabel(shiftSeason(shown, -1))}`} onClick={() => setOffset(offset - 1)} />
            <span className="rq-muted rq-season-side" aria-hidden="true">{seasonLabel(shiftSeason(shown, -1))}</span>
            <h2 aria-live="polite" className="rq-season-name">{seasonLabel(shown)}</h2>
            <span className="rq-muted rq-season-side" aria-hidden="true">{seasonLabel(shiftSeason(shown, 1))}</span>
            <IconButton icon={<ChevronRight />} label={`Show ${seasonLabel(shiftSeason(shown, 1))}`} onClick={() => setOffset(offset + 1)} />
          </div>
          <div className="rq-filter-line">
            <Chips label="Sort" onChange={(next) => setSort(next ?? 'popularity')} options={SORTS} value={sort} />
            <Chips label="Format" onChange={setFormat} options={FORMATS} value={format} />
          </div>
          <SeasonSpotlight shown={shown} />
          {offset === 0 ? <AiringSchedule /> : null}
          <h2 className="rq-grid-title">Everything in {seasonLabel(shown)}</h2>
          <InfiniteGrid emptyTitle={`Nothing announced for ${seasonLabel(shown)} yet.`} load={loadSeason} queryKey={`season:${shown.season}:${shown.year}:${sort}:${format ?? ''}`} />
          <ExtraRail section="trending" title="Trending now" />
          <ExtraRail section="top" title="All-time top" />
          <ExtraRail section="movies" title="Anime films" />
        </>
      ) : (
        <InfiniteGrid emptyTitle="Nothing here yet." load={(page, signal) => getCatalogList('anime', { section: current, genre, page }, signal)} queryKey={`anime:${current}:${genre ?? ''}`} />
      )}
    </>
  );
}
