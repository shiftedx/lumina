import { useEffect, useState } from 'react';
import { getContextualMusicMix, saveMusicPlaylist, type MusicMixPreview, type MusicMixSeedKind } from '../../api';
import { Button, ConfirmDialog, Dialog, Input } from '../../ui';
import { appendMusicMix, playMusicMix, useMusicSession } from './musicSession';
import './music.css';

export function MusicMixButton({ kind, id, name, enabled }: { kind: MusicMixSeedKind; id: string; name: string; enabled: boolean }) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<MusicMixPreview | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [playlistName, setPlaylistName] = useState('');
  const [saved, setSaved] = useState(false);
  const session = useMusicSession();
  useEffect(() => {
    if (!open) return;
    let current = true;
    setPreview(null); setError(''); setSaved(false); setPlaylistName(`${name} mix`);
    void getContextualMusicMix(kind, id).then((value) => { if (current) setPreview(value); }, () => { if (current) setError('Could not load this mix. Close and try again.'); });
    return () => { current = false; };
  }, [open, kind, id, name]);
  if (!enabled) return null;
  const label = kind === 'track' ? 'Track Radio' : 'Instant Mix';
  const run = async (action: 'play' | 'append' | 'save') => {
    if (!preview?.items.length) return;
    setBusy(true); setError('');
    try {
      if (action === 'save') {
        await saveMusicPlaylist({ name: playlistName.trim(), shared: false, item_ids: preview.items.map((item) => item.id) });
        setSaved(true);
      } else {
        if (!session.owner) throw new Error('Your music session is not ready.');
        if (action === 'play') await playMusicMix(session.owner, preview.items);
        else await appendMusicMix(session.owner, preview.items);
        setConfirm(false); setOpen(false);
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'This action could not be completed.'); setConfirm(false); }
    finally { setBusy(false); }
  };
  return <><button aria-label={`${label} for ${name}`} className="g-text-button music-mix-trigger" data-focus-item onClick={() => setOpen(true)} type="button">{label}</button>
    <Dialog busy={busy} onClose={() => setOpen(false)} open={open} size="lg" title={`${label} · ${name}`} footer={<><Button disabled={!preview?.items.length || busy} onClick={() => setConfirm(true)} variant="primary">Play Mix</Button><Button disabled={!preview?.items.length || busy} onClick={() => void run('append')}>Add to Queue</Button></>}>
      {!preview && !error ? <p>Finding tracks…</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {preview && !preview.items.length ? <p>No matching tracks are available for this mix.</p> : null}
      {preview?.items.length ? <><p>{preview.items.length} tracks in order</p><ol className="music-mix-list">{preview.items.map((item) => <li key={item.id}><strong>{item.title}</strong><span>{item.artist} · {item.reason.code === 'seed_track' ? 'From this album' : item.reason.code === 'same_artist' ? 'Same artist' : `Shared genre: ${item.reason.genre}`}</span></li>)}</ol><div className="music-mix-save"><label htmlFor={`mix-name-${kind}-${id}`}>Playlist name</label><Input id={`mix-name-${kind}-${id}`} maxLength={120} onChange={(event) => setPlaylistName(event.target.value)} value={playlistName} /><Button disabled={busy || !playlistName.trim() || saved} onClick={() => void run('save')}>Save as Playlist</Button>{saved ? <span role="status">Playlist saved.</span> : null}</div></> : null}
    </Dialog>
    <ConfirmDialog body={<>Replace your current queue and start this mix?{session.unavailablePolicy === 'wait' && session.sharedEntries.some((entry) => entry.kind === 'acquisition' && entry.state !== 'playable') ? ' This removes the waiting request from the queue.' : ''}</>} busy={busy} confirmLabel="Play Mix" onCancel={() => setConfirm(false)} onConfirm={() => void run('play')} open={confirm} title="Replace queue?" />
  </>;
}
