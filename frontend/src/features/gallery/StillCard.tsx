/**
 * A Library item card for YouTube, Recordings and saved audio: the thumbnail as a still
 * (a 1:1 crop for audio), a progress bar once started, an editorial caption, and a More menu with Add to queue and
 * Delete. No unwatched
 * triangle: most saved videos are unwatched, so it would be noise.
 */
import type { ButtonHTMLAttributes, ReactNode } from 'react';

import { libraryThumbnail } from '../../luminaModel';
import type { LibraryItem, TitleArt } from '../../types';
import { formatDuration } from '../../utils';
import { ArtHost, ArtMenu } from './ArtMenu';
import { GalleryArt } from './GalleryArt';
import { fallbackColour } from './galleryModel';
import type { LoadPriority } from './imageLoader';
import { ArtMarker } from './PosterCard';

export type StillKind = 'video' | 'recording' | 'audio';

export type StillCardProps = {
  item: LibraryItem;
  kind: StillKind;
  /** 'still' = 16:9, 'square' = 1:1 with the thumbnail cropped to cover (saved audio). */
  shape: 'still' | 'square';
  onPlay: (item: LibraryItem) => void;
  priority: LoadPriority;
  position?: number;
  sizes: string;
  caption?: boolean;
  /** When set, the More menu offers "Add to queue" (1.5.1 parity). */
  onQueue?: (item: LibraryItem) => void;
  /** When set, the More menu offers "Delete from Lumina"; the caller has already checked canDeleteLibraryFile. */
  onDelete?: (item: LibraryItem) => void;
};

const lengthOf = (item: LibraryItem): number | null => item.progress?.duration_seconds ?? item.duration ?? null;

/** Percent watched for the progress bar; null when not started, finished, or with no length to measure against. */
export function stillProgress(item: LibraryItem): number | null {
  const progress = item.progress;
  const length = lengthOf(item);
  if (!progress || progress.completed || progress.position_seconds <= 0 || !length) return null;
  return Math.min(100, Math.max(4, (progress.position_seconds / length) * 100));
}

/** The card's accessible name: "{title}, {channel}, {duration}{, watched|, {x} minutes left}". */
export function stillLabel(item: LibraryItem): string {
  const parts = [item.title, item.uploader || null, item.duration ? formatDuration(item.duration) : null];
  const progress = item.progress;
  const length = lengthOf(item);
  if (progress?.completed) parts.push('watched');
  else if (progress && progress.position_seconds > 0 && length && length > progress.position_seconds) {
    const minutes = Math.ceil((length - progress.position_seconds) / 60);
    parts.push(`${minutes} minute${minutes === 1 ? '' : 's'} left`);
  }
  return parts.filter(Boolean).join(', ');
}

/** The caption's label line: channel and length for videos, channel, date and length for recordings. */
export function stillLine(item: LibraryItem, kind: StillKind): string {
  if (kind === 'audio') return item.uploader || '';
  const length = item.duration ? formatDuration(item.duration) : null;
  if (kind === 'recording') {
    const at = item.downloaded_at ?? item.created_at;
    return [item.uploader, at ? new Date(at).toLocaleDateString(undefined, { dateStyle: 'medium' }) : null, length].filter(Boolean).join(' · ');
  }
  return [item.uploader, length, item.progress?.completed ? 'Watched' : null].filter(Boolean).join(' · ');
}

export type StillFrameProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'onClick' | 'title' | 'type'> & {
  /** A rendition-backed TitleArt, or { url, widths: [] } for a plain thumbnail. */
  art: TitleArt | null;
  /** Slot colour (galleryModel.cardColour, or fallbackColour with fromPalette true). */
  colour: { colour: string; fromPalette: boolean };
  shape: 'still' | 'square';
  /** Caption line 1, and the typographic card's name when the art is missing or fails. */
  name: string;
  /** Caption line 2 (label). */
  line?: string | null;
  /** A label above line 1: the Live shelf's provider. */
  kicker?: string | null;
  /** The button's accessible name. */
  label: string;
  /** Percent watched for the gold bar; null or absent = none. */
  percent?: number | null;
  /** Over the art's top-left corner: the LiveBadge. */
  badge?: ReactNode;
  /** The art menu (ArtMenu) for this card, rendered beside the button. */
  menu?: ReactNode;
  onActivate: () => void;
  priority: LoadPriority;
  position?: number;
  sizes: string;
  caption?: boolean;
};

/**
 * The still card's body: art, badge, progress bar and an editorial caption in one button. StillCard
 * renders it for Library items; Home renders it for titles, remote entries and live streams.
 */
export function StillFrame({ art, colour, shape, name, line, kicker, label, percent = null, badge, menu, onActivate, priority, position = 0, sizes, caption = true, className, ...button }: StillFrameProps) {
  return (
    <ArtHost hasBadge={Boolean(badge)}>
    <button {...button} aria-label={label} className={`g-still${className ? ` ${className}` : ''}`} onClick={onActivate} type="button">
      <span className="g-still-frame">
        <GalleryArt alt="" art={art} card={{ name }} colour={colour} kind={shape === 'square' ? 'square' : 'still'} position={position} priority={priority} sizes={sizes} />
        {badge}
        {percent === null ? null : <ArtMarker marker={{ kind: 'progress', percent }} />}
      </span>
      {caption ? (
        <span aria-hidden="true" className="g-still-caption">
          {kicker ? <span className="g-label">{kicker}</span> : null}
          <span className="g-still-title">{name}</span>
          {line ? <span className="g-label">{line}</span> : null}
        </span>
      ) : null}
    </button>
    {menu}
    </ArtHost>
  );
}

export function StillCard({ item, kind, shape, onPlay, priority, position = 0, sizes, caption = true, onQueue, onDelete }: StillCardProps) {
  const thumbnail = libraryThumbnail(item);
  const progress = stillProgress(item);
  const line = stillLine(item, kind);
  return (
    <div className={`g-still-card is-${shape}`}>
      <StillFrame
        art={thumbnail ? { url: thumbnail, widths: [] } : null}
        caption={caption}
        colour={{ colour: fallbackColour(item.id), fromPalette: true }}
        data-focus-key={item.id}
        label={stillLabel(item)}
        line={line}
        menu={<ArtMenu extra={[...(onQueue ? [{ kind: 'item' as const, label: 'Add to queue', onSelect: () => onQueue(item) }] : []), ...(onDelete ? [{ kind: 'item' as const, label: 'Delete from Lumina', danger: true, onSelect: () => onDelete(item) }] : [])]} subject={{ kind: 'library', item }} />}
        name={item.title}
        onActivate={() => onPlay(item)}
        percent={progress}
        position={position}
        priority={priority}
        shape={shape}
        sizes={sizes}
      />
    </div>
  );
}
