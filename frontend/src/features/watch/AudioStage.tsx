/**
 * The audio stage: while a track or saved audio plays, the player frame shows the cover on
 * the cover's colour with the track and artist below, and the operating system's media controls get the metadata and
 * the album queue. The <audio> element keeps playing underneath, so sessions, checkpoints and controls are unchanged.
 *
 */
import { type CSSProperties, useEffect, useRef } from 'react';

import { resolveArtworkUrl } from '../../Artwork';
import { libraryThumbnail } from '../../luminaModel';
import type { LibraryItem, TitleArt, TitleDetail } from '../../types';
import { GalleryArt } from '../gallery/GalleryArt';
import { fallbackColour, renditionUrl, safeColour } from '../gallery/galleryModel';
import '../gallery/music.css';

export type QueueStep = { itemId: string; label: string };

export type AudioStageProps = {
  item: LibraryItem;
  /** The track's album with its tracks; null for saved audio, or until it loads. */
  album: TitleDetail | null;
  previous: QueueStep | null;
  next: QueueStep | null;
  onPlayItem: (itemId: string) => void;
  onSeek: (seconds: number) => void;
};

/** What the stage and the Media Session show: the track, its artist, the byline and the art. */
export function stageText(item: LibraryItem, album: TitleDetail | null): { title: string; artist: string; byline: string; art: TitleArt | null } {
  if (!album) {
    const thumbnail = libraryThumbnail(item);
    return { title: item.title, artist: item.uploader ?? '', byline: item.uploader ?? '', art: thumbnail ? { url: thumbnail, widths: [] } : null };
  }
  const track = album.tracks?.find((entry) => entry.item_id === item.id);
  const artist = track?.artist ?? album.artist_name ?? '';
  return { title: track?.name ?? item.title, artist, byline: [artist, album.name].filter(Boolean).join(' · '), art: album.poster ?? null };
}

const ACTIONS = ['previoustrack', 'nexttrack', 'seekto'] as const;
/** The stage's shade over the cover colour; inline, so music.css stays free of colour literals. */
const STAGE_SHADE = 'linear-gradient(transparent, color-mix(in srgb, var(--g-overlay-halo) 45%, transparent))';

/** Sets or clears one action; a browser that does not support an action throws, and that action is simply skipped. */
export function setAction(session: MediaSession, action: (typeof ACTIONS)[number] | 'play' | 'pause', handler: MediaSessionActionHandler | null) {
  try {
    session.setActionHandler(action, handler);
  } catch {
    // unsupported here (older Safari, Firefox without seekto)
  }
}

export function AudioStage({ item, album, previous, next, onPlayItem, onSeek }: AudioStageProps) {
  const { title, artist, byline, art } = stageText(item, album);
  const own = safeColour(art?.dominant);
  const colour = own ?? fallbackColour(item.id);
  // The 480w rendition for albums (960w reaches the stage through srcset); saved audio's own thumbnail.
  const artwork = resolveArtworkUrl(renditionUrl(art, 480) ?? art?.url ?? null);
  const latest = useRef({ onPlayItem, onSeek });
  latest.current = { onPlayItem, onSeek };

  useEffect(() => {
    if (!('mediaSession' in navigator) || typeof MediaMetadata === 'undefined') return undefined;
    const session = navigator.mediaSession;
    session.metadata = new MediaMetadata({ title, artist, album: album?.name ?? '', artwork: artwork ? [{ src: artwork, sizes: '480x480' }] : [] });
    setAction(session, 'previoustrack', previous ? () => latest.current.onPlayItem(previous.itemId) : null);
    setAction(session, 'nexttrack', next ? () => latest.current.onPlayItem(next.itemId) : null);
    setAction(session, 'seekto', (details) => { if (details.seekTime !== undefined) latest.current.onSeek(details.seekTime); });
    return () => {
      session.metadata = null;
      for (const action of ACTIONS) setAction(session, action, null);
    };
  }, [title, artist, album?.name, artwork, previous?.itemId, next?.itemId]); // eslint-disable-line react-hooks/exhaustive-deps -- previous/next are read by id

  return (
    <div className="gallery g-audio-stage" style={{ backgroundColor: colour, backgroundImage: STAGE_SHADE } as CSSProperties}>
      <GalleryArt alt="" art={art} card={{ name: title }} className="g-audio-art" colour={{ colour, fromPalette: !own }} kind="square" priority={1} sizes="min(60vh, 480px)" />
      <p className="g-audio-title">{title}</p>
      {byline ? <p className="g-audio-byline">{byline}</p> : null}
      {next ? <p className="g-audio-next">Next · {next.label}</p> : null}
    </div>
  );
}
