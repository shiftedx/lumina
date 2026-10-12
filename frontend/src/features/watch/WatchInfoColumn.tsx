/**
 * The editorial column under a web video's player: kicker, serif headline, meta,
 * byline with Follow, actions, the full description (6-line clamp, every timestamp a seek, URLs as text), chapters and
 * the remaining tools. WatchSurface computes every value; this only lays them out.
 */
import { Check, Plus } from 'lucide-react';
import { type ReactNode, useId, useState } from 'react';

import { ChapterList, type DescriptionTimestamp, DescriptionWithTimestamps, type TimelineChapter } from '../../chapters';
import { formatCompactNumber } from '../../utils';
import { channelPagePath, followAppLink, rememberChannel } from '../channels/channelMention';
import { ChannelAvatar } from '../gallery/ChannelAvatar';
import { LiveBadge, type LiveBadgeState } from '../gallery/LiveBadge';
import { moveFocus } from '../media/focusNav';
import './watch.css';

export const DESCRIPTION_MAX = 5_000;
/** About six clamped lines of body text; a shorter description has nothing to expand. */
export const CLAMP_CHARS = 420;
const CLAMP_LINES = 6; // keep in sync with -webkit-line-clamp in watch.css

export type WatchInfoColumnProps = {
  provider: string;
  badge: LiveBadgeState | null;
  startsAt?: string | null;
  saved: boolean;
  title: string;
  meta: string;
  channel: { name: string; id: string | null; avatarUrl?: string | null; followers: number | null; imported: boolean; following: boolean; onFollow: () => void };
  actions: ReactNode;
  recording?: ReactNode;
  feedback?: ReactNode;
  description: string | null;
  timestamps: readonly DescriptionTimestamp[];
  chapters: readonly TimelineChapter[];
  currentTime: number;
  onSeek: (seconds: number) => void;
  tools?: ReactNode;
};

export function WatchInfoColumn({ provider, badge, startsAt, saved, title, meta, channel, actions, recording, feedback, description, timestamps, chapters, currentTime, onSeek, tools }: WatchInfoColumnProps) {
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const cut = description && description.length > DESCRIPTION_MAX ? DESCRIPTION_MAX - (/[\uD800-\uDBFF]/.test(description[DESCRIPTION_MAX - 1]) ? 1 : 0) : 0;
  const text = description ? (cut ? `${description.slice(0, cut)}…` : description) : null;
  const name = channel.id
    ? <a className="g-watch-channel" data-focus-item href={channelPagePath(channel.id)} onClick={(event) => { rememberChannel({ id: channel.id!, name: channel.name, avatarUrl: channel.avatarUrl }); followAppLink(event); }}>{channel.name}</a>
    : <span className="g-watch-channel">{channel.name}</span>;
  return (
    <section aria-labelledby={titleId} className="g-watch-column" onKeyDown={moveFocus}>
      <p className="g-label g-watch-kicker">{provider}{badge ? <> · <LiveBadge startsAt={startsAt} state={badge} surface="paper" /></> : null}{saved ? ' · In your library' : null}</p>
      <h1 className="g-watch-headline" id={titleId}>{title}</h1>
      {meta ? <p className="g-label g-watch-meta">{meta}</p> : null}
      <div className="g-watch-byline" data-focus-row>
        <ChannelAvatar name={channel.name} size={40} url={channel.avatarUrl} />
        <div className="g-watch-byline-copy">
          {name}
          <span className="g-label">{channel.imported ? 'Imported · read-only' : channel.followers ? `${formatCompactNumber(channel.followers)} followers` : null}</span>
        </div>
        {channel.imported ? null : (
          <button aria-pressed={channel.following} className={`g-button${channel.following ? '' : ' is-primary'}`} data-focus-item onClick={channel.onFollow} type="button">
            {channel.following ? <Check aria-hidden="true" /> : <Plus aria-hidden="true" />}{channel.following ? 'Following' : 'Follow'}
          </button>
        )}
      </div>
      <div className="g-watch-actions" data-focus-row>{actions}</div>
      {feedback}
      {recording ? <div className="g-watch-recording">{recording}</div> : null}
      {text ? (
        <div className="g-watch-about">
          <div className={`g-watch-description${open ? '' : ' is-clamped'}`}>
            <DescriptionWithTimestamps description={text} onSeek={onSeek} timestamps={timestamps.filter((token) => token.end <= DESCRIPTION_MAX)} />
          </div>
          {text.length > CLAMP_CHARS || text.split('\n').length > CLAMP_LINES ? <button className="g-text-button" data-focus-item onClick={() => setOpen((value) => !value)} type="button">{open ? 'Less' : 'More'}</button> : null}
        </div>
      ) : null}
      <ChapterList chapters={chapters} currentTime={currentTime} onSeek={onSeek} />
      {tools}
    </section>
  );
}
