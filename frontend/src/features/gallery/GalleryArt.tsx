/**
 * One gallery image: colour, blurred preview, deferred sharp image, typographic card.
 *
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { resolveArtworkUrl } from '../../Artwork';
import { imageFailedLabel, imageLoadLabel, recordMetric } from '../../perfMetrics';
import type { TitleArt } from '../../types';
import { type ArtKind, artSrcSet, cardTextColour, GALLERY_RETRY_DELAYS_MS, prefersReducedMotion, renditionUrl, safeColour, safePreview } from './galleryModel';
import { type LoadPriority, useImageSlot } from './imageLoader';
import './gallery.css';

export type GalleryArtProps = {
  art: TitleArt | null | undefined;
  kind: ArtKind;
  /** Accessible name; '' when the surrounding control already names the title. */
  alt: string;
  sizes: string;
  priority: LoadPriority;
  position?: number;
  /** Slot background and card colour: galleryModel.cardColour(title, kind). */
  colour: { colour: string; fromPalette: boolean };
  /** Typographic card content; null renders nothing on failure (logos: the caller shows text via onFail). */
  card: { name: string; year?: number | null } | null;
  className?: string;
  /** The sharp image decoded, or the card shown in its place. */
  onSettled?: (outcome: 'image' | 'card') => void;
  onFail?: () => void;
};

/** A card left by exhausted retries is re-attempted once, on a mount at least this long after. */
export const REATTEMPT_AFTER_MS = 60_000;
const EXHAUSTED_MEMORY = 2_000;
const exhaustedAt = new Map<string, number>();

type Phase = { src: string | null; attempt: number; state: 'loading' | 'waiting' | 'shown' | 'card'; faded: boolean };

function startPhase(src: string | null): Phase {
  if (!src) return { src, attempt: 0, state: 'card', faded: false };
  const at = exhaustedAt.get(src);
  if (at === undefined) return { src, attempt: 0, state: 'loading', faded: false };
  // After the quiet period, one more try; failing it goes straight back to the card.
  return { src, attempt: GALLERY_RETRY_DELAYS_MS.length, state: Date.now() - at >= REATTEMPT_AFTER_MS ? 'loading' : 'card', faded: false };
}

// The default 250-entry Resource Timing buffer fills on one wall; a full buffer silently drops the entries 404s are read from.
globalThis.performance?.setResourceTimingBufferSize?.(1_000);

/** Status and cache outcome of a finished load (Resource Timing), as Artwork reads them; an <img> cannot see headers. */
function timing(url: string): (PerformanceResourceTiming & { responseStatus?: number }) | undefined {
  return (globalThis.performance?.getEntriesByName?.(url) as Array<PerformanceResourceTiming & { responseStatus?: number }> | undefined)?.at(-1);
}

function sources(art: TitleArt | null | undefined, kind: ArtKind): { src: string | null; srcSet: string | undefined } {
  const smallest = art?.widths.length ? Math.min(...art.widths) : null;
  const src = resolveArtworkUrl((smallest !== null ? renditionUrl(art, smallest) : null) ?? art?.url ?? null);
  const srcSet = artSrcSet(art, kind)
    ?.split(', ')
    .flatMap((entry) => {
      const [url, width] = entry.split(' ');
      const resolved = resolveArtworkUrl(url);
      return resolved ? [`${resolved} ${width}`] : [];
    })
    .join(', ');
  return { src, srcSet: srcSet || undefined };
}

export function GalleryArt({ art, kind, alt, sizes, priority, position = 0, colour, card, className, onSettled, onFail }: GalleryArtProps) {
  const { src, srcSet } = sources(art, kind);
  const [phase, setPhase] = useState<Phase>(() => startPhase(src));
  // A new URL starts clean during render (store-previous-prop pattern), so no effect can race the load.
  const current = phase.src === src ? phase : startPhase(src);
  if (phase !== current) setPhase(current);
  const image = useRef<HTMLImageElement | null>(null);
  const retry = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startedAt = useRef(0);
  const slot = useImageSlot(current.state === 'loading' && src ? `${src}#${current.attempt}` : null, priority, position);
  // Each attempt mounts a fresh <img> (keyed on the attempt); the clock starts as it attaches, before any load event.
  const attach = useCallback((node: HTMLImageElement | null) => {
    image.current = node;
    if (node) startedAt.current = performance.now();
  }, []);
  useEffect(() => () => { if (retry.current !== null) clearTimeout(retry.current); }, [src]);
  // A phase that begins as the card (no artwork, or retries exhausted under a minute ago) still reports it once.
  useEffect(() => {
    if (current.state !== 'card') return;
    onFail?.();
    onSettled?.('card');
  }, [src]); // once per URL; a card reached later reports from toCard

  const update = (next: (value: Phase) => Phase) => setPhase((value) => (value.src === src ? next(value) : value));
  const toCard = (outcome: '404' | 'exhausted') => {
    recordMetric('image_failed', imageFailedLabel(kind, outcome), 1);
    update((value) => ({ ...value, state: 'card' }));
    onFail?.();
    onSettled?.('card');
  };
  const onError = () => {
    slot.settle();
    if (!src) return;
    const status = timing(image.current?.currentSrc || src)?.responseStatus;
    if (status === 404 || status === 410) return toCard('404');
    if (current.attempt >= GALLERY_RETRY_DELAYS_MS.length) {
      exhaustedAt.delete(src);
      exhaustedAt.set(src, Date.now());
      if (exhaustedAt.size > EXHAUSTED_MEMORY) exhaustedAt.delete(exhaustedAt.keys().next().value as string);
      return toCard('exhausted');
    }
    recordMetric('image_failed', imageFailedLabel(kind, 'transient'), 1);
    update((value) => ({ ...value, state: 'waiting' }));
    retry.current = setTimeout(() => update((value) => ({ ...value, attempt: value.attempt + 1, state: 'loading' })), GALLERY_RETRY_DELAYS_MS[current.attempt]);
  };
  const onLoad = () => {
    const element = image.current;
    const reveal = () => {
      slot.settle();
      if (src) {
        exhaustedAt.delete(src);
        const cached = timing(element?.currentSrc || src)?.transferSize === 0;
        recordMetric('image_load_ms', imageLoadLabel(kind, cached ? 'hit' : 'net'), performance.now() - startedAt.current);
      }
      // With no crossfade (reduced motion, or no transition support) the preview can go at once.
      const instant = !element || prefersReducedMotion() || !(parseFloat(getComputedStyle(element).transitionDuration) > 0);
      update((value) => ({ ...value, state: 'shown', faded: instant }));
      onSettled?.('image');
    };
    if (element && typeof element.decode === 'function') element.decode().then(reveal, reveal);
    else reveal();
  };

  const background = safeColour(art?.dominant) ?? colour.colour;
  const preview = safePreview(art?.preview);
  const showImage = src !== null && ((current.state === 'loading' && slot.granted) || current.state === 'shown');
  return (
    <span className={`g-art g-art-${kind}${className ? ` ${className}` : ''}`} style={{ backgroundColor: background }}>
      {preview && current.state !== 'card' && !current.faded ? <img alt="" aria-hidden="true" className="g-art-preview" src={preview} /> : null}
      {current.state === 'card'
        ? card
          ? (
            <span aria-label={alt || undefined} className="g-card" role={alt ? 'img' : undefined} style={{ backgroundColor: colour.colour, color: cardTextColour(colour.colour, colour.fromPalette) }}>
              <span className="g-card-name">{card.name}</span>
              {card.year ? <span className="g-card-year">{card.year}</span> : null}
            </span>
          )
          : null
        : showImage
          ? <img alt={alt} className={`g-art-image${current.state === 'shown' ? ' is-shown' : ''}`} decoding="async" fetchPriority={slot.fetchPriority} key={current.attempt} onError={onError} onLoad={onLoad} onTransitionEnd={() => update((value) => ({ ...value, faded: true }))} ref={attach} sizes={sizes} src={src ?? undefined} srcSet={srcSet} />
          : null}
    </span>
  );
}
