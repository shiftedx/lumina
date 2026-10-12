/**
 * A three-column backdrop tile: name and facts on a scrim. With no backdrop, or one that fails, the poster
 * crops to cover the tile; with neither, a bare colour field. The caption names the title, so the art never repeats it.
 * The walls' featured rows and the All landing's spotlight share it.
 */
import { Check } from 'lucide-react';
import { type CSSProperties, type MouseEvent, useState } from 'react';

import type { TitleSummary } from '../../types';
import { formatRuntime } from '../titles/titleModel';
import { GalleryArt } from './GalleryArt';
import { cardColour, posterLabel, posterMarker } from './galleryModel';
import type { LoadPriority } from './imageLoader';
import { ArtMarker, useIntentPrefetch } from './PosterCard';
import { rememberSummary } from './titleCache';

export type FeatureTileProps = {
  title: TitleSummary; index: number; priority: LoadPriority; position: number; sizes: string; style?: CSSProperties; tabIndex: number;
  onFocus?: () => void; onOpen: (title: TitleSummary, event?: MouseEvent<HTMLButtonElement>) => void;
  /** Select mode (bulk edit), as on PosterCard. */
  selectable?: boolean; selected?: boolean;
  /** Replaces the facts line: the All spotlight's "Movie · 2024 · Sci-Fi". */
  kicker?: string;
  /** Added to g-feature: the spotlight's is-lead and is-side. */
  className?: string;
};

export function FeatureTile({ title, index, priority, position, sizes, style, tabIndex, onFocus, onOpen, kicker, className, selectable, selected }: FeatureTileProps) {
  const intent = useIntentPrefetch(title);
  const [backdropFailed, setBackdropFailed] = useState(false);
  const kind = title.backdrop && !backdropFailed ? 'backdrop' : 'poster';
  const facts = kicker ?? [title.year ? String(title.year) : null, title.genres[0] ?? null, formatRuntime(title.runtime_seconds)].filter(Boolean).join(' · ');
  return (
    <button
      aria-label={posterLabel(title)}
      aria-pressed={selectable ? Boolean(selected) : undefined}
      className={`g-feature${className ? ` ${className}` : ''}`}
      data-focus-key={title.id}
      data-index={index}
      onBlur={intent.cancel}
      onClick={(event) => { rememberSummary(title); if (selectable) onOpen(title, event); else onOpen(title); }}
      onFocus={() => { intent.start(); onFocus?.(); }}
      onPointerEnter={intent.start}
      onPointerLeave={intent.cancel}
      style={style}
      tabIndex={tabIndex}
      type="button"
    >
      <GalleryArt
        alt=""
        art={kind === 'backdrop' ? title.backdrop : title.poster}
        card={null}
        colour={cardColour(title, kind)}
        key={kind}
        kind={kind}
        onSettled={(outcome) => { if (outcome === 'card' && kind === 'backdrop') setBackdropFailed(true); }}
        position={position}
        priority={priority}
        sizes={sizes}
      />
      <span aria-hidden="true" className="g-feature-copy"><span className="g-feature-name">{title.name}</span>{facts ? <span className="g-label">{facts}</span> : null}</span>
      <ArtMarker marker={posterMarker(title)} />
      {selectable ? <span aria-hidden="true" className={`g-select-box${selected ? ' is-on' : ''}`}>{selected ? <Check /> : null}</span> : null}
    </button>
  );
}
