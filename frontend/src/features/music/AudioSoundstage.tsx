import { RefreshCw } from '../../luminaIcons';
import { useEffect, useRef, useState } from 'react';

import { getTitle, listTitles } from '../../api';
import type { TitleSummary } from '../../types';
import { TextButton } from '../../ui/Button';
import { albumLabel } from '../gallery/AlbumCard';
import { GalleryArt } from '../gallery/GalleryArt';
import { cardColour, countNoun } from '../gallery/galleryModel';

const ALBUM_SOURCE_LIMIT = 18;
const SHOWCASE_LIMIT = 6;

const failureMessage = (error: unknown, fallback: string) => error instanceof Error && error.message ? error.message : fallback;

const artistKey = (album: TitleSummary) => album.artist_name?.trim().toLowerCase() || album.parent_id || album.id;

function showcaseAlbums(items: TitleSummary[]): TitleSummary[] {
  const albums = items.filter((title) => title.type === 'album');
  const chosen: TitleSummary[] = [];
  const artists = new Set<string>();

  for (const album of albums) {
    if (chosen.length >= SHOWCASE_LIMIT) break;
    const key = artistKey(album);
    if (artists.has(key)) continue;
    artists.add(key);
    chosen.push(album);
  }

  for (const album of albums) {
    if (chosen.length >= SHOWCASE_LIMIT) break;
    if (!chosen.some((chosenAlbum) => chosenAlbum.id === album.id)) chosen.push(album);
  }

  return chosen;
}

async function supplementAlbums(initialAlbums: TitleSummary[], isCurrent: () => boolean): Promise<TitleSummary[] | null> {
  if (!initialAlbums.length || new Set(initialAlbums.map(artistKey)).size >= SHOWCASE_LIMIT) return initialAlbums;
  if (!isCurrent()) return null;

  const representedIds = new Set(initialAlbums.map((album) => album.parent_id).filter((id): id is string => Boolean(id)));
  const representedNames = new Set(initialAlbums.map((album) => album.artist_name?.trim().toLowerCase()).filter((name): name is string => Boolean(name)));
  let artistPage;
  try {
    artistPage = await listTitles({ type: 'artist', limit: SHOWCASE_LIMIT, sort: 'created' });
  } catch {
    return initialAlbums;
  }
  if (!isCurrent()) return null;

  const artists = artistPage.items
    .filter((artist) => artist.type === 'artist' && !representedIds.has(artist.id) && !representedNames.has(artist.name.trim().toLowerCase()))
    .slice(0, SHOWCASE_LIMIT);
  const details = await Promise.allSettled(artists.map((artist) => getTitle(artist.id)));
  if (!isCurrent()) return null;
  const supplements = details.flatMap((result) => {
    if (result.status !== 'fulfilled') return [];
    const albums = result.value.children.filter((child) => child.type === 'album');
    const cover = albums.find((album) => album.poster) ?? albums[0];
    return cover ? [cover] : [];
  });

  return [...initialAlbums, ...supplements];
}

export function AudioSoundstage({ owner, onOpenArtist }: { owner: string; onOpenArtist: (artist: TitleSummary) => void }) {
  const [featuredAlbums, setFeaturedAlbums] = useState<TitleSummary[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const listRequest = useRef(0);

  useEffect(() => {
    const current = ++listRequest.current;
    const isCurrent = () => listRequest.current === current;
    setFeaturedAlbums(null);
    setProblem(null);
    void listTitles({ type: 'album', limit: ALBUM_SOURCE_LIMIT, sort: 'created' }).then(async (page) => {
      if (!isCurrent()) return;
      const initialAlbums = page.items.filter((title) => title.type === 'album');
      setFeaturedAlbums(showcaseAlbums(initialAlbums));
      const availableAlbums = await supplementAlbums(initialAlbums, isCurrent);
      if (!availableAlbums || !isCurrent()) return;
      setFeaturedAlbums(showcaseAlbums(availableAlbums));
    }, (error) => {
      if (!isCurrent()) return;
      setFeaturedAlbums([]);
      setProblem(failureMessage(error, 'Albums could not be loaded.'));
    });
    return () => { listRequest.current += 1; };
  }, [owner, reload]);

  if (featuredAlbums === null) return <section aria-label="Music collection" aria-live="polite" className="gallery audio-soundstage is-loading"><div className="audio-soundstage-loading" role="status">Loading albums…</div></section>;
  if (!featuredAlbums.length) return <section aria-labelledby="audio-soundstage-empty" className="gallery audio-soundstage is-empty"><div><p className="g-kicker">Your collection</p><h2 id="audio-soundstage-empty">No albums yet</h2><p aria-live="polite" role={problem ? 'status' : undefined}>{problem ?? 'Albums will appear here when music joins your library.'}</p>{problem ? <TextButton icon={<RefreshCw />} onClick={() => setReload((value) => value + 1)}>Try again</TextButton> : null}</div></section>;

  const featuredArtistCount = new Set(featuredAlbums.map(artistKey)).size;
  return <section aria-labelledby="audio-soundstage-title" className="gallery audio-soundstage">
    <div className="audio-soundstage-story">
      <p className="g-kicker">Your collection</p>
      <h2 id="audio-soundstage-title">A room full of records</h2>
      <p className="audio-soundstage-intro">Pick a record from across your library and settle into a listen.</p>
      <p className="audio-soundstage-meta">On this shelf: {countNoun(featuredAlbums.length, ['album', 'albums'])} · {countNoun(featuredArtistCount, ['artist', 'artists'])}</p>
    </div>
    <section aria-labelledby="audio-soundstage-releases" className="audio-soundstage-releases">
      <div><h3 id="audio-soundstage-releases">From your library</h3><span>Across your artists</span></div>
      <ul>{featuredAlbums.map((album, index) => <li key={album.id}><button aria-label={albumLabel(album)} onClick={() => onOpenArtist(album)} type="button"><GalleryArt alt="" art={album.poster} card={{ name: album.name, year: album.year }} colour={cardColour(album, 'square')} kind="square" position={index} priority={index < 3 ? 1 : 2} sizes="(max-width: 599px) 42vw, (max-width: 1199px) 22vw, 180px" /><span><strong>{album.name}</strong>{album.artist_name || album.year ? <small>{[album.artist_name, album.year].filter(Boolean).join(' · ')}</small> : null}</span></button></li>)}</ul>
    </section>
  </section>;
}

export { ALBUM_SOURCE_LIMIT, SHOWCASE_LIMIT };
