/**
 * A wall's filters: a modal <dialog> sliding in from the right on desktop and tablet, the same dialog as
 * a bottom sheet on phones, where the quick chips move in too. The choice applies on "Show n titles" or any close.
 * The wall decides the facets and whether Resolution and the chips exist.
 */
import { useEffect, useId, useRef, useState } from 'react';

import { getTitleFacets, listTitles, type TitleListQuery } from '../../api';
import type { TitleFacets, TitleResolution } from '../../types';
import { countNoun, RESOLUTIONS, type WallKind, type WallQuery, wallListQuery, WALLS, wallYear } from './galleryModel';

export const COUNT_DEBOUNCE_MS = 300;
export const CHIPS = [['unwatched', 'Unwatched'], ['progress', 'In progress'], ['fav', 'Favorites']] as const;
const RESOLUTION_LABELS: Record<TitleResolution, string> = { '4k': '4K', '1080p': '1080p', '720p': '720p', sd: 'SD' };
const MAX_GENRES = 10;

type FilterDrawerProps = { wall: WallKind; query: WallQuery; phone: boolean; onApply: (next: WallQuery) => void; onClose: () => void };

export function FilterDrawer({ wall, query, phone, onApply, onClose }: FilterDrawerProps) {
  const def = WALLS[wall];
  const dialog = useRef<HTMLDialogElement>(null);
  const headingId = useId();
  const genreLimitId = useId();
  const [pending, setPending] = useState(query);
  const [years, setYears] = useState({ from: query.from === null ? '' : String(query.from), to: query.to === null ? '' : String(query.to) });
  const [facets, setFacets] = useState<TitleFacets | null>(null);
  const [facetsFailed, setFacetsFailed] = useState(false);
  const [count, setCount] = useState<number | null>(null);
  const chosen: WallQuery = { ...pending, from: wallYear(years.from), to: wallYear(years.to) };
  const latest = useRef(chosen);
  latest.current = chosen;
  const countKey = JSON.stringify(wallListQuery(wall, chosen));
  const resolutions = def.facets?.type !== 'album';

  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
  }, []);

  useEffect(() => {
    const asked = WALLS[wall].facets;
    if (!asked) return undefined;
    const controller = new AbortController();
    getTitleFacets(asked, { signal: controller.signal }).then(setFacets, () => { if (!controller.signal.aborted) setFacetsFailed(true); });
    return () => controller.abort();
  }, [wall]);

  // "Show n titles": the total of the pending choice, asked 300 ms after it last changed, with limit=1.
  useEffect(() => {
    setCount(null);
    const controller = new AbortController();
    const timer = setTimeout(() => {
      listTitles({ ...(JSON.parse(countKey) as TitleListQuery), limit: 1 }, { signal: controller.signal }).then((page) => setCount(page.total ?? null), () => undefined);
    }, COUNT_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [countKey]);

  const toggleGenre = (name: string) => setPending((current) => ({
    ...current,
    genre: current.genre.includes(name) ? current.genre.filter((genre) => genre !== name) : [...current.genre, name].slice(0, MAX_GENRES),
  }));
  const toggleResolution = (value: TitleResolution) => setPending((current) => ({
    ...current,
    res: current.res.includes(value) ? current.res.filter((entry) => entry !== value) : [...current.res, value],
  }));
  const clearAll = () => {
    setPending((current) => ({ ...current, genre: [], res: [], ...(phone ? { unwatched: false, progress: false, fav: false } : {}) }));
    setYears({ from: '', to: '' });
  };
  const resolutionCount = (value: TitleResolution) => facets?.resolutions.find((entry) => entry.value === value)?.count ?? 0;

  return (
    <dialog aria-labelledby={headingId} className="g-drawer" onClose={() => { onApply(latest.current); onClose(); }} ref={dialog}>
      <div className="g-drawer-body">
        <span aria-hidden="true" className="g-sheet-handle" />
        <h2 className="g-drawer-title" id={headingId}>Filters</h2>
        {phone && def.chips ? (
          <fieldset>
            <legend className="g-label">Show</legend>
            <div className="g-chips">
              {CHIPS.map(([chip, label]) => (
                <button aria-pressed={pending[chip]} className="g-chip g-button-text" key={chip} onClick={() => setPending((current) => ({ ...current, [chip]: !current[chip] }))} type="button">{label}</button>
              ))}
            </div>
          </fieldset>
        ) : null}
        <fieldset>
          <legend className="g-label">Genre</legend>
          {facets
            ? facets.genres.map((genre) => {
              const disabled = !pending.genre.includes(genre.name) && pending.genre.length >= MAX_GENRES;
              return (
                <label className="g-check" key={genre.name}>
                  <input aria-describedby={disabled ? genreLimitId : undefined} checked={pending.genre.includes(genre.name)} disabled={disabled} onChange={() => toggleGenre(genre.name)} type="checkbox" />
                  <span>{genre.name}</span>{' '}
                  <small>{genre.count.toLocaleString()}</small>
                </label>
              );
            })
            : <p className="g-drawer-note">{facetsFailed ? 'Filters are unavailable right now.' : 'Loading…'}</p>}
          {facets && pending.genre.length >= MAX_GENRES ? <p className="g-drawer-note" id={genreLimitId}>You can choose up to {MAX_GENRES} genres.</p> : null}
        </fieldset>
        <fieldset>
          <legend className="g-label">Year</legend>
          <div className="g-years">
            <label>
              <span className="g-label">From</span>
              <input className="g-input" inputMode="numeric" max={2100} min={1870} onChange={(event) => setYears((current) => ({ ...current, from: event.target.value }))} placeholder={facets?.years ? String(facets.years.min) : undefined} type="number" value={years.from} />
            </label>
            <label>
              <span className="g-label">To</span>
              <input className="g-input" inputMode="numeric" max={2100} min={1870} onChange={(event) => setYears((current) => ({ ...current, to: event.target.value }))} placeholder={facets?.years ? String(facets.years.max) : undefined} type="number" value={years.to} />
            </label>
          </div>
        </fieldset>
        {resolutions ? (
          <fieldset>
            <legend className="g-label">Resolution</legend>
            {RESOLUTIONS.map((value) => (
              <label className="g-check" key={value}>
                <input checked={pending.res.includes(value)} onChange={() => toggleResolution(value)} type="checkbox" />
                <span>{RESOLUTION_LABELS[value]}</span>{' '}
                <small>{resolutionCount(value).toLocaleString()}</small>
              </label>
            ))}
          </fieldset>
        ) : null}
      </div>
      <footer className="g-drawer-footer">
        <button className="g-button g-button-text" onClick={clearAll} type="button">Clear all</button>
        <button className="g-button is-primary g-button-text" onClick={() => dialog.current?.close()} type="button">
          <span aria-live="polite">{count === null ? `Show ${def.noun[1]}` : `Show ${countNoun(count, def.noun)}`}</span>
        </button>
      </footer>
    </dialog>
  );
}
