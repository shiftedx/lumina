/**
 * The LIVE badge: LIVE with a pulsing gold dot, UPCOMING (or its start) with a gold ring,
 * ENDED with no dot. One component for cards, heroes, the channel header, the watch page, the player and the Live
 * shelf. Decoration only: the word is always present and the surrounding control names the state.
 */
import type { MediaLifecycle } from '../../types';
import './liveBadge.css';

export type LiveBadgeState = 'live' | 'upcoming' | 'ended';

export type LiveBadgeProps = {
  state: LiveBadgeState;
  /** 'art': over artwork (fixed dark chip). 'paper': on the page (ink chip, inverts with the theme). 'bar': the player's control bar (fixed). */
  surface: 'art' | 'paper' | 'bar';
  /** Upcoming only: the provider's start time, shown as "8:30 PM" today or "TUE 8:30 PM" later. */
  startsAt?: string | null;
  className?: string;
};

/** The badge for a lifecycle; `ended` (the relay reported the end, or a kept card) wins. Null for video on demand. */
export function liveBadgeState(lifecycle: MediaLifecycle | null | undefined, ended = false): LiveBadgeState | null {
  if (ended) return 'ended';
  if (lifecycle === 'live') return 'live';
  if (lifecycle === 'upcoming') return 'upcoming';
  if (lifecycle === 'post_live' || lifecycle === 'completed_live') return 'ended';
  return null;
}

/** "8:30 PM" when the stream starts today, "TUE 8:30 PM" later, "UPCOMING" when the start is unknown. */
export function upcomingLabel(startsAt: string | null | undefined, now: Date = new Date()): string {
  const at = startsAt ? new Date(startsAt) : null;
  if (!at || Number.isNaN(at.getTime())) return 'UPCOMING';
  const time = at.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }).toUpperCase();
  return at.toDateString() === now.toDateString() ? time : `${at.toLocaleDateString(undefined, { weekday: 'short' }).toUpperCase()} ${time}`;
}

export function LiveBadge({ state, surface, startsAt, className }: LiveBadgeProps) {
  const text = state === 'live' ? 'LIVE' : state === 'ended' ? 'ENDED' : upcomingLabel(startsAt);
  return (
    <span aria-hidden="true" className={`g-live is-${state} on-${surface}${className ? ` ${className}` : ''}`}>
      {state === 'ended' ? null : <span className="g-live-dot" />}
      {text}
    </span>
  );
}
