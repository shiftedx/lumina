/**
 * The album page: a sticky 1:1 cover beside the kicker, title, artist line, genres, Play,
 * Shuffle and Favorite, and the tracklist by disc. GalleryTitlePage renders it for an album and keeps the fetch, the
 * summary cache, focus and Back.
 */
import { type CSSProperties, type KeyboardEvent, type MouseEvent, type Ref, useState } from 'react';
import { Check, ChevronLeft, Heart, Play, Shuffle } from 'lucide-react';

import { setFavorite } from '../../api';
import type { AlbumTrack, TitleDetail, TitleSummary } from '../../types';
import { errorMessage, formatDuration } from '../../utils';
import { setAlbumQueue, shuffled, trackLabel } from './albumQueue';
import { GalleryArt } from './GalleryArt';
import { cardColour, countNoun, type GalleryTheme } from './galleryModel';
import { pageAccent } from './titlePageModel';
import { useMediaQuery } from './WallGrid';
import './music.css';

const COVER_SIZES = '(max-width: 599px) min(100vw, 420px), 440px';
const galleryTheme = (): GalleryTheme => (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark');

export type AlbumPageProps = {
  /** Paints at once: the clicked card's summary, or the cached detail. */
  summary: TitleSummary;
  /** The album's detail with its tracks; null until it arrives. */
  detail: TitleDetail | null;
  headingRef: Ref<HTMLHeadingElement>;
  backLabel: string;
  onBack: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void;
  onOpenTitle: (title: TitleSummary) => void;
  onPlay: (itemId: string) => void;
  onPatch: (update: (current: TitleDetail) => TitleDetail) => void;
};

/** "48 min", "1 h 12 min": an album's length. */
export function albumLength(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)} h ${minutes % 60} min` : `${minutes} min`;
}

/** The track to resume: the one last played that is started and not finished. */
export function resumeTrack(tracks: readonly AlbumTrack[]): AlbumTrack | null {
  return tracks
    .filter((track) => track.user_data.position_seconds > 0 && !track.user_data.played)
    .reduce<AlbumTrack | null>((best, track) => (!best || (track.user_data.last_watched_at ?? '') > (best.user_data.last_watched_at ?? '') ? track : best), null);
}

/** Seconds left on a started track, or null when it is not started, is finished, or has no known length. */
function secondsLeft(track: AlbumTrack): number | null {
  const { played, position_seconds: position } = track.user_data;
  return played || position <= 0 || !track.duration_seconds ? null : Math.max(0, track.duration_seconds - position);
}

/** "Play 4. Song, by Guest, 3:01, played" or "…, 12 minutes left". */
export function trackButtonLabel(track: AlbumTrack): string {
  const left = secondsLeft(track);
  const minutes = left === null ? 0 : Math.round(left / 60);
  const state = track.user_data.played ? 'played'
    : left === null ? null
      : left < 60 ? 'less than a minute left' : `${minutes} minute${minutes === 1 ? '' : 's'} left`;
  return [`Play ${trackLabel(track)}`, track.artist ? `by ${track.artist}` : null, track.duration_seconds ? formatDuration(track.duration_seconds) : null, state].filter(Boolean).join(', ');
}

/** The album's discs in served order; a track with no disc is its own group. */
function byDisc(tracks: readonly AlbumTrack[]): Array<[number | null, AlbumTrack[]]> {
  const discs = new Map<number | null, AlbumTrack[]>();
  for (const track of tracks) discs.set(track.disc ?? null, [...(discs.get(track.disc ?? null) ?? []), track]);
  return [...discs];
}

/** The artist link's target: enough of a summary for the artist page to paint its name at once. */
function artistOf(album: TitleSummary): TitleSummary {
  return { id: album.parent_id as string, type: 'artist', name: album.artist_name as string, genres: [], added_at: album.added_at, user_data: { played: false, is_favorite: false, position_seconds: 0 } };
}

function TrackRow({ track, onPlay }: { track: AlbumTrack; onPlay: (track: AlbumTrack) => void }) {
  const left = secondsLeft(track);
  const percent = left === null ? 0 : Math.max(4, Math.min(100, (track.user_data.position_seconds / (track.duration_seconds as number)) * 100));
  const time = left !== null ? (left < 60 ? '<1 min left' : `${Math.round(left / 60)} min left`) : track.duration_seconds ? formatDuration(track.duration_seconds) : '';
  return (
    <button aria-label={trackButtonLabel(track)} className="g-track" data-focus-item onClick={() => onPlay(track)} type="button">
      <span aria-hidden="true" className="g-track-number"><span className="g-track-index">{track.number ?? '–'}</span><Play className="g-track-glyph" /></span>
      <span aria-hidden="true" className="g-track-name">
        <span className="g-track-title">{track.name}</span>
        {track.artist ? <span className="g-label">{track.artist}</span> : null}
      </span>
      <span aria-hidden="true" className="g-track-duration">{track.user_data.played ? <Check /> : null}{time}</span>
      {left !== null ? <span aria-hidden="true" className="g-track-progress"><span style={{ width: `${percent}%` }} /></span> : null}
    </button>
  );
}

/** The rows are one focus column: Up and Down between rows, Left back to the actions. */
function Tracklist({ tracks, onPlay }: { tracks: AlbumTrack[]; onPlay: (track: AlbumTrack) => void }) {
  const discs = byDisc(tracks);
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    const rows = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('.g-track')];
    const index = rows.indexOf(document.activeElement as HTMLButtonElement);
    if (index < 0) return;
    event.preventDefault();
    event.stopPropagation(); // the page's own arrow handling would move again
    const play = event.currentTarget.closest('.g-album-body')?.querySelector<HTMLElement>('.g-album-play');
    const target = event.key === 'ArrowDown' ? rows[index + 1] : event.key === 'ArrowUp' ? rows[index - 1] ?? play : event.key === 'ArrowLeft' ? play : null;
    target?.focus();
  };
  return (
    <div className="g-tracklist" onKeyDown={onKeyDown}>
      {discs.map(([disc, rows]) => (
        <div key={disc ?? 'none'}>
          {discs.length > 1 ? <h2 className="g-label g-disc">Disc {disc ?? '–'}</h2> : null}
          <ol>{rows.map((track) => <li key={track.item_id}><TrackRow onPlay={onPlay} track={track} /></li>)}</ol>
        </div>
      ))}
    </div>
  );
}

export function AlbumPage({ summary, detail, headingRef, backLabel, onBack, onKeyDown, onOpenTitle, onPlay, onPatch }: AlbumPageProps) {
  const phone = useMediaQuery('(max-width: 599px)');
  const [notice, setNotice] = useState<string | null>(null);
  const album = detail ?? summary;
  const tracks = detail?.tracks ?? null;
  const resume = tracks ? resumeTrack(tracks) : null;
  const length = tracks?.reduce((sum, track) => sum + (track.duration_seconds ?? 0), 0) ?? 0;
  const count = tracks?.length ?? album.child_count ?? null;
  const kicker = ['Album', album.year ? String(album.year) : null, count === null ? null : countNoun(count, ['track', 'tracks']), length ? albumLength(length) : null].filter(Boolean).join(' · ');
  const favorite = album.user_data.is_favorite;
  const playable = Boolean(tracks?.length);
  const artist = album.artist_name ?? null;
  const linked = Boolean(artist && album.parent_id && artist.toLowerCase() !== 'various artists');

  /** Plays a track with the album order queued, so a stored shuffle of this album no longer applies. */
  const play = (track: AlbumTrack, order: readonly AlbumTrack[] = tracks ?? []) => {
    setAlbumQueue(album.id, order);
    onPlay(track.item_id);
  };
  const shuffle = () => {
    if (!tracks?.length) return;
    const order = shuffled(tracks);
    play(order[0], order);
  };
  const toggleFavorite = async () => {
    try {
      await setFavorite(album.id, !favorite);
      onPatch((current) => ({ ...current, user_data: { ...current.user_data, is_favorite: !favorite } }));
      setNotice(null);
    } catch (error) {
      setNotice(errorMessage(error, 'Lumina could not update your favorites. Try again.'));
    }
  };
  const openArtist = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; // new tab or window: the browser's
    event.preventDefault();
    onOpenTitle(artistOf(album));
  };

  return (
    <div className={`gallery g-album-page${phone ? ' is-phone' : ''}`} onKeyDown={onKeyDown} style={{ '--g-accent': pageAccent(album, galleryTheme()) } as CSSProperties}>
      <button aria-label={`Back to ${backLabel}`} className="g-back g-label" data-focus-item onClick={onBack} type="button"><ChevronLeft aria-hidden="true" /> {backLabel}</button>
      <div className="g-album-layout">
        <div className="g-album-cover">
          <GalleryArt alt="" art={album.poster} card={{ name: album.name, year: album.year }} colour={cardColour(album, 'square')} kind="square" priority={1} sizes={COVER_SIZES} />
        </div>
        <div className="g-album-body">
          <p className="g-label">{kicker}</p>
          <h1 className="g-album-title" ref={headingRef} tabIndex={-1}>{album.name}</h1>
          {artist ? <p className="g-album-artist">by {linked ? <a data-focus-item href={`/title/${encodeURIComponent(album.parent_id as string)}`} onClick={openArtist}>{artist}</a> : artist}</p> : null}
          {album.genres.length ? <p className="g-label">{album.genres.slice(0, 3).join(' · ')}</p> : null}
          <div className="g-album-actions" data-focus-row>
            <button className="g-button is-primary g-button-text g-album-play" data-focus-item disabled={!playable} onClick={() => { if (tracks?.length) play(resume ?? tracks[0]); }} type="button">
              <Play aria-hidden="true" /> {resume ? `Resume · ${trackLabel(resume)}` : 'Play'}
            </button>
            {phone
              ? <button aria-label="Shuffle" className="g-icon-button" data-focus-item disabled={!playable} onClick={shuffle} type="button"><Shuffle aria-hidden="true" /></button>
              : <button className="g-button g-button-text" data-focus-item disabled={!playable} onClick={shuffle} type="button"><Shuffle aria-hidden="true" /> Shuffle</button>}
            {phone
              ? <button aria-label="Favorite" aria-pressed={favorite} className="g-icon-button" data-focus-item disabled={!detail} onClick={() => void toggleFavorite()} type="button"><Heart aria-hidden="true" fill={favorite ? 'currentColor' : 'none'} /></button>
              : <button aria-pressed={favorite} className="g-button g-button-text" data-focus-item disabled={!detail} onClick={() => void toggleFavorite()} type="button"><Heart aria-hidden="true" fill={favorite ? 'currentColor' : 'none'} /> Favorite</button>}
          </div>
          {notice ? <p className="g-album-note" role="alert">{notice}</p> : null}
          {tracks === null ? null : tracks.length ? <Tracklist onPlay={play} tracks={tracks} /> : <p className="g-album-note">These tracks are not available right now.</p>}
        </div>
      </div>
    </div>
  );
}
