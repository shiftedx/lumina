/**
 * A wall-style poster button: art, state marker and caption. The title page
 * uses it for More like this and In this collection.
 */
import { Check } from 'lucide-react';
import { type ButtonHTMLAttributes, type MouseEvent, useEffect, useRef } from 'react';

import { resolveArtworkUrl } from '../../Artwork';
import type { TitleSummary } from '../../types';
import { ArtHost, ArtMenu } from './ArtMenu';
import { GalleryArt } from './GalleryArt';
import { backdropWidthFor, cardColour, type PosterMarker, posterLabel, posterMarker, renditionUrl } from './galleryModel';
import { type LoadPriority, prefetchImage } from './imageLoader';
import { prefetchTitle, rememberSummary } from './titleCache';

export type PosterCardProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'onClick' | 'title' | 'type'> & {
  title: TitleSummary;
  onOpen: (title: TitleSummary, event?: MouseEvent<HTMLButtonElement>) => void;
  priority: LoadPriority;
  /** Queue order inside the priority class: row * 1000 + column. */
  position?: number;
  /** `sizes` for the poster srcset, matching the rendered column width. */
  sizes: string;
  /** Title and year under the poster (desktop and tablet); false on phone. */
  caption?: boolean;
  /** Select mode (bulk edit): the poster shows a checkbox and reports its state with aria-pressed. */
  selectable?: boolean;
  selected?: boolean;
};

export const INTENT_DELAY_MS = 150;

/** State marker over a poster, tile or still; decoration only, the control's name says it in words. */
export function ArtMarker({ marker, small = false }: { marker: PosterMarker; small?: boolean }) {
  if (marker.kind === 'unwatched') return <span aria-hidden="true" className={`g-marker-triangle${small ? ' is-small' : ''}`} />;
  if (marker.kind === 'count') return <span aria-hidden="true" className="g-marker-count">{marker.count > 99 ? '99+' : marker.count}</span>;
  if (marker.kind === 'progress') return <span aria-hidden="true" className="g-marker-progress"><span style={{ width: `${marker.percent}%` }} /></span>;
  return null;
}

/** After 150 ms of hover or keyboard focus on a desktop pointer, warm the title's backdrop and detail. */
export function useIntentPrefetch(title: TitleSummary) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancel = () => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
  };
  useEffect(() => cancel, []);
  const start = () => {
    if (!window.matchMedia?.('(hover: hover) and (pointer: fine)').matches) return;
    cancel();
    timer.current = setTimeout(() => {
      const backdrop = resolveArtworkUrl(renditionUrl(title.backdrop, backdropWidthFor(window.innerWidth)));
      if (backdrop) prefetchImage(backdrop, 3);
      prefetchTitle(title.id);
    }, INTENT_DELAY_MS);
  };
  return { start, cancel };
}

export function PosterCard({ title, onOpen, priority, position = 0, sizes, caption = true, selectable, selected, className, onFocus, onBlur, onPointerEnter, onPointerLeave, ...button }: PosterCardProps) {
  const intent = useIntentPrefetch(title);
  return (
    <ArtHost>
    <button
      {...button}
      aria-label={posterLabel(title)}
      aria-pressed={selectable ? Boolean(selected) : undefined}
      className={`g-poster${className ? ` ${className}` : ''}`}
      onBlur={(event) => { intent.cancel(); onBlur?.(event); }}
      onClick={(event) => { rememberSummary(title); if (selectable) onOpen(title, event); else onOpen(title); }}
      onFocus={(event) => { intent.start(); onFocus?.(event); }}
      onPointerEnter={(event) => { intent.start(); onPointerEnter?.(event); }}
      onPointerLeave={(event) => { intent.cancel(); onPointerLeave?.(event); }}
      type="button"
    >
      <span className="g-poster-frame">
        <GalleryArt alt="" art={title.poster} card={{ name: title.name, year: title.year }} colour={cardColour(title, 'poster')} kind="poster" position={position} priority={priority} sizes={sizes} />
        <ArtMarker marker={posterMarker(title)} />
        {selectable ? <span aria-hidden="true" className={`g-select-box${selected ? ' is-on' : ''}`}>{selected ? <Check /> : null}</span> : null}
      </span>
      {caption ? <span aria-hidden="true" className="g-caption"><span className="g-caption-title">{title.name}</span>{title.year ? <span className="g-caption-year">{title.year}</span> : null}</span> : null}
    </button>
    {selectable ? null : <ArtMenu subject={{ kind: 'title', title }} />}
    </ArtHost>
  );
}
