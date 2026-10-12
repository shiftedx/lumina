/**
 * A 1:1 album or artist card: the cover, and a caption of title and "Artist · 2019" (album)
 * or name and "4 albums" (artist). No state marker: albums are replayed, so "unplayed" is noise.
 * WallGrid's square walls, the artist page and the Music chapter render it. The props mirror PosterCardProps.
 */
import type { ButtonHTMLAttributes } from 'react';

import type { TitleSummary } from '../../types';
import { ArtHost, ArtMenu } from './ArtMenu';
import { GalleryArt } from './GalleryArt';
import { cardColour, countNoun } from './galleryModel';
import type { LoadPriority } from './imageLoader';
import { useIntentPrefetch } from './PosterCard';
import { rememberSummary } from './titleCache';
import './music.css';

export type AlbumCardProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'onClick' | 'title' | 'type'> & {
  /** An album or an artist. */
  title: TitleSummary;
  onOpen: (title: TitleSummary) => void;
  priority: LoadPriority;
  /** Queue order inside the priority class: row * 1000 + column. */
  position?: number;
  sizes: string;
  /** Album: title and "Artist · 2019"; artist: name and "4 albums". False on phone. */
  caption?: boolean;
  /** False on the artist's own page, where an album's byline is its year alone. */
  showArtist?: boolean;
};

/** The label line under a card: "Artist · 2019", "2019", or "4 albums". */
export function albumByline(title: TitleSummary, showArtist = true): string {
  if (title.type === 'artist') return title.child_count ? countNoun(title.child_count, ['album', 'albums']) : '';
  return [showArtist ? title.artist_name : null, title.year ? String(title.year) : null].filter(Boolean).join(' · ');
}

/** The card's accessible name: "Album One by Artist A, 2019, 12 tracks"; an artist: "Artist A, 4 albums". */
export function albumLabel(title: TitleSummary): string {
  if (title.type === 'artist') return [title.name, title.child_count ? countNoun(title.child_count, ['album', 'albums']) : null].filter(Boolean).join(', ');
  const named = title.artist_name ? `${title.name} by ${title.artist_name}` : title.name;
  return [named, title.year ? String(title.year) : null, title.child_count ? countNoun(title.child_count, ['track', 'tracks']) : null].filter(Boolean).join(', ');
}

export function AlbumCard({ title, onOpen, priority, position = 0, sizes, caption = true, showArtist = true, className, onFocus, onBlur, onPointerEnter, onPointerLeave, ...button }: AlbumCardProps) {
  const intent = useIntentPrefetch(title);
  const byline = albumByline(title, showArtist);
  return (
    <ArtHost>
    <button
      {...button}
      aria-label={albumLabel(title)}
      className={`g-poster g-album${className ? ` ${className}` : ''}`}
      onBlur={(event) => { intent.cancel(); onBlur?.(event); }}
      onClick={() => { rememberSummary(title); onOpen(title); }}
      onFocus={(event) => { intent.start(); onFocus?.(event); }}
      onPointerEnter={(event) => { intent.start(); onPointerEnter?.(event); }}
      onPointerLeave={(event) => { intent.cancel(); onPointerLeave?.(event); }}
      type="button"
    >
      <GalleryArt alt="" art={title.poster} card={{ name: title.name, year: title.type === 'album' ? title.year : null }} colour={cardColour(title, 'square')} kind="square" position={position} priority={priority} sizes={sizes} />
      {caption ? <span aria-hidden="true" className="g-caption"><span className="g-caption-title">{title.name}</span>{byline ? <span className="g-caption-year">{byline}</span> : null}</span> : null}
    </button>
    <ArtMenu subject={{ kind: 'title', title }} />
    </ArtHost>
  );
}
