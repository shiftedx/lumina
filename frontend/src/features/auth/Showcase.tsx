import { Pause, Play } from 'lucide-react';
import { type KeyboardEvent, useEffect, useRef, useState } from 'react';
import { getShowcase, type ShowcaseSlide } from '../../api';
import { IconButton } from '../../ui';
import { cx } from '../../ui/cx';

const ART = '/api/public/showcase/art/';

function loadImage(url: string | undefined): Promise<void> {
  if (!url) return Promise.resolve();
  const image = new Image();
  image.src = url;
  return image.decode();
}

const reducedMotion = () => typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

export interface Showcase {
  slides: ShowcaseSlide[];
  index: number;
  previous: number | null;
  /** Hover or focus on the controls holds the current slide; ``stopped`` is the pause button (or reduced motion). */
  held: boolean;
  stopped: boolean;
  select: (index: number) => void;
  advance: () => void;
  hold: (held: boolean) => void;
  toggle: () => void;
}

/** Public release art for the sign-in page. Sign-in never waits: the list is asked for after first paint and the art
 *  appears only once its first image has decoded; no slides (Requests off, upstream down) keeps the calm page. */
export function useShowcase(enabled: boolean): Showcase | null {
  const [slides, setSlides] = useState<ShowcaseSlide[]>([]);
  const [index, setIndex] = useState(0);
  const [previous, setPrevious] = useState<number | null>(null);
  const [held, hold] = useState(false);
  const [stopped, setStopped] = useState(reducedMotion);
  const nextReady = useRef<Promise<boolean>>(Promise.resolve(true));

  useEffect(() => {
    if (!enabled) return; // the boot 'loading' stage of a signed-in member never asks
    let live = true;
    const timer = window.setTimeout(() => {
      getShowcase().then(async ({ slides: answer }) => {
        // Only Lumina's own showcase images ever reach an <img>.
        const usable = (Array.isArray(answer) ? answer : []).filter((slide) => slide?.backdrop_url?.startsWith(ART) && (!slide.logo_url || slide.logo_url.startsWith(ART)));
        if (!usable.length) return;
        await loadImage(usable[0].backdrop_url);
        if (live) setSlides(usable);
      }).catch(() => undefined);
    }, 0);
    return () => { live = false; window.clearTimeout(timer); };
  }, [enabled]);

  // Preload the next slide while this one holds, so the crossfade never reveals a half-loaded image.
  useEffect(() => {
    if (slides.length < 2) return;
    const next = slides[(index + 1) % slides.length];
    void loadImage(next.logo_url).catch(() => undefined);
    nextReady.current = loadImage(next.backdrop_url).then(() => true, () => false);
  }, [slides, index]);

  if (!slides.length) return null;
  const select = (to: number) => {
    if (to === index) return;
    setPrevious(index);
    setIndex(to);
  };
  return {
    slides, index, previous, held, stopped, select, hold,
    toggle: () => setStopped((value) => !value),
    advance: () => {
      const next = (index + 1) % slides.length;
      void nextReady.current.then((ok) => {
        if (ok) { select(next); return; }
        setSlides((all) => all.filter((_, i) => i !== next)); // a broken image drops out of the cycle
        setIndex((i) => (next < i ? i - 1 : i));
        setPrevious(null);
      });
    },
  };
}

/** The full-bleed backdrops: the outgoing slide stays beneath while the incoming one fades in over it. Decorative. */
export function ShowcaseArt({ show }: { show: Showcase }) {
  const layers = show.previous === null || show.previous === show.index ? [show.index] : [show.previous, show.index];
  return (
    <div aria-hidden="true" className={cx('g-show-art', (show.held || show.stopped) && 'is-held')}>
      {layers.map((i) => <img alt="" className={cx(show.previous !== null && i === show.index && 'is-in')} decoding="async" key={show.slides[i].backdrop_url} src={show.slides[i].backdrop_url} />)}
    </div>
  );
}

/** Caption, title (its logo when there is one) and progress pips; the running pip's fill paces the cycle. */
export function ShowcaseCaption({ show }: { show: Showcase }) {
  const pips = useRef<HTMLDivElement>(null);
  const slide = show.slides[show.index];
  const running = show.slides.length > 1;
  function onKey(event: KeyboardEvent) {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const to = (show.index + step + show.slides.length) % show.slides.length;
    show.select(to);
    pips.current?.querySelectorAll('button')[to]?.focus();
  }
  return (
    <div
      className="g-show-caption"
      onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) show.hold(false); }}
      onFocus={() => show.hold(true)}
      onPointerEnter={() => show.hold(true)}
      onPointerLeave={() => show.hold(false)}
    >
      <p className="g-show-now" key={slide.backdrop_url}>
        <span className="g-show-when">{slide.caption}</span>
        {slide.logo_url ? <img alt={slide.title} className="g-show-logo" src={slide.logo_url} /> : <span className="g-show-title">{slide.title}</span>}
      </p>
      {running ? (
        <div className="g-show-controls">
          <IconButton icon={show.stopped ? <Play size={16} /> : <Pause size={16} />} label={show.stopped ? 'Play the showcase' : 'Pause the showcase'} onClick={show.toggle} variant="overlay" />
          <div aria-label="Featured releases" className={cx('g-show-pips', (show.held || show.stopped) && 'is-held')} onKeyDown={onKey} ref={pips} role="group">
            {show.slides.map((item, i) => (
              <button aria-current={i === show.index || undefined} aria-label={item.title} className="g-show-pip" key={item.backdrop_url} onClick={() => show.select(i)} tabIndex={i === show.index ? 0 : -1} type="button">
                {i === show.index ? <i key={show.index} onAnimationEnd={show.advance} /> : null}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
