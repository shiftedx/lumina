/**
 * Up next for a titled Library item: an episode's following episodes in order (with the autoplay switch and a way to the
 * whole series), or a movie's collection in collection order with the playing film marked. Nothing for anything else.
 * Rows reuse the watch queue's row styles.
 */
import { Check } from 'lucide-react';

import { Artwork } from '../../Artwork';
import type { LibraryUpNext, TitleSummary } from '../../types';
import { ProgressBar, Switch, TextButton } from '../../ui';
import { formatDuration } from '../../utils';
import { episodeCode } from '../titles/titleModel';
import './tools.css';

/** The title that plays after the current one: the first following episode, or the film after this one in its collection. */
export function nextInUpNext(upNext: LibraryUpNext | null): TitleSummary | null {
  if (!upNext) return null;
  const at = upNext.kind === 'collection' ? upNext.items.findIndex((item) => item.id === upNext.current_id) : -1;
  if (upNext.kind === 'collection' && at < 0) return null;
  return upNext.items.slice(at + 1).find((item) => item.play_item_id) ?? null;
}

/** The film before this one in its collection (an episode list holds only the following episodes). */
export function previousInUpNext(upNext: LibraryUpNext | null): TitleSummary | null {
  const at = upNext?.kind === 'collection' ? upNext.items.findIndex((item) => item.id === upNext.current_id) : -1;
  return at > 0 ? upNext!.items.slice(0, at).reverse().find((item) => item.play_item_id) ?? null : null;
}

function state(item: TitleSummary, current: boolean): string {
  if (current) return 'Now playing';
  const { played, position_seconds: position, duration_seconds: duration } = item.user_data;
  if (played) return 'Watched';
  const total = duration || item.runtime_seconds;
  if (position > 0 && total) return `${formatDuration(Math.max(0, total - position))} left`;
  return item.runtime_seconds ? formatDuration(item.runtime_seconds) : item.year ? String(item.year) : '';
}

export function LibraryUpNextRail({ upNext, autoplay, onAutoplayChange, onPlay, onOpenAll }: {
  upNext: LibraryUpNext;
  autoplay: boolean;
  onAutoplayChange: (autoplay: boolean) => void;
  onPlay: (itemId: string) => void;
  /** "See all episodes": the series page. */
  onOpenAll?: () => void;
}) {
  if (upNext.kind === 'none' || !upNext.items.length) return null;
  const episodes = upNext.kind === 'episodes';
  return (
    <section aria-labelledby="library-up-next-title" className="watch-queue library-up-next">
      <header>
        <h2 id="library-up-next-title">{episodes ? 'Up next' : upNext.title || 'Collection'}</h2>
        {episodes ? <Switch checked={autoplay} label="Autoplay" onChange={onAutoplayChange} /> : null}
      </header>
      <ol className="watch-queue-list">
        {upNext.items.map((item) => {
          const current = item.id === upNext.current_id;
          const name = [episodes ? episodeCode(item) : null, item.name].filter(Boolean).join(' · ');
          const total = item.user_data.duration_seconds || item.runtime_seconds;
          const started = !item.user_data.played && item.user_data.position_seconds > 0 && total;
          return (
            <li className="g-list-row watch-queue-item" key={item.id}>
              <button aria-current={current || undefined} className="watch-queue-play" disabled={current || !item.play_item_id} onClick={() => item.play_item_id && onPlay(item.play_item_id)} type="button">
                <span className="watch-queue-thumb">
                  <Artwork alt="" src={episodes ? item.poster?.url ?? item.poster_url : item.backdrop?.url ?? item.backdrop_url ?? item.poster_url} />
                  {item.user_data.played ? <b aria-hidden="true"><Check /></b> : null}
                  {started ? <ProgressBar className="library-up-next-progress" label="Watched so far" value={(item.user_data.position_seconds / total) * 100} /> : null}
                </span>
                <span className="watch-queue-copy"><strong>{name}</strong><small className={current ? 'watch-queue-next' : undefined}>{state(item, current)}</small></span>
              </button>
            </li>
          );
        })}
      </ol>
      {episodes && onOpenAll ? <TextButton onClick={onOpenAll}>See all episodes</TextButton> : null}
    </section>
  );
}
