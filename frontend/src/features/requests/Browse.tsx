/** Films and Series: section chips, genre chips and an infinite grid; and the search results. Section and genre live in the URL. */
import { getCatalogList, getGenres, searchCatalog } from './requestsApi';
import { useLoad, useRequests } from './requestsContext';
import { InfiniteGrid } from './parts';

const SECTIONS = {
  movies: [['trending', 'Trending'], ['popular', 'Popular'], ['upcoming', 'Upcoming'], ['now_playing', 'In cinemas'], ['top_rated', 'Top rated']],
  shows: [['trending', 'Trending'], ['popular', 'Popular'], ['on_the_air', 'On the air'], ['airing_today', 'Airing today'], ['top_rated', 'Top rated']],
} as const;

export function Chips<T extends string>({ label, options, value, onChange }: { label: string; options: ReadonlyArray<readonly [T, string]>; value: T | undefined; onChange: (value: T | undefined) => void }) {
  return (
    <div aria-label={label} className="rq-chips" data-focus-row role="group">
      {options.map(([key, name]) => <button aria-pressed={key === value} className="g-chip" data-focus-item key={key} onClick={() => onChange(key === value ? undefined : key)} type="button">{name}</button>)}
    </div>
  );
}

export function Browse({ view, section, genre }: { view: 'movies' | 'shows'; section?: string; genre?: string }) {
  const { go } = useRequests();
  const kind = view === 'movies' ? 'movie' : 'show';
  const sections = SECTIONS[view];
  const current = sections.some(([key]) => key === section) ? section : 'trending';
  const genres = useLoad(`genres:${kind}`, (signal) => getGenres(kind, signal));
  const set = (next: { section?: string; genre?: string }) => go({ surface: 'requests', view, ...(next.section && next.section !== 'trending' ? { section: next.section } : {}), ...(next.genre ? { genre: next.genre } : {}) }, true);
  return (
    <>
      <Chips label="Section" onChange={(next) => set({ section: next ?? 'trending', genre })} options={sections} value={current} />
      {genres.data?.genres.length ? <Chips label="Genre" onChange={(next) => set({ section: current, genre: next })} options={genres.data.genres.map((entry) => [entry.id, entry.name] as const)} value={genre} /> : null}
      <InfiniteGrid emptyTitle="Nothing here yet." load={(page, signal) => getCatalogList(kind, { section: current, genre, page }, signal)} queryKey={`${kind}:${current}:${genre ?? ''}`} />
    </>
  );
}

export function SearchResults({ query }: { query: string }) {
  return <InfiniteGrid emptyTitle={`Nothing matches “${query}”.`} load={(page, signal) => searchCatalog(query, page, signal)} queryKey={`search:${query}`} />;
}
