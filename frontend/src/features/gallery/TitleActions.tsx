/**
 * The title page's version picker, actions and notices, and the playback warm-up on the primary
 * action. Every change the member makes forgets the cached detail, so reopening the page
 * refetches.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Clapperboard, Heart, ListPlus, Pencil, Play, Wrench } from 'lucide-react';

import { setFavorite, setTitleWatched } from '../../api';
import { cancelSpeculativeStart, prefetchPlaybackOptions, speculativeStart } from '../../playbackPrefetch';
import type { TitleDetail } from '../../types';
import { IdentifyDialog } from '../titles/IdentifyDialog';
import { primaryAction, versionLabel } from '../titles/titleModel';
import { queueMedia } from '../watch/WatchQueue';
import { forgetTitle } from './titleCache';

/** Hover or focus this long on the primary action before a conversion may start early. */
export const SPECULATE_AFTER_MS = 400;

export type TitleActionsProps = {
  detail: TitleDetail;
  user: { role: string; can_edit_details?: boolean };
  onPlay: (itemId: string) => void;
  /** Patch the page's copy after a change the member made (watched, favorite). */
  onPatch: (update: (current: TitleDetail) => TitleDetail) => void;
  /** Reload the title: a series' episodes and next episode change with its watched state; a fixed match changes everything. */
  onReload: () => void;
  /** Open the metadata editor (shown only to people who can edit). */
  onEdit?: () => void;
};

export function TitleActions({ detail, user, onPlay, onPatch, onReload, onEdit }: TitleActionsProps) {
  const [versionId, setVersionId] = useState<string | null>(null);
  const [identifying, setIdentifying] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const fixRef = useRef<HTMLButtonElement>(null);
  const speculation = useRef<{ itemId: string; timer: number } | null>(null);
  const action = primaryAction(detail, versionId);
  const warmItem = action && !action.reason ? action.itemId : null;
  const chosenVersion = versionId ?? detail.user_data.resume_item_id ?? detail.play_item_id ?? null;
  const trailer = detail.extras.find((extra) => extra.extra_type === 'trailer');
  const { played, is_favorite: favorite } = detail.user_data;

  const endSpeculation = useCallback(() => {
    const current = speculation.current;
    if (!current) return;
    window.clearTimeout(current.timer);
    cancelSpeculativeStart(current.itemId);
    speculation.current = null;
  }, []);
  const beginSpeculation = (itemId: string) => {
    endSpeculation();
    speculation.current = { itemId, timer: window.setTimeout(() => speculativeStart(itemId), SPECULATE_AFTER_MS) };
  };
  useEffect(() => { if (warmItem) prefetchPlaybackOptions(warmItem); }, [warmItem]);
  useEffect(() => endSpeculation, [endSpeculation]);

  async function run(work: () => Promise<void>, failure: string) {
    setNotice(null);
    try { await work(); } catch { setNotice(failure); }
  }
  const toggleWatched = () => run(async () => {
    const userData = await setTitleWatched(detail.id, !played);
    forgetTitle(detail.id);
    onPatch((current) => ({ ...current, user_data: userData }));
    // Series and seasons: episode rows and the next episode change too.
    if (detail.type !== 'movie') onReload();
  }, 'Lumina could not update watched state. Try again.');
  const toggleFavorite = () => run(async () => {
    await setFavorite(detail.id, !favorite);
    forgetTitle(detail.id);
    onPatch((current) => ({ ...current, user_data: { ...current.user_data, is_favorite: !favorite } }));
  }, 'Lumina could not update your favorites. Try again.');
  // Queueing a series queues its next episode.
  const addToWatchlist = () => run(async () => {
    if (!action?.itemId) return;
    setNotice(await queueMedia({ kind: 'library', library_item_id: action.itemId }, 'end') ? 'Added to your watchlist.' : 'Lumina could not update your watchlist. Try again.');
  }, 'Lumina could not update your watchlist. Try again.');

  return (
    <>
      {detail.type === 'movie' && detail.versions.length > 1 ? (
        <fieldset className="t-versions" data-focus-row>
          <legend className="g-label">Version</legend>
          {detail.versions.map((version) => <label key={version.item_id}><input checked={chosenVersion === version.item_id} data-focus-item name={`title-version-${detail.id}`} onChange={() => setVersionId(version.item_id)} type="radio" /> <span>{versionLabel(version)}</span></label>)}
        </fieldset>
      ) : null}
      <div className="t-actions" data-focus-row>
        {action ? (
          <button
            aria-describedby={action.reason ? 't-play-reason' : undefined} className="g-button g-button-text is-primary" data-focus-item disabled={!action.itemId || Boolean(action.reason)}
            onBlur={endSpeculation} onClick={() => { if (action.itemId) onPlay(action.itemId); }} onFocus={() => { if (warmItem) beginSpeculation(warmItem); }}
            onPointerEnter={() => { if (warmItem) beginSpeculation(warmItem); }} onPointerLeave={endSpeculation} type="button"
          >
            <Play aria-hidden="true" fill="currentColor" /> {action.label}
          </button>
        ) : null}
        <button aria-pressed={played} className="g-button g-button-text" data-focus-item onClick={() => void toggleWatched()} type="button"><Check aria-hidden="true" /> {played ? 'Watched' : 'Mark watched'}</button>
        <button aria-pressed={favorite} className="g-button g-button-text" data-focus-item onClick={() => void toggleFavorite()} type="button"><Heart aria-hidden="true" fill={favorite ? 'currentColor' : 'none'} /> Favorite</button>
        {trailer ? <button className="g-button g-button-text" data-focus-item onClick={() => onPlay(trailer.item_id)} type="button"><Clapperboard aria-hidden="true" /> Trailer</button> : null}
        {action?.itemId ? <button className="g-button g-button-text" data-focus-item onClick={() => void addToWatchlist()} type="button"><ListPlus aria-hidden="true" /> Add to watchlist</button> : null}
        {user.can_edit_details && onEdit && ['movie', 'series', 'season', 'episode'].includes(detail.type) ? <button className="g-button g-button-text" data-focus-item onClick={onEdit} type="button"><Pencil aria-hidden="true" /> Edit details</button> : null}
        {user.role === 'admin' ? <button className="g-button g-button-text" data-focus-item onClick={() => setIdentifying(true)} ref={fixRef} type="button"><Wrench aria-hidden="true" /> Fix match</button> : null}
      </div>
      {action?.reason ? <p className="t-note" id="t-play-reason">{action.reason}</p> : null}
      {notice ? <p className="t-note" role="status">{notice}</p> : null}
      {identifying ? <IdentifyDialog onChanged={(message) => { setNotice(message); forgetTitle(detail.id); onReload(); }} onClose={() => { setIdentifying(false); fixRef.current?.focus(); }} title={detail} /> : null}
    </>
  );
}
