import { ArrowUpRight, Play, RefreshCw, Shuffle } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { getTitle, listTitles } from '../../api';
import type { TitleDetail, TitleSummary } from '../../types';
import { Button, TextButton } from '../../ui/Button';
import { albumLabel } from '../gallery/AlbumCard';
import { GalleryArt } from '../gallery/GalleryArt';
import { cardColour, countNoun } from '../gallery/galleryModel';
import { playMusic } from './musicSession';

const FEATURED_LIMIT = 6;

const activeYears = (detail: TitleDetail | null) => {
  const since = detail?.music?.active_since;
  const until = detail?.music?.active_until;
  if (since && until) return `${since}–${until}`;
  if (since) return `Active since ${since}`;
  if (until) return `Active until ${until}`;
  return null;
};

const failureMessage = (error: unknown, fallback: string) => error instanceof Error && error.message ? error.message : fallback;

export function AudioSoundstage({ owner, onOpenArtist }: { owner: string; onOpenArtist: (artist: TitleSummary) => void }) {
  const [artists, setArtists] = useState<TitleSummary[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TitleDetail | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [detailProblem, setDetailProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState<'play' | 'shuffle' | null>(null);
  const [reload, setReload] = useState(0);
  const listRequest = useRef(0);
  const detailRequest = useRef(0);

  useEffect(() => {
    const current = ++listRequest.current;
    detailRequest.current += 1;
    setArtists(null); setSelectedId(null); setDetail(null); setProblem(null); setDetailProblem(null);
    void listTitles({ type: 'artist', limit: FEATURED_LIMIT, sort: 'created' }).then((page) => {
      if (listRequest.current !== current) return;
      const found = page.items.filter((title) => title.type === 'artist').slice(0, FEATURED_LIMIT);
      setArtists(found); setSelectedId(found[0]?.id ?? null);
    }, (error) => {
      if (listRequest.current === current) { setArtists([]); setProblem(failureMessage(error, 'Featured artists could not be loaded.')); }
    });
    return () => { listRequest.current += 1; detailRequest.current += 1; };
  }, [owner, reload]);

  const selected = artists?.find((artist) => artist.id === selectedId) ?? null;
  useEffect(() => {
    const current = ++detailRequest.current;
    setDetail(null); setDetailProblem(null);
    if (!selected) return;
    void getTitle(selected.id).then((found) => {
      if (detailRequest.current === current && owner) setDetail(found);
    }, (error) => {
      if (detailRequest.current === current) setDetailProblem(failureMessage(error, 'Artist details could not be loaded.'));
    });
  }, [owner, selected?.id]);

  const artist = detail ?? selected;
  const albums = detail?.children.filter((child) => child.type === 'album').slice(0, 5) ?? [];
  const metadata = [detail?.music?.origin, activeYears(detail)].filter(Boolean);

  const listen = async (shuffle: boolean) => {
    if (!artist) return;
    setBusy(shuffle ? 'shuffle' : 'play'); setDetailProblem(null);
    try { await playMusic(owner, { title_id: artist.id }, undefined, 0, shuffle); }
    catch (error) { setDetailProblem(failureMessage(error, 'This artist could not be played.')); }
    finally { setBusy(null); }
  };

  if (artists === null) return <section aria-label="Featured artist" aria-live="polite" className="gallery audio-soundstage is-loading"><div className="audio-soundstage-loading" role="status">Preparing the soundstage…</div></section>;
  if (!artists.length) return <section aria-labelledby="audio-soundstage-empty" className="gallery audio-soundstage is-empty"><div><p className="g-kicker">Featured artist</p><h2 id="audio-soundstage-empty">Your soundstage is waiting</h2><p>{problem ?? 'Artists will appear here when albums join your library.'}</p>{problem ? <TextButton icon={<RefreshCw />} onClick={() => setReload((value) => value + 1)}>Try again</TextButton> : null}</div></section>;
  if (!artist) return null;

  return <section aria-labelledby="audio-soundstage-title" className="gallery audio-soundstage">
    <div className="audio-soundstage-portrait">
      <GalleryArt alt={`Portrait of ${artist.name}`} art={artist.poster} card={{ name: artist.name }} className="audio-soundstage-art" colour={cardColour(artist, 'square')} kind="square" priority={1} sizes="(max-width: 599px) 100vw, (max-width: 1099px) 42vw, 520px" />
    </div>
    <div className="audio-soundstage-story">
      <p className="g-kicker">On the soundstage</p>
      <h2 id="audio-soundstage-title">{artist.name}</h2>
      {metadata.length ? <p className="audio-soundstage-meta">{metadata.join(' · ')}</p> : null}
      {artist.overview ? <p className="audio-soundstage-bio">{artist.overview}</p> : detail === null && !detailProblem ? <p aria-live="polite" className="audio-soundstage-note" role="status">Loading artist story…</p> : null}
      {artist.genres.length ? <p className="audio-soundstage-genres">{artist.genres.slice(0, 4).join(' · ')}</p> : null}
      <div className="audio-soundstage-actions">
        <Button busy={busy === 'play'} disabled={artist.child_count === 0} icon={<Play />} onClick={() => void listen(false)} variant="primary">Play artist</Button>
        <Button busy={busy === 'shuffle'} disabled={artist.child_count === 0} icon={<Shuffle />} onClick={() => void listen(true)}>Shuffle</Button>
        <TextButton iconEnd={<ArrowUpRight />} onClick={() => onOpenArtist(artist)}>Open artist</TextButton>
      </div>
      {detailProblem ? <p aria-live="polite" className="audio-soundstage-note is-error" role="status">{detailProblem}</p> : null}
    </div>
    {albums.length ? <section aria-labelledby="audio-soundstage-releases" className="audio-soundstage-releases">
      <div><h3 id="audio-soundstage-releases">From the library</h3><span>{countNoun(albums.length, ['release', 'releases'])}</span></div>
      <ul>{albums.map((album, index) => <li className={index === 0 ? 'is-lead' : ''} key={album.id}><button aria-label={albumLabel(album)} onClick={() => onOpenArtist(album)} type="button"><GalleryArt alt="" art={album.poster} card={{ name: album.name, year: album.year }} colour={cardColour(album, 'square')} kind="square" position={index} priority={index < 2 ? 1 : 2} sizes={index === 0 ? '(max-width: 599px) 56vw, 280px' : '(max-width: 599px) 28vw, 150px'} /><span><strong>{album.name}</strong>{album.year ? <small>{album.year}</small> : null}</span></button></li>)}</ul>
    </section> : null}
    {artists.length > 1 ? <nav aria-label="Choose a featured artist" className="audio-soundstage-select"><span>More artists</span>{artists.map((choice, index) => <button aria-label={`Feature ${choice.name}`} aria-pressed={choice.id === artist.id} key={choice.id} onClick={() => setSelectedId(choice.id)} type="button"><GalleryArt alt="" art={choice.poster} card={{ name: choice.name }} colour={cardColour(choice, 'square')} kind="square" position={index} priority={3} sizes="56px" /><span>{choice.name}</span></button>)}</nav> : null}
  </section>;
}

export { FEATURED_LIMIT };
