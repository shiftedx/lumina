/**
 * The gallery title page for movies, shows and boxsets, and the dispatcher for album and artist pages: a full-bleed
 * backdrop hero, an editorial body with a side column, The story so far, seasons and the episode row, key scenes, cast, extras and More like this.
 * Opening from a wall paints the clicked summary at once; the detail fills in the rest.
 */
import { type CSSProperties, Fragment, type KeyboardEvent, useCallback, useEffect, useRef, useState } from 'react';
import { ChevronLeft, MoreHorizontal } from 'lucide-react';

import { ApiRequestError, listSimilarTitles } from '../../api';
import { Artwork, resolveArtworkUrl } from '../../Artwork';
import { type DetailHeroLabel, recordMetric, sinceNavigation } from '../../perfMetrics';
import type { ExtraType, TitleCategory, TitleDetail, TitleExtra, TitlePerson, TitleSummary, TitleType } from '../../types';
import { formatDuration } from '../../utils';
import { FOCUS_TARGETS, moveFocus } from '../media/focusNav';
import { RecoCard, useRecoFilter, useRecoGeneration } from '../reco/recoFeedback';
import { titleTarget } from '../reco/recoModel';
import { RecoImpressionScope, useRecoImpressions } from '../reco/useRecoImpressions';
import { Menu } from '../../ui';
import { orderSeasons, seasonLabel, useFetched } from '../titles/titleModel';
import { AlbumPage } from './AlbumPage';
import { ArtistPage } from './ArtistPage';
import { EpisodeRow } from './EpisodeRow';
import { GalleryArt } from './GalleryArt';
import { backdropWidthFor, cardColour, cardTextColour, type GalleryTheme, renditionUrl } from './galleryModel';
import { prefetchImage } from './imageLoader';
import { KeyScenes } from './KeyScenes';
import { LENS_LABELS, titleLens } from './libraryLens';
import { PosterCard } from './PosterCard';
import { StorySoFar } from './StorySoFar';
import { TitleActions } from './TitleActions';
import { cachedTitle, forgetTitle, loadTitle, summaryFor } from './titleCache';
import { castOf, hasDropCap, metaLine, pageAccent, sideBlocks, storyVisible, usesLogo } from './titlePageModel';
import './gallery.css';
import './titlePage.css';

const EXTRA_LABELS: Record<ExtraType, string> = {
  trailer: 'Trailer', featurette: 'Featurette', behindthescenes: 'Behind the scenes', deletedscene: 'Deleted scene',
  interview: 'Interview', scene: 'Scene', short: 'Short', clip: 'Clip', other: 'Extra',
};
const TEXT_ENTRY = 'input, textarea, select, dialog, [contenteditable="true"]';
const POSTER_SIZES = '(max-width: 599px) 34vw, 168px';
const LOGO_SIZES = '(max-width: 599px) 80vw, 600px';
/** A logo sits on the page, never on a colour slot. */
const NO_SLOT = { colour: 'transparent', fromPalette: false };
const galleryTheme = (): GalleryTheme => (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark');
/** Back's words: the lens the title belongs to: Movies, Shows, Anime or Music. */
const lensLabel = (title: TitleSummary) => LENS_LABELS[titleLens(title.type, title.category)!];

export type GalleryTitlePageProps = {
  id: string;
  season: number | null;
  user: { role: string; can_edit_details?: boolean };
  /** Open the metadata editor for a title, season or episode id. */
  onEdit: (titleId: string) => void;
  /** Back's words when they differ from the title's own lens: where Back actually goes. */
  backLabel?: string;
  /** Back to the Library; the shown title's category is passed too. */
  onBack: (type: TitleType | null, category?: TitleCategory | null) => void;
  onPlay: (itemId: string, startSeconds?: number) => void;
  onOpenTitle: (title: TitleSummary) => void;
  onSearch: (query: string) => void;
  onSeason: (season: number) => void;
};

function SideColumn({ detail, onOpenTitle }: { detail: TitleDetail; onOpenTitle: (title: TitleSummary) => void }) {
  const blocks = sideBlocks(detail);
  const boxset = detail.boxset;
  if (!blocks.length && !boxset) return null;
  return (
    <dl className="t-side">
      {blocks.map((block) => <div key={block.label}><dt className="g-label">{block.label}</dt><dd>{block.text}</dd></div>)}
      {boxset ? <div><dt className="g-label">Part of</dt><dd><button className="g-text-button" data-focus-item onClick={() => onOpenTitle(boxset)} type="button">{boxset.name}</button></dd></div> : null}
    </dl>
  );
}

function Seasons({ detail, season, revision, onSeason, onPlay, canEdit, onEdit }: { detail: TitleDetail; season: number | null; revision: number; onSeason: (season: number) => void; onPlay: (itemId: string) => void; canEdit: boolean; onEdit: (titleId: string) => void }) {
  const seasons = orderSeasons(detail.children.filter((child) => child.type === 'season'));
  const numbers = seasons.map((entry) => entry.index_number ?? 0);
  const active = season !== null && numbers.includes(season) ? season : detail.play_next?.season_number ?? numbers.find(Boolean) ?? numbers[0] ?? null;
  if (!seasons.length || active === null) return null;

  function onTabKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if ((event.target as HTMLElement).getAttribute('role') !== 'tab') return; // the season menu keeps its own arrows
    const index = Math.max(0, numbers.indexOf(active ?? numbers[0]));
    const last = numbers.length - 1;
    const target = event.key === 'ArrowRight' ? (index + 1) % numbers.length : event.key === 'ArrowLeft' ? (index - 1 + numbers.length) % numbers.length : event.key === 'Home' ? 0 : event.key === 'End' ? last : null;
    if (target === null) return;
    event.preventDefault();
    onSeason(numbers[target]);
    requestAnimationFrame(() => document.getElementById(`season-tab-${numbers[target]}`)?.focus());
  }

  return (
    <section aria-labelledby="t-episodes-heading" className="t-section t-seasons">
      <h2 className="sr-only" id="t-episodes-heading">Episodes</h2>
      <div aria-label="Seasons" className="g-tabs t-tabs" onKeyDown={onTabKeyDown} role="tablist">
        {seasons.map((entry) => {
          const number = entry.index_number ?? 0;
          const selected = number === active;
          return <button aria-controls="t-episodes-panel" aria-selected={selected} className="g-label" data-focus-item id={`season-tab-${number}`} key={entry.id} onClick={() => onSeason(number)} role="tab" tabIndex={selected ? 0 : -1} type="button">{seasonLabel(number)}</button>;
        })}
        {canEdit ? (
          <Menu align="end" items={[{ kind: 'item', label: 'Edit season details', onSelect: () => { const id = seasons.find((entry) => (entry.index_number ?? 0) === active)?.id; if (id) onEdit(id); } }]}
            trigger={(props) => <button {...props} aria-label="More for season" className="g-icon-button" data-focus-item type="button"><MoreHorizontal aria-hidden="true" /></button>} />
        ) : null}
      </div>
      <div aria-labelledby={`season-tab-${active}`} id="t-episodes-panel" role="tabpanel">
        <EpisodeRow canEdit={canEdit} onEdit={onEdit} onPlay={onPlay} playNextId={detail.play_next?.id ?? null} revision={revision} season={active} seriesId={detail.id} />
      </div>
    </section>
  );
}

function Cast({ people, onSearch }: { people: TitlePerson[]; onSearch: (query: string) => void }) {
  if (!people.length) return null;
  return (
    <section aria-labelledby="t-cast-heading" className="t-section">
      <h2 className="t-h2" id="t-cast-heading">Cast</h2>
      <ul className="t-row" data-focus-row>
        {people.slice(0, 20).map((person, index) => (
          <li key={`${person.name}-${index}`}>
            <button className="t-person" data-focus-item onClick={() => onSearch(person.name)} type="button">
              <Artwork alt="" className="t-portrait" fallback={<span aria-hidden="true" className="t-monogram">{person.name.charAt(0)}</span>} src={person.image_url} />
              <span className="t-person-name">{person.name}</span>
              {person.role ? <span className="t-person-role g-label">{person.role}</span> : null}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Extras({ extras, onPlay }: { extras: TitleExtra[]; onPlay: (itemId: string) => void }) {
  if (!extras.length) return null;
  return (
    <section aria-labelledby="t-extras-heading" className="t-section">
      <h2 className="t-h2" id="t-extras-heading">Extras</h2>
      <ul className="t-extra-list" data-focus-row>
        {extras.map((extra) => (
          <li key={extra.item_id}>
            <button className="t-extra" data-focus-item onClick={() => onPlay(extra.item_id)} type="button">
              <span className="t-extra-name">{extra.name}</span>
              <span className="t-extra-meta g-label">{[EXTRA_LABELS[extra.extra_type], extra.duration_seconds ? formatDuration(extra.duration_seconds) : null].filter(Boolean).join(' · ')}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function PosterRow({ heading, headingId, titles, onOpenTitle }: { heading: string; headingId: string; titles: TitleSummary[]; onOpenTitle: (title: TitleSummary) => void }) {
  // More like this is a served list (its posters carry `reco`); In this collection is not, and is never filtered.
  const recommended = titles.some((entry) => entry.reco);
  const observe = useRecoImpressions(titles.find((entry) => entry.reco)?.reco?.list_id ?? null);
  const filtered = useRecoFilter(titles, titleTarget, true);
  const shown = recommended ? filtered : titles;
  if (!shown.length) return null;
  return (
    <RecoImpressionScope value={observe}>
      <section aria-labelledby={headingId} className="t-section">
        <h2 className="t-h2" id={headingId}>{heading}</h2>
        <ul className="t-row" data-focus-row>
          {shown.map((entry, index) => {
            const poster = <PosterCard className="t-row-poster" data-focus-item onOpen={onOpenTitle} position={index} priority={3} sizes={POSTER_SIZES} title={entry} />;
            return (
              <li key={entry.id}>
                {entry.reco
                  ? <RecoCard reco={entry.reco} target={titleTarget(entry)}>{poster}</RecoCard>
                  : poster}
              </li>
            );
          })}
        </ul>
      </section>
    </RecoImpressionScope>
  );
}

export function GalleryTitlePage({ id, season, user, backLabel, onBack, onEdit, onPlay, onOpenTitle, onSearch, onSeason }: GalleryTitlePageProps) {
  const [revision, setRevision] = useState(0);
  // Try again and reloads after a change bypass the 30 s cache; a first open may use what intent prefetch loaded.
  const title = useFetched<TitleDetail>(id, () => { if (revision) forgetTitle(id); return loadTitle(id); }, revision);
  const reload = () => setRevision((value) => value + 1);
  // Music has no "More like this": an album or artist never asks. A deep link waits for
  // its title to know which it is.
  const clicked = summaryFor(id)?.type;
  const shownType = clicked ?? title.data?.type;
  const recoGeneration = useRecoGeneration();
  const similar = useFetched(shownType === 'album' || shownType === 'artist' || !shownType ? null : `${id}:similar`, () => listSimilarTitles(id), recoGeneration);
  const [logoFailed, setLogoFailed] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const heroLabel = useRef<DetailHeroLabel>(summaryFor(id) ? 'click' : 'deep_link');
  const heroTimed = useRef(false);
  // A reload after a change that fails keeps the page and says so; only a first load that fails replaces it.
  const lastDetail = useRef<TitleDetail | null>(null);
  if (title.data) lastDetail.current = title.data;
  // It has no copy to patch, so a change made meanwhile refetches instead.
  const reloadFailed = Boolean(title.error) && lastDetail.current !== null;
  const data = title.data ?? (reloadFailed ? lastDetail.current : title.error ? null : cachedTitle(id));
  const failed = !data && Boolean(title.error);
  const shown: TitleSummary | null = data ?? summaryFor(id);
  const hasBackdrop = Boolean(shown?.backdrop);
  /** Back to the Library with the shown title's type and category. */
  const back = () => onBack(shown?.type ?? null, shown?.category ?? null);

  const heroSettled = useCallback(() => {
    if (heroTimed.current) return;
    heroTimed.current = true;
    recordMetric('detail_hero_ms', heroLabel.current, sinceNavigation());
  }, []);
  // The headline, or the problem's heading, takes focus so arrows, Escape and Backspace work from a TV remote.
  useEffect(() => { if (data || failed) headingRef.current?.focus({ preventScroll: true }); }, [data?.id, failed]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    // Hero preload: the clicked summary already names the backdrop rendition.
    const src = resolveArtworkUrl(renditionUrl(summaryFor(id)?.backdrop, backdropWidthFor(window.innerWidth)));
    if (src) prefetchImage(src, 1);
  }, [id]);
  // With no backdrop, the colour field is the finished hero.
  useEffect(() => { if (shown && !hasBackdrop) heroSettled(); }, [shown?.id, hasBackdrop, heroSettled]); // eslint-disable-line react-hooks/exhaustive-deps

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if ((event.key === 'Escape' || event.key === 'Backspace') && !(event.target as HTMLElement).closest(TEXT_ENTRY)) {
      event.preventDefault();
      back();
      return;
    }
    // From the headline, Down goes to the first control in the body and Up to Back, not to whichever is nearest the middle
    // of a wide headline.
    const fromHeading = event.target === headingRef.current;
    const first = !fromHeading ? null
      : event.key === 'ArrowDown' ? event.currentTarget.querySelector('.t-body')?.querySelector<HTMLElement>(FOCUS_TARGETS)
        : event.key === 'ArrowUp' ? event.currentTarget.querySelector<HTMLElement>('.t-back') : null;
    if (first) {
      event.preventDefault();
      first.focus();
      return;
    }
    moveFocus(event);
  }

  if (failed) {
    const missing = title.error instanceof ApiRequestError && title.error.status === 404;
    return (
      <div className="gallery t-page" onKeyDown={onKeyDown}>
        <div className="t-problem" role="alert">
          <h1 ref={headingRef} tabIndex={-1}>{missing ? 'This title is not in your library' : 'Lumina could not load this title'}</h1>
          <p>{missing ? 'It may have been removed, or it is not shared with you.' : 'Nothing in your library changed. Try again in a moment.'}</p>
          {missing ? null : <button className="g-button g-button-text" onClick={reload} type="button">Try again</button>}
          <button className="g-button g-button-text" onClick={back} type="button">Back to your library</button>
        </div>
      </div>
    );
  }
  if (!shown) {
    // A deep link before its data: a quiet paper field, no text.
    return <div aria-busy="true" className="gallery t-page" onKeyDown={onKeyDown}><div aria-hidden="true" className="t-hero is-empty" /></div>;
  }
  if (shown.type === 'album' || shown.type === 'artist') {
    const music = { backLabel: backLabel ?? lensLabel(shown), detail: data, headingRef, onBack: back, onKeyDown, onOpenTitle, onPlay, summary: shown };
    return shown.type === 'album' ? <AlbumPage {...music} onPatch={reloadFailed ? () => reload() : title.patch} /> : <ArtistPage {...music} />;
  }

  const plain = !hasBackdrop;
  const colour = cardColour(shown, 'backdrop');
  const logo = data && usesLogo(data) && !logoFailed ? data.logo : null;
  const meta = metaLine(data ?? shown);
  const headline = (
    <h1 className="t-headline" ref={headingRef} tabIndex={-1}>
      {logo ? <GalleryArt alt={shown.name} art={logo} card={null} className="t-logo" colour={NO_SLOT} kind="logo" onFail={() => setLogoFailed(true)} priority={1} sizes={LOGO_SIZES} /> : shown.name}
    </h1>
  );
  const trailer = data?.extras.find((extra) => extra.extra_type === 'trailer');

  return (
    <div className={`gallery t-page${plain ? ' is-plain' : ''}`} onKeyDown={onKeyDown} style={{ '--g-accent': pageAccent(shown, galleryTheme()) } as CSSProperties}>
      <div className="t-hero" style={{ backgroundColor: colour.colour, color: plain ? cardTextColour(colour.colour, colour.fromPalette) : undefined }}>
        {plain ? null : <GalleryArt alt="" art={shown.backdrop} card={null} className="t-hero-art" colour={colour} kind="backdrop" onSettled={heroSettled} priority={1} sizes="100vw" />}
        {plain ? null : <div aria-hidden="true" className="t-hero-fade" />}
        <button aria-label={`Back to ${backLabel ?? lensLabel(shown)}`} className="t-back g-label" data-focus-item onClick={back} type="button"><ChevronLeft aria-hidden="true" /> {backLabel ?? lensLabel(shown)}</button>
        {plain ? headline : null}
      </div>
      <div className="t-body">
        <div className="t-main">
          {plain ? null : headline}
          {meta.length ? <p className="t-meta g-label">{meta.map((part, index) => <Fragment key={index}>{index ? ' · ' : null}<span className="t-meta-part">{part}</span></Fragment>)}</p> : null}
          {shown.overview ? <p className={`t-lede${hasDropCap(shown.overview) ? ' has-dropcap' : ''}`}>{shown.overview}</p> : null}
          {data?.tagline ? <p className="t-tagline">{data.tagline}</p> : null}
          {data ? <TitleActions detail={data} onPatch={reloadFailed ? reload : title.patch} onPlay={onPlay} onEdit={() => onEdit(data.id)} onReload={reload} user={user} /> : null}
          {reloadFailed ? <p className="t-note" role="alert">Lumina could not refresh this title. <button className="g-text-button" onClick={reload} type="button">Try again</button></p> : null}
          {data && storyVisible(data) && data.play_next ? <StorySoFar episodeId={data.play_next.id} /> : null}
        </div>
        {data ? <SideColumn detail={data} onOpenTitle={onOpenTitle} /> : null}
      </div>
      {data ? (
        <>
          {data.type === 'series' ? <Seasons canEdit={Boolean(user.can_edit_details)} detail={data} onEdit={onEdit} onPlay={onPlay} onSeason={onSeason} revision={revision} season={season} /> : null}
          {data.type === 'movie' || data.type === 'series' ? <KeyScenes onPlay={onPlay} title={data} /> : null}
          {data.type === 'boxset'
            ? <PosterRow heading="In this collection" headingId="t-collection-heading" onOpenTitle={onOpenTitle} titles={data.children} />
            : <Cast onSearch={onSearch} people={castOf(data.people)} />}
          <Extras extras={data.extras.filter((extra) => extra !== trailer)} onPlay={onPlay} />
          <PosterRow heading="More like this" headingId="t-similar-heading" onOpenTitle={onOpenTitle} titles={similar.data ?? []} />
        </>
      ) : null}
    </div>
  );
}
