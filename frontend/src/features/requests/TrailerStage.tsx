/**
 * A trailer playing inline, in the hero, through Lumina's own remote playback: the same preview → RemotePlayer path the
 * /watch?url=https://www.youtube.com/watch?v=<id> route uses. Never a YouTube iframe. The player chunk loads on first use.
 */
import { X } from 'lucide-react';
import { lazy, Suspense, useEffect, useState } from 'react';

import { previewUrl } from '../../api';
import type { RemotePlayback } from '../../types';
import { Button, IconButton } from '../../ui';
import type { Trailer } from './requestsApi';

const RemotePlayer = lazy(() => import('../../remotePlayer').then((module) => ({ default: module.RemotePlayer })));

export const trailerUrl = (trailer: Pick<Trailer, 'youtube_id'>) => `https://www.youtube.com/watch?v=${encodeURIComponent(trailer.youtube_id)}`;

export function TrailerStage({ trailers, title, poster, onClose }: { trailers: Trailer[]; title: string; poster?: string | null; onClose: () => void }) {
  const [index, setIndex] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const trailer = trailers[index];
  const [state, setState] = useState<{ id: string; playback: RemotePlayback | null; failed: boolean } | null>(null);
  useEffect(() => {
    if (!trailer) return undefined;
    const controller = new AbortController();
    previewUrl({ source_url: trailerUrl(trailer), lazy_playlist: true }, { signal: controller.signal, timeoutMs: 30_000 }).then(
      (preview) => { if (!controller.signal.aborted) setState({ id: trailer.youtube_id, playback: preview.playback ?? null, failed: !preview.playback }); },
      () => { if (!controller.signal.aborted) setState({ id: trailer.youtube_id, playback: null, failed: true }); },
    );
    return () => controller.abort();
  }, [trailer, attempt]);
  if (!trailer) return null;
  const current = state?.id === trailer.youtube_id ? state : null;
  const name = `${title} · ${trailer.name}`;
  return (
    <div aria-label={`Trailer: ${name}`} className="rq-trailer" role="region">
      <div className="player-frame rq-trailer-player">
        {current?.playback ? (
          <Suspense fallback={<p className="rq-trailer-note">Starting the trailer…</p>}>
            <RemotePlayer playback={current.playback} poster={poster ?? null} title={name} />
          </Suspense>
        ) : current?.failed ? (
          <p className="rq-trailer-note">This trailer can’t be played right now. <Button onClick={() => { setState(null); setAttempt((value) => value + 1); }} variant="quiet">Try again</Button></p>
        ) : <p className="rq-trailer-note" role="status">Starting the trailer…</p>}
      </div>
      <div className="rq-trailer-bar">
        {trailers.length > 1 ? (
          <div aria-label="Trailers" className="rq-trailer-picks" role="group">
            {trailers.map((option, position) => <button aria-pressed={position === index} className="g-chip" key={option.youtube_id} onClick={() => setIndex(position)} type="button">{option.name}</button>)}
          </div>
        ) : <span />}
        <IconButton icon={<X />} label="Close trailer" onClick={onClose} />
      </div>
    </div>
  );
}
