import { ChevronDown, ChevronUp, ListMusic, Play, Plus, Radio, RefreshCw, Save, Trash2, X } from '../../luminaIcons';
import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react';

import * as api from '../../api';
import { Button, TextButton } from '../../ui/Button';
import { musicSession, playMusicQueue, setMusicOwner, useMusicSession } from './musicSession';
import type { MusicPlaylist, MusicPlaylistRules, MusicPlaylistSave, MusicTrack } from './musicTypes';
import './musicPlaylists.css';

type MixKind = 'rotation' | 'favorites' | 'unplayed' | 'radio';

const MIXES: ReadonlyArray<{ kind: MixKind; name: string; description: string }> = [
  { kind: 'rotation', name: 'In rotation', description: 'The music you have returned to lately.' },
  { kind: 'favorites', name: 'Favorites mix', description: 'A fresh shuffle of music you love.' },
  { kind: 'unplayed', name: 'Unplayed mix', description: 'Saved music you have not heard yet.' },
  { kind: 'radio', name: 'Track radio', description: 'A mix shaped by the track playing now.' },
];

const messageFor = (error: unknown, action: 'save' | 'delete' | 'load' | 'play' = 'load') => {
  if (error instanceof api.ApiRequestError && error.status === 409) return 'This playlist changed on another screen. Reopen it before saving your changes.';
  if (error instanceof api.ApiRequestError && error.status === 403) return action === 'delete'
    ? 'This shared playlist is read-only. Only its owner can delete it.'
    : 'This shared playlist is read-only. Only its owner can change it.';
  if (error instanceof Error && error.message) return error.message;
  if (action === 'play') return 'This music could not be played.';
  if (action === 'save') return 'The playlist could not be saved.';
  if (action === 'delete') return 'The playlist could not be deleted.';
  return 'Playlists could not be loaded.';
};

const trackLine = (track: MusicTrack) => [track.artist, track.album].filter(Boolean).join(' · ');

function PlaylistRows({ tracks, editable, onMove, onRemove }: {
  tracks: MusicTrack[];
  editable: boolean;
  onMove: (from: number, to: number) => void;
  onRemove: (index: number) => void;
}) {
  if (!tracks.length) return <p className="music-playlists-empty">No tracks are in this playlist.</p>;
  return <ol className="music-playlist-tracks">{tracks.map((track, index) => (
    <li key={`${track.id}:${index}`}>
      <span aria-hidden="true" className="music-playlist-track-number">{index + 1}</span>
      <span className="music-playlist-track-copy"><strong>{track.title}</strong><small>{trackLine(track) || 'Unknown artist'}</small></span>
      {editable ? <span className="music-playlist-track-actions">
        <button aria-label={`Move ${track.title} up`} disabled={index === 0} onClick={() => onMove(index, index - 1)} type="button"><ChevronUp aria-hidden="true" /></button>
        <button aria-label={`Move ${track.title} down`} disabled={index === tracks.length - 1} onClick={() => onMove(index, index + 1)} type="button"><ChevronDown aria-hidden="true" /></button>
        <button aria-label={`Remove ${track.title}`} onClick={() => onRemove(index)} type="button"><X aria-hidden="true" /></button>
      </span> : null}
    </li>
  ))}</ol>;
}

function rulesFrom(form: HTMLFormElement, artistId: string | null): MusicPlaylistRules {
  const data = new FormData(form);
  const number = (name: string) => {
    const raw = String(data.get(name) || '').trim();
    return raw ? Number(raw) : null;
  };
  return {
    genres: String(data.get('genres') || '').split(',').map((genre) => genre.trim()).filter(Boolean).slice(0, 20),
    artist_id: data.get('current-artist') === 'on' ? artistId : null,
    year_from: number('year-from'),
    year_to: number('year-to'),
    favorites: data.get('favorites') === 'on',
    unplayed: data.get('unplayed') === 'on',
  };
}

export function MusicPlaylists({ owner }: { owner: string }) {
  const session = useMusicSession();
  const [playlists, setPlaylists] = useState<MusicPlaylist[] | null>(null);
  const [selected, setSelected] = useState<MusicPlaylist | null>(null);
  const [tracks, setTracks] = useState<MusicTrack[] | null>(null);
  const [editName, setEditName] = useState('');
  const [editShared, setEditShared] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createShared, setCreateShared] = useState(false);
  const [createSmart, setCreateSmart] = useState(false);
  const [includeQueue, setIncludeQueue] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const request = useRef(0);

  const refresh = useCallback(async (preferId?: string) => {
    const current = ++request.current;
    setProblem(null);
    setPlaylists(null);
    try {
      const found = await api.listMusicPlaylists();
      if (request.current !== current) return;
      setPlaylists(found);
      if (preferId) {
        const preferred = found.find((playlist) => playlist.id === preferId) ?? null;
        setSelected(preferred); setTracks(null);
        if (!preferred) return;
        setEditName(preferred.name); setEditShared(preferred.shared);
        try {
          const loaded = await api.getMusicPlaylistTracks(preferred.id);
          if (request.current === current) setTracks(loaded);
        } catch (error) {
          if (request.current === current) { setTracks([]); setProblem(messageFor(error)); }
        }
      }
    } catch (error) {
      if (request.current === current) { setPlaylists([]); setProblem(messageFor(error)); }
    }
  }, []);

  useEffect(() => {
    request.current += 1;
    setSelected(null); setTracks(null); setEditName(''); setNotice(null); setProblem(null);
    void refresh();
    return () => { request.current += 1; };
  }, [owner, refresh]);

  const open = useCallback(async (playlist: MusicPlaylist) => {
    const current = ++request.current;
    setSelected(playlist); setEditName(playlist.name); setEditShared(playlist.shared); setTracks(null); setProblem(null); setNotice(null);
    try {
      const loaded = await api.getMusicPlaylistTracks(playlist.id);
      if (request.current === current) setTracks(loaded);
    } catch (error) {
      if (request.current === current) { setTracks([]); setProblem(messageFor(error)); }
    }
  }, []);

  const play = async (queue: MusicTrack[]) => {
    if (!queue.length) { setProblem('There are no tracks to play.'); return; }
    setBusy('play'); setProblem(null);
    try { setMusicOwner(owner); await playMusicQueue(queue); }
    catch (error) { setProblem(messageFor(error, 'play')); }
    finally { setBusy(null); }
  };

  const playMix = async (kind: MixKind) => {
    const current = musicSession().queue[musicSession().playback.index] ?? null;
    if (kind === 'radio' && !current) { setProblem('Play a track first to start Track radio.'); return; }
    setBusy(`mix:${kind}`); setProblem(null); setNotice(null);
    try {
      const queue = await api.getMusicMix(kind, kind === 'radio' ? current?.id : undefined);
      if (!queue.length) { setProblem(kind === 'radio' ? 'No related tracks were found.' : `There are no tracks in ${MIXES.find((mix) => mix.kind === kind)?.name}.`); return; }
      await play(queue);
    } catch (error) { setProblem(messageFor(error, 'play')); }
    finally { setBusy(null); }
  };

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const cleanName = createName.trim();
    if (!cleanName) { setProblem('Give the playlist a name.'); return; }
    const liveSession = musicSession();
    const current = liveSession.queue[liveSession.playback.index] ?? null;
    const body: MusicPlaylistSave = createSmart
      ? { name: cleanName, shared: createShared, item_ids: [], rules: rulesFrom(event.currentTarget, current?.artist_id ?? null) }
      : { name: cleanName, shared: createShared, item_ids: includeQueue ? liveSession.queue.map((track) => track.id) : [] };
    setBusy('create'); setProblem(null); setNotice(null);
    try {
      const saved = await api.saveMusicPlaylist(body);
      setNotice(`${saved.name} was created.`); setCreateName(''); setCreateShared(false); setCreateSmart(false); setIncludeQueue(true);
      await refresh(saved.id);
    } catch (error) { setProblem(messageFor(error, 'save')); }
    finally { setBusy(null); }
  };

  const save = async () => {
    if (!selected || !tracks || !selected.can_edit) return;
    const cleanName = editName.trim();
    if (!cleanName) { setProblem('Give the playlist a name.'); return; }
    setBusy('save'); setProblem(null); setNotice(null);
    try {
      const saved = await api.saveMusicPlaylist({ name: cleanName, shared: editShared, item_ids: selected.rules ? [] : tracks.map((track) => track.id), rules: selected.rules, revision: selected.revision }, selected.id);
      setSelected(saved); setPlaylists((current) => current?.map((playlist) => playlist.id === saved.id ? saved : playlist) ?? current); setNotice('Playlist saved.');
    } catch (error) { setProblem(messageFor(error, 'save')); }
    finally { setBusy(null); }
  };

  const remove = async () => {
    if (!selected?.can_edit) return;
    setBusy('delete'); setProblem(null); setNotice(null);
    try {
      await api.deleteMusicPlaylist(selected.id);
      setPlaylists((current) => current?.filter((playlist) => playlist.id !== selected.id) ?? current);
      setSelected(null); setTracks(null); setEditName(''); setNotice('Playlist deleted.');
    } catch (error) { setProblem(messageFor(error, 'delete')); }
    finally { setBusy(null); }
  };

  const move = (from: number, to: number) => setTracks((current) => {
    if (!current || to < 0 || to >= current.length) return current;
    const next = [...current]; const [track] = next.splice(from, 1); next.splice(to, 0, track); return next;
  });

  const currentTrack = session.queue[session.playback.index] ?? null;
  return <section aria-labelledby="music-playlists-title" className="music-playlists">
    <header className="music-playlists-heading">
      <div><p className="g-kicker">Your listening room</p><h2 id="music-playlists-title">Playlists & mixes</h2><p>Keep a queue, build a rule-based playlist, or start a mix made from your library.</p></div>
      <TextButton aria-label="Refresh playlists" busy={busy === 'refresh'} icon={<RefreshCw />} onClick={() => void refresh(selected?.id)}>Refresh</TextButton>
    </header>

    {(problem || notice) ? <div aria-live="polite" className={`music-playlists-message ${problem ? 'is-error' : ''}`} role="status">{problem ?? notice}</div> : null}

    <section aria-labelledby="music-mixes-title" className="music-playlists-section">
      <h3 id="music-mixes-title">Made for now</h3>
      <div className="music-mix-grid">{MIXES.map((mix) => <article key={mix.kind}>
        <span className="music-mix-icon" aria-hidden="true">{mix.kind === 'radio' ? <Radio /> : <ListMusic />}</span>
        <h4>{mix.name}</h4><p>{mix.description}</p>
        <Button busy={busy === `mix:${mix.kind}`} disabled={mix.kind === 'radio' && !currentTrack} icon={<Play />} onClick={() => void playMix(mix.kind)}>{mix.kind === 'radio' && !currentTrack ? 'Play a track first' : `Play ${mix.name}`}</Button>
      </article>)}</div>
    </section>

    <div className="music-playlists-workspace">
      <section aria-labelledby="saved-playlists-title" className="music-playlists-section music-playlists-saved">
        <div className="music-playlists-section-head"><h3 id="saved-playlists-title">Saved playlists</h3>{playlists ? <span>{playlists.length}</span> : null}</div>
        {playlists === null ? <p aria-live="polite" role="status">Loading playlists…</p>
          : playlists.length ? <ul>{playlists.map((playlist) => <li key={playlist.id}><button aria-current={selected?.id === playlist.id ? 'true' : undefined} onClick={() => void open(playlist)} type="button"><span><strong>{playlist.name}</strong><small>{playlist.rules ? 'Smart playlist' : `${playlist.count ?? 0} track${playlist.count === 1 ? '' : 's'}`}{playlist.shared ? ' · Shared' : ''}</small></span><span>{playlist.can_edit ? 'Yours' : 'View'}</span></button></li>)}</ul>
          : <p className="music-playlists-empty">No playlists yet. Make one from the queue or create an empty playlist.</p>}
      </section>

      <section aria-labelledby="playlist-editor-title" className="music-playlists-section music-playlist-editor">
        {selected ? <>
          <div className="music-playlists-section-head"><div><p className="g-kicker">{selected.rules ? 'Smart playlist' : selected.can_edit ? 'Your playlist' : 'Shared playlist'}</p><h3 id="playlist-editor-title">{selected.name}</h3></div><Button busy={busy === 'play'} disabled={!tracks?.length} icon={<Play />} onClick={() => void play(tracks ?? [])} variant="primary">Play all</Button></div>
          {selected.can_edit ? <div className="music-playlist-fields"><label><span>Name</span><input maxLength={120} onChange={(event) => setEditName(event.target.value)} value={editName} /></label><label className="music-playlist-check"><input checked={editShared} onChange={(event) => setEditShared(event.target.checked)} type="checkbox" /> Share with the household</label></div> : <p className="music-playlists-readonly">Shared by another household member. You can listen, but only its owner can change it.</p>}
          {tracks === null ? <p aria-live="polite" role="status">Loading tracks…</p> : <PlaylistRows editable={selected.can_edit && !selected.rules} onMove={move} onRemove={(index) => setTracks((current) => current?.filter((_, at) => at !== index) ?? current)} tracks={tracks} />}
          {selected.can_edit ? <div className="music-playlist-editor-actions"><Button busy={busy === 'save'} icon={<Save />} onClick={() => void save()} variant="primary">Save changes</Button><Button busy={busy === 'delete'} icon={<Trash2 />} onClick={() => void remove()} variant="danger">Delete playlist</Button></div> : null}
        </> : <><p className="g-kicker">Choose a playlist</p><h3 id="playlist-editor-title">Playlist details</h3><p className="music-playlists-empty">Select a saved playlist to see its tracks, play it, or make changes.</p></>}
      </section>
    </div>

    <section aria-labelledby="new-playlist-title" className="music-playlists-section music-playlist-create">
      <div><p className="g-kicker">Make it yours</p><h3 id="new-playlist-title">New playlist</h3><p>A regular playlist can start with the current queue. A smart playlist updates as your library changes.</p></div>
      <form onSubmit={(event) => void create(event)}>
        <label><span>Name</span><input maxLength={120} onChange={(event) => setCreateName(event.target.value)} placeholder="Sunday morning" value={createName} /></label>
        <fieldset><legend>Playlist type</legend><label><input checked={!createSmart} onChange={() => setCreateSmart(false)} name="playlist-type" type="radio" /> Regular</label><label><input checked={createSmart} onChange={() => setCreateSmart(true)} name="playlist-type" type="radio" /> Smart</label></fieldset>
        {!createSmart ? <label className="music-playlist-check"><input checked={includeQueue} onChange={(event) => setIncludeQueue(event.target.checked)} type="checkbox" /> Start with current queue ({session.queue.length} tracks)</label> : <div className="music-smart-rules">
          <label><span>Genres <small>separate with commas</small></span><input name="genres" placeholder="Jazz, Soul" /></label>
          <label><span>From year</span><input inputMode="numeric" max={2200} min={1000} name="year-from" type="number" /></label>
          <label><span>Through year</span><input inputMode="numeric" max={2200} min={1000} name="year-to" type="number" /></label>
          <label className="music-playlist-check"><input name="favorites" type="checkbox" /> Favorites only</label>
          <label className="music-playlist-check"><input name="unplayed" type="checkbox" /> Unplayed only</label>
          {currentTrack?.artist_id ? <label className="music-playlist-check"><input name="current-artist" type="checkbox" /> Only {currentTrack.artist || 'the current artist'}</label> : null}
        </div>}
        <label className="music-playlist-check"><input checked={createShared} onChange={(event) => setCreateShared(event.target.checked)} type="checkbox" /> Share with the household</label>
        <Button busy={busy === 'create'} icon={<Plus />} type="submit" variant="primary">Create playlist</Button>
      </form>
    </section>
  </section>;
}
