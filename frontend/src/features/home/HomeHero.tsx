/**
 * Home's hero carousel: the member's Continue titles, then their recommendations, one full-bleed
 * slide at a time with Resume or Play and Details. Slides move horizontally (an instant swap under reduced motion) and
 * advance every 8 s while nothing holds them: hover, focus inside, or a hidden tab pause the pips' timer. The pips are a
 * tablist (arrow keys change slide), the step buttons are pointer affordances, and touch swipes.
 *
 * Each slide paints at once from the summary (a wide backdrop, or a colour field), then from the anchor's detail through
 * the title page's 30 s cache; portrait-only art is composed as a poster over a blurred copy of itself. Resume and Play
 * go through onPlay (the app's openRoute, which claims an early conversion); after Home's first screen and at least a
 * second, the first slide's playback decision is warmed once; 400 ms of hover or focus on the primary action may start
 * the conversion early.
 */
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { type CSSProperties, type KeyboardEvent, useCallback, useEffect, useRef, useState } from 'react';

import { resolveArtworkUrl } from '../../Artwork';
import { recordMetric, sinceNavigation } from '../../perfMetrics';
import { cancelSpeculativeStart, prefetchPlaybackOptions, speculativeStart } from '../../playbackPrefetch';
import type { TitleDetail, TitleSummary } from '../../types';
import { GalleryArt } from '../gallery/GalleryArt';
import { backdropWidthFor, cardColour, cardTextColour, prefersReducedMotion, renditionUrl } from '../gallery/galleryModel';
import { prefetchImage } from '../gallery/imageLoader';
import { SPECULATE_AFTER_MS } from '../gallery/TitleActions';
import { loadTitle } from '../gallery/titleCache';
import { usesLogo } from '../gallery/titlePageModel';
import { primaryAction } from '../titles/titleModel';
import { entryPercent, heroAnchor, heroArt, heroKey, heroMeta, type HeroPick, titleCaption } from './homeModel';

/** The warm-up waits at least this long after mount, and for the first screen. */
export const WARM_AFTER_MS = 1_000;
const LOGO_SIZES = '(max-width: 599px) 80vw, 600px';
const POSTER_SIZES = '(max-width: 599px) 40vw, 320px';
const NO_SLOT = { colour: 'transparent', fromPalette: false };
const SWIPE_PX = 48;
const KICKER: Record<HeroPick['kind'], string> = { continue: 'Continue watching', recommended: 'Recommended for you', newest: 'New in your library' };
const saveData = (): boolean => Boolean((navigator as Navigator & { connection?: { saveData?: boolean } }).connection?.saveData);

export type HomeHeroProps = {
  slides: HeroPick[];
  /** Home's first screen has painted: the warm-up may start. */
  ready: boolean;
  /** Resume and Play: the app's openRoute({ surface: 'watch', libraryId }). */
  onPlay: (itemId: string) => void;
  onOpenTitle: (title: TitleSummary) => void;
};

type Shown = { key: string; from: string | null; dir: 1 | -1 };

export function HomeHero({ slides, ready, onPlay, onOpenTitle }: HomeHeroProps) {
  const [shown, setShown] = useState<Shown>(() => ({ key: slides[0] ? heroKey(slides[0]) : '', from: null, dir: 1 }));
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [hidden, setHidden] = useState(() => document.visibilityState === 'hidden');
  const [announce, setAnnounce] = useState(false);
  const timed = useRef(false);
  const swipe = useRef<number | null>(null);
  const count = slides.length;
  // A slide that left (its Continue entry removed) hands over to the first.
  const index = Math.max(0, slides.findIndex((pick) => heroKey(pick) === shown.key));
  const current = slides[index];

  useEffect(() => {
    const sync = () => setHidden(document.visibilityState === 'hidden');
    document.addEventListener('visibilitychange', sync);
    return () => document.removeEventListener('visibilitychange', sync);
  }, []);
  // The next slide's detail is fetched ahead, so it arrives with its art.
  const nextAnchor = count > 1 ? heroAnchor(slides[(index + 1) % count].title) : null;
  useEffect(() => { if (nextAnchor) loadTitle(nextAnchor).catch(() => undefined); }, [nextAnchor]);

  const settled = useCallback(() => {
    if (timed.current) return;
    timed.current = true;
    recordMetric('home_hero_ms', 'home', sinceNavigation());
  }, []);

  if (!current) return null;
  const go = (to: number, dir: 1 | -1, byMember = true) => {
    const target = slides[(to + count) % count];
    if (!target || target === current) return;
    setShown({ key: heroKey(target), from: prefersReducedMotion() ? null : heroKey(current), dir });
    if (byMember) setAnnounce(true);
  };
  const pipKeys = (event: KeyboardEvent<HTMLDivElement>) => {
    const dir = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    if (!dir) return;
    event.preventDefault();
    go(index + dir, dir);
    (event.currentTarget.children[(index + dir + count) % count] as HTMLElement | undefined)?.focus();
  };
  const leaving = shown.from === null ? undefined : slides.find((pick) => heroKey(pick) === shown.from);
  const paused = hovered || focused || hidden;

  return (
    <section
      aria-label="Featured"
      aria-roledescription="carousel"
      className={`h-hero${paused ? ' is-paused' : ''}`}
      onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocused(false); }}
      onFocus={() => setFocused(true)}
      onPointerCancel={() => { swipe.current = null; }}
      onPointerDown={(event) => { swipe.current = event.pointerType === 'mouse' ? null : event.clientX; }}
      onPointerEnter={(event) => { if (event.pointerType === 'mouse') setHovered(true); }}
      onPointerLeave={() => setHovered(false)}
      onPointerUp={(event) => {
        const start = swipe.current;
        swipe.current = null;
        if (start === null || Math.abs(event.clientX - start) < SWIPE_PX) return;
        const dir = event.clientX < start ? 1 : -1;
        go(index + dir, dir);
      }}
    >
      <div aria-live={announce ? 'polite' : 'off'} className="h-hero-stage" style={{ '--h-dir': shown.dir } as CSSProperties}>
        {leaving && leaving !== current ? (
          <HeroSlide key={heroKey(leaving)} motion="is-out" onDone={() => setShown((value) => ({ ...value, from: null }))} onOpenTitle={onOpenTitle} onPlay={onPlay} pick={leaving} position={slides.indexOf(leaving)} total={count} warm={false} />
        ) : null}
        <HeroSlide
          key={heroKey(current)}
          motion={leaving && leaving !== current ? 'is-in' : ''}
          onOpenTitle={onOpenTitle}
          onPlay={onPlay}
          onSettled={index === 0 ? settled : undefined}
          pick={current}
          position={index}
          total={count}
          warm={ready && index === 0}
        />
      </div>
      {count > 1 ? (
        <div className="h-hero-controls">
          <button aria-label="Previous" className="g-icon-button h-hero-step" onClick={() => go(index - 1, -1)} tabIndex={-1} type="button"><ChevronLeft aria-hidden="true" /></button>
          <div aria-label="Featured titles" className="h-pips" onKeyDown={pipKeys} role="tablist">
            {slides.map((pick, position) => (
              <button
                aria-controls={position === index ? `h-slide-${position}` : undefined}
                aria-label={titleCaption(pick.title).name}
                aria-selected={position === index}
                className="h-pip"
                data-focus-item={position === index ? '' : undefined}
                key={heroKey(pick)}
                onClick={() => go(position, position > index ? 1 : -1)}
                role="tab"
                tabIndex={position === index ? 0 : -1}
                type="button"
              >
                {/* The fill runs for one slide's time and its end advances the carousel; paused while held, absent under reduced motion. */}
                {position === index ? <span className="h-pip-fill" key={heroKey(pick)} onAnimationEnd={() => go(index + 1, 1, false)} /> : null}
              </button>
            ))}
          </div>
          <button aria-label="Next" className="g-icon-button h-hero-step" onClick={() => go(index + 1, 1)} tabIndex={-1} type="button"><ChevronRight aria-hidden="true" /></button>
        </div>
      ) : null}
    </section>
  );
}

type HeroSlideProps = {
  pick: HeroPick;
  position: number;
  total: number;
  /** '' at rest, 'is-in' arriving, 'is-out' leaving (inert; onDone when its exit ends). */
  motion: '' | 'is-in' | 'is-out';
  /** Warm this slide's playback decision once (the first slide, after the first screen). */
  warm: boolean;
  onPlay: (itemId: string) => void;
  onOpenTitle: (title: TitleSummary) => void;
  onSettled?: () => void;
  onDone?: () => void;
};

function HeroSlide({ pick, position, total, motion, warm, onPlay, onOpenTitle, onSettled, onDone }: HeroSlideProps) {
  const { title } = pick;
  const anchor = heroAnchor(title);
  const [detail, setDetail] = useState<TitleDetail | null>(null);
  const [failed, setFailed] = useState(false);
  const [logoFailed, setLogoFailed] = useState(false);
  const mountedAt = useRef(Date.now());
  const warmed = useRef(false);
  const speculation = useRef<{ itemId: string; timer: number } | null>(null);

  useEffect(() => {
    let current = true;
    loadTitle(anchor).then((next) => { if (current) setDetail(next); }, () => { if (current) setFailed(true); });
    const backdrop = resolveArtworkUrl(renditionUrl(title.backdrop, backdropWidthFor(window.innerWidth)));
    if (backdrop) prefetchImage(backdrop, 1);
    return () => { current = false; };
  }, [anchor]); // eslint-disable-line react-hooks/exhaustive-deps -- a slide is keyed by its pick

  const art = heroArt(title, detail);
  const shown = detail ?? title;
  const colour = cardColour(shown, art.kind === 'poster' ? 'poster' : 'backdrop');
  const continuing = pick.kind === 'continue';
  const action = !continuing && detail ? primaryAction(detail) : null;
  const itemId = continuing ? pick.entry.item_id : action && !action.reason ? action.itemId : null;
  const primaryLabel = continuing ? 'Resume' : action?.label ?? null;
  const unavailable = action?.reason ?? null;
  const name = detail?.name ?? titleCaption(title).name;
  const logo = detail && usesLogo(detail) && !logoFailed ? detail.logo : null;
  const overview = (continuing && title.type === 'episode' ? title.overview : shown.overview) ?? null;
  const percent = continuing ? entryPercent(pick.entry) : null;
  const meta = heroMeta(pick);
  const headingId = `h-hero-title-${position}`;

  // With no art to wait for, the colour field is the finished hero.
  useEffect(() => { if ((detail || failed) && art.kind === 'field') onSettled?.(); }, [detail, failed, art.kind, onSettled]);

  useEffect(() => {
    if (!warm || !itemId || warmed.current || saveData()) return undefined;
    const timer = window.setTimeout(() => {
      warmed.current = true;
      prefetchPlaybackOptions(itemId);
    }, Math.max(0, WARM_AFTER_MS - (Date.now() - mountedAt.current)));
    return () => window.clearTimeout(timer);
  }, [warm, itemId]);

  const endSpeculation = useCallback(() => {
    const current = speculation.current;
    if (!current) return;
    window.clearTimeout(current.timer);
    cancelSpeculativeStart(current.itemId);
    speculation.current = null;
  }, []);
  const beginSpeculation = (id: string) => {
    endSpeculation();
    speculation.current = { itemId: id, timer: window.setTimeout(() => speculativeStart(id), SPECULATE_AFTER_MS) };
  };
  useEffect(() => endSpeculation, [endSpeculation]);
  const settle = onSettled ? () => onSettled() : undefined;
  const out = motion === 'is-out';

  return (
    <div
      aria-hidden={out || undefined}
      aria-label={`${position + 1} of ${total}`}
      aria-roledescription="slide"
      className={`h-slide is-${art.kind} ${motion}`}
      id={out ? undefined : `h-slide-${position}`}
      inert={out || undefined}
      onAnimationEnd={(event) => { if (event.target === event.currentTarget) onDone?.(); }}
      role={total > 1 ? 'tabpanel' : 'group'}
      style={{ '--h-hero-colour': colour.colour, color: art.kind === 'field' ? cardTextColour(colour.colour, colour.fromPalette) : undefined } as CSSProperties}
    >
      {art.kind === 'backdrop' ? <GalleryArt alt="" art={art.art} card={null} className="h-hero-art" colour={colour} kind="backdrop" onSettled={settle} priority={1} sizes="100vw" /> : null}
      {/* Portrait art: the poster beside the text, over a blurred, darkened copy of itself (the same rendition, one fetch). */}
      {art.kind === 'poster' ? <GalleryArt alt="" art={art.art} card={null} className="h-hero-art h-hero-blur" colour={colour} kind="poster" priority={1} sizes={POSTER_SIZES} /> : null}
      {art.kind === 'poster' ? <GalleryArt alt="" art={art.art} card={null} className="h-hero-poster" colour={colour} kind="poster" onSettled={settle} priority={1} sizes={POSTER_SIZES} /> : null}
      {art.kind === 'field' ? null : <div aria-hidden="true" className="h-hero-scrim" />}
      <div className="h-hero-text">
        <p className="g-label h-hero-kicker">{KICKER[pick.kind]}</p>
        <h2 className="h-hero-title" id={headingId}>
          {logo ? <GalleryArt alt={name} art={logo} card={null} className="h-hero-logo" colour={NO_SLOT} kind="logo" onFail={() => setLogoFailed(true)} priority={1} sizes={LOGO_SIZES} /> : name}
        </h2>
        {meta ? <p className="g-label h-hero-meta">{meta}</p> : null}
        {pick.kind === 'recommended' && pick.reason ? <p className="h-hero-why">{pick.reason}</p> : null}
        {percent === null ? null : <span aria-hidden="true" className="h-hero-progress"><span style={{ width: `${percent}%` }} /></span>}
        {overview ? <p className="h-hero-overview">{overview}</p> : null}
        <div className="h-hero-actions" data-focus-row>
          {unavailable ? <p className="h-hero-why">{unavailable}</p> : null}
          {!unavailable && primaryLabel && itemId ? (
            <button
              className="g-button is-primary"
              data-focus-item
              onBlur={endSpeculation}
              onClick={() => onPlay(itemId)}
              onFocus={() => beginSpeculation(itemId)}
              onPointerEnter={() => beginSpeculation(itemId)}
              onPointerLeave={endSpeculation}
              type="button"
            >
              {primaryLabel}
            </button>
          ) : null}
          <button aria-describedby={headingId} className="g-button" data-focus-item onClick={() => onOpenTitle(detail ?? title)} type="button">Details</button>
        </div>
      </div>
    </div>
  );
}
