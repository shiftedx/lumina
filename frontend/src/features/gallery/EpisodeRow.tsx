/**
 * One season's episodes as a sideways row of stills. Unwatched and in-progress episodes only ever show the
 * episode guide's teaser; watched ones may show their AI summary.
 * A still carries the poster marker at its small size.
 */
import { useEffect, useMemo, useRef, useState } from 'react';

import { MoreHorizontal } from 'lucide-react';

import { getEpisodeSummaries, listSeasonEpisodes } from '../../api';
import type { EpisodeSummaries, TitleSummary } from '../../types';
import { Menu } from '../../ui';
import { useFetched } from '../titles/titleModel';
import { GalleryArt } from './GalleryArt';
import { cardColour, posterMarker } from './galleryModel';
import { ArtMarker } from './PosterCard';
import { AI_TIMEOUT, episodeBlurb, episodeHeading, episodeLabel, episodeMeta, playNextScrollLeft, stillPriority } from './titlePageModel';

const STILL_SIZES = '(max-width: 599px) min(72vw, 300px), 320px';
const NO_SUMMARIES: EpisodeSummaries = { available: false, items: [] };
const byIndex = (a: TitleSummary, b: TitleSummary) => (a.index_number ?? 0) - (b.index_number ?? 0);

export function EpisodeRow({ seriesId, season, playNextId, revision = 0, onPlay, canEdit = false, onEdit }: {
  seriesId: string;
  season: number;
  playNextId: string | null;
  revision?: number;
  onPlay: (itemId: string) => void;
  canEdit?: boolean;
  onEdit?: (episodeId: string) => void;
}) {
  const [attempt, setAttempt] = useState(0);
  const episodes = useFetched(`${seriesId}:${season}`, () => listSeasonEpisodes(seriesId, season), revision + attempt);
  const ordered = useMemo(() => (episodes.data ? [...episodes.data].sort(byIndex) : null), [episodes.data]);
  // Summaries exist only for watched episodes, so a season with none watched asks for nothing.
  const anyWatched = Boolean(ordered?.some((episode) => episode.user_data.played));
  const summaries = useFetched(anyWatched ? `${seriesId}:${season}:summaries` : null, () => getEpisodeSummaries(seriesId, season, AI_TIMEOUT).catch(() => NO_SUMMARIES), revision);
  const texts = new Map(summaries.data?.available ? summaries.data.items.map((item) => [item.episode_id, item.overview]) : []);
  const firstInView = Math.max(0, ordered?.findIndex((episode) => episode.id === playNextId) ?? 0);
  const listRef = useRef<HTMLOListElement>(null);
  const scrolledFor = useRef<string | null>(null);

  useEffect(() => {
    // Once per season: the next episode becomes the first fully visible card (behavior 'auto').
    const list = listRef.current;
    const key = `${seriesId}:${season}`;
    if (!list || !ordered || scrolledFor.current === key) return;
    scrolledFor.current = key;
    const card = list.children[firstInView] as HTMLElement | undefined;
    if (firstInView > 0 && card) list.scrollLeft = playNextScrollLeft(card.offsetLeft, parseFloat(getComputedStyle(list).columnGap) || 0);
  }, [ordered, firstInView, seriesId, season]);

  if (episodes.error) {
    return <div className="t-row-state" role="alert"><span>Lumina could not load this season.</span> <button className="g-button g-button-text" onClick={() => setAttempt((value) => value + 1)} type="button">Try again</button></div>;
  }
  if (!ordered) return <p className="t-row-state" role="status">Loading episodes…</p>;
  if (!ordered.length) return <p className="t-row-state">No episodes in this season yet.</p>;
  return (
    <ol className="t-episodes" data-focus-row ref={listRef}>
      {ordered.map((episode, index) => {
        const meta = episodeMeta(episode);
        const blurb = episodeBlurb(episode, texts.get(episode.id));
        const metaId = `t-episode-meta-${episode.id}`;
        const blurbId = `t-episode-blurb-${episode.id}`;
        return (
          <li key={episode.id}>
            <button aria-describedby={[meta ? metaId : null, blurb ? blurbId : null].filter(Boolean).join(' ') || undefined} aria-label={episodeLabel(episode)} className="t-episode" data-focus-item disabled={!episode.play_item_id} onClick={() => { if (episode.play_item_id) onPlay(episode.play_item_id); }} type="button">
              <span className="t-still">
                <GalleryArt alt="" art={episode.poster} card={{ name: episode.name }} colour={cardColour(episode, 'still')} kind="still" position={index} priority={stillPriority(index, firstInView)} sizes={STILL_SIZES} />
                <ArtMarker marker={posterMarker(episode)} small />
              </span>
              <span className="t-episode-title">{episodeHeading(episode)}</span>
              {meta ? <span className="t-episode-meta g-label" id={metaId}>{meta}</span> : null}
              {blurb ? <span className="t-episode-blurb" id={blurbId}>{blurb}</span> : null}
            </button>
            {canEdit && onEdit ? (
              <Menu align="end" arrowOpens={false} items={[{ kind: 'item', label: 'Edit details', onSelect: () => onEdit(episode.id) }]}
                trigger={(props) => <button {...props} aria-label={`More for ${episodeLabel(episode)}`} className="g-icon-button t-episode-more" data-focus-item type="button"><MoreHorizontal aria-hidden="true" /></button>} />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
