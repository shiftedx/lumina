/**
 * The artist page: the artist image and kicker, Play all and Shuffle all over every album
 * in page order, and a grid of the artist's albums, newest first as served. GalleryTitlePage renders it for an
 * artist.
 */
import { type KeyboardEvent, type Ref, useState } from 'react';
import { ChevronLeft, Play, Shuffle } from 'lucide-react';

import type { TitleDetail, TitleSummary } from '../../types';
import { AlbumCard } from './AlbumCard';
import { setAlbumQueue, shuffled } from './albumQueue';
import { GalleryArt } from './GalleryArt';
import { cardColour, countNoun } from './galleryModel';
import { loadTitle } from './titleCache';
import { useMediaQuery } from './WallGrid';
import './music.css';

export type ArtistPageProps = {
  summary: TitleSummary;
  /** The artist's detail; `children` are its visible albums, year DESC. Null until it arrives. */
  detail: TitleDetail | null;
  headingRef: Ref<HTMLHeadingElement>;
  backLabel: string;
  onBack: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void;
  onOpenTitle: (title: TitleSummary) => void;
  onPlay: (itemId: string) => void;
};

export function ArtistPage({ summary, detail, headingRef, backLabel, onBack, onKeyDown, onOpenTitle, onPlay }: ArtistPageProps) {
  const phone = useMediaQuery('(max-width: 599px)');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const artist = detail ?? summary;
  const albums = detail?.children ?? null;
  const albumCount = albums?.length ?? artist.child_count ?? null;
  const trackCount = albums?.reduce((sum, album) => sum + (album.child_count ?? 0), 0) ?? 0;
  const kicker = ['Artist', albumCount === null ? null : countNoun(albumCount, ['album', 'albums']), trackCount ? countNoun(trackCount, ['track', 'tracks']) : null].filter(Boolean).join(' · ');

  /** Every album's tracks, albums in page order; each album's detail comes through the 30 s title cache. */
  async function playAll(shuffle: boolean) {
    if (!albums?.length) return;
    setBusy(true);
    setNotice(null);
    try {
      const tracks = (await Promise.all(albums.map((album) => loadTitle(album.id)))).flatMap((album) => album.tracks ?? []);
      if (!tracks.length) {
        setNotice('Nothing by this artist is available right now.');
        return;
      }
      const order = shuffle ? shuffled(tracks) : tracks;
      setAlbumQueue(artist.id, order);
      onPlay(order[0].item_id);
    } catch {
      setNotice('Lumina could not load these tracks. Try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`gallery g-artist-page${phone ? ' is-phone' : ''}`} onKeyDown={onKeyDown}>
      <button aria-label={`Back to ${backLabel}`} className="g-back g-label" data-focus-item onClick={onBack} type="button"><ChevronLeft aria-hidden="true" /> {backLabel}</button>
      <header className="g-artist-head">
        <div className="g-artist-image">
          <GalleryArt alt="" art={artist.poster} card={{ name: artist.name }} colour={cardColour(artist, 'square')} kind="square" priority={1} sizes={phone ? '160px' : '240px'} />
        </div>
        <div className="g-artist-text">
          <p className="g-label">{kicker}</p>
          <h1 className="g-album-title" ref={headingRef} tabIndex={-1}>{artist.name}</h1>
          <div className="g-album-actions" data-focus-row>
            <button className="g-button is-primary g-button-text" data-focus-item disabled={busy || !albums?.length} onClick={() => void playAll(false)} type="button"><Play aria-hidden="true" /> Play all</button>
            <button className="g-button g-button-text" data-focus-item disabled={busy || !albums?.length} onClick={() => void playAll(true)} type="button"><Shuffle aria-hidden="true" /> Shuffle all</button>
          </div>
          {notice ? <p className="g-album-note" role="alert">{notice}</p> : null}
        </div>
      </header>
      {albums === null ? null : albums.length ? (
        <section aria-labelledby="g-artist-albums" className="g-artist-albums">
          <h2 className="g-chapter" id="g-artist-albums">Albums <span className="g-label">{albums.length}</span></h2>
          <ul className="g-album-grid" data-focus-row>
            {albums.map((album, index) => <li key={album.id}><AlbumCard data-focus-item onOpen={onOpenTitle} position={index} priority={2} showArtist={false} sizes="(max-width: 599px) 33vw, 200px" title={album} /></li>)}
          </ul>
        </section>
      ) : <p className="g-album-note">Nothing by this artist is available right now.</p>}
    </div>
  );
}
