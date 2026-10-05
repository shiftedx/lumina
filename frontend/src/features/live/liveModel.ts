/**
 * The Live page's model: which stream leads, the schedule, the rails, the kicker, the
 * availability lines and the in-place refresh merge that never moves or removes a focused card. Pure functions only.
 */
import { buildLiveRails } from '../../liveRails';
import { SOURCE_LABELS } from '../../luminaModel';
import type { LiveSnapshot, RemoteEntry, SearchSource } from '../../types';
import { liveCountLabel } from '../../utils';
import { isRemoteEnded, remoteProvider } from '../gallery/remoteModel';

export const LIVE_PROVIDERS: SearchSource[] = ['youtube', 'twitch', 'kick'];
export const UPCOMING_SHOWN = 8;
export const ENDED_SHOWN = 12;

export const entryKey = (item: RemoteEntry, index = 0): string => item.webpage_url || item.id || `entry-${index}`;
export const isLive = (item: RemoteEntry): boolean => item.capabilities?.lifecycle === 'live';
const byViewers = (a: RemoteEntry, b: RemoteEntry) => (b.view_count ?? -1) - (a.view_count ?? -1);

export function clockTime(iso: string | null | undefined): string | null {
  const at = iso ? new Date(iso) : null;
  return at && !Number.isNaN(at.getTime()) ? at.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : null;
}

/** Providers that could not be checked, with when Lumina last tried; a failed provider is never "nobody live". */
export function providerChecks(snapshot: LiveSnapshot | null): Map<SearchSource, string | null> {
  const failed = new Map<SearchSource, string | null>();
  if (snapshot && !snapshot.twitch_available) failed.set('twitch', snapshot.refreshed_at ?? null);
  for (const { source, checked_at: checkedAt } of snapshot?.followed_unavailable || []) failed.set(source, checkedAt);
  return failed;
}

export type LiveRailView = { key: string; label: string; liveCount: number | null; entries: RemoteEntry[] };
export type LiveView = {
  providers: SearchSource[];
  hero: RemoteEntry | null;
  heroFromFollows: boolean;
  alsoFollowed: RemoteEntry[];
  upcoming: RemoteEntry[];
  rails: LiveRailView[];
  ended: RemoteEntry[];
  liveCount: number;
  followedCount: number;
};

export function liveView(snapshot: LiveSnapshot | null, provider: SearchSource | 'all'): LiveView {
  const hero = snapshot?.hero ?? [];
  const all = [...new Map([...hero, ...(snapshot?.items ?? [])].map((item, index) => [entryKey(item, index), item])).values()];
  const failed = providerChecks(snapshot);
  const shown = (item: RemoteEntry) => provider === 'all' || remoteProvider(item) === provider;
  const followed = hero.filter((item) => shown(item) && isLive(item)).sort(byViewers);
  const liveNow = all.filter((item) => shown(item) && isLive(item));
  const lead = followed[0] ?? [...liveNow].sort(byViewers)[0] ?? null;
  const leadKey = lead ? entryKey(lead) : null;
  return {
    providers: LIVE_PROVIDERS.filter((key) => failed.has(key) || all.some((item) => remoteProvider(item) === key)),
    hero: lead,
    heroFromFollows: followed.length > 0,
    alsoFollowed: followed.slice(1),
    upcoming: all.filter((item) => shown(item) && item.capabilities?.lifecycle === 'upcoming'),
    rails: buildLiveRails(snapshot)
      .map((rail) => ({ key: rail.key, label: rail.label, liveCount: rail.liveCount, entries: rail.entries.filter((item) => shown(item) && isLive(item) && entryKey(item) !== leadKey) }))
      .filter((rail) => rail.entries.length),
    ended: all.filter((item) => shown(item) && isRemoteEnded(item.capabilities?.lifecycle)).slice(0, ENDED_SHOWN),
    liveCount: liveNow.length,
    followedCount: followed.length,
  };
}

export type UpcomingGroup = { key: string; label: string; entries: RemoteEntry[] };

const startOf = (item: RemoteEntry): Date | null => {
  const at = item.capabilities?.scheduled_start ? new Date(item.capabilities.scheduled_start) : null;
  return at && !Number.isNaN(at.getTime()) ? at : null;
};

/** The schedule's day groups in the member's locale: Later today, Tomorrow, "Wed 1 Oct", Time not announced. */
export function upcomingGroups(entries: readonly RemoteEntry[], now: Date = new Date()): UpcomingGroup[] {
  const sorted = [...entries].sort((a, b) => (startOf(a)?.getTime() ?? Number.MAX_SAFE_INTEGER) - (startOf(b)?.getTime() ?? Number.MAX_SAFE_INTEGER));
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  const groups: UpcomingGroup[] = [];
  for (const item of sorted) {
    const at = startOf(item);
    const key = at ? at.toDateString() : 'unknown';
    const label = !at ? 'Time not announced'
      : key === now.toDateString() ? 'Later today'
        : key === tomorrow.toDateString() ? 'Tomorrow'
          : at.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
    const last = groups.at(-1);
    if (last?.key === key) last.entries.push(item);
    else groups.push({ key, label, entries: [item] });
  }
  return groups;
}

/** The masthead kicker. */
export function liveKicker(snapshot: LiveSnapshot | null, view: LiveView, compact = false): string {
  if (!snapshot) return '';
  const when = snapshot.refreshing ? 'Refreshing…'
    : snapshot.stale ? (clockTime(snapshot.last_success_at) ? `Last known at ${clockTime(snapshot.last_success_at)}` : null)
      : clockTime(snapshot.refreshed_at) ? `Checked ${clockTime(snapshot.refreshed_at)}` : null;
  return [liveCountLabel(snapshot.live_total, compact, 'live now') ?? `${view.liveCount} live now`, view.followedCount ? `${view.followedCount} from your follows` : null, when].filter(Boolean).join(' · ');
}

/** The availability band, one line each; the partial and stale lines only with content to qualify. */
export function liveNoticeLines(snapshot: LiveSnapshot | null, hasContent: boolean): string[] {
  if (!snapshot) return [];
  const lines: string[] = [];
  if (!snapshot.twitch_available) {
    const tried = clockTime(snapshot.refreshed_at);
    lines.push(`Twitch could not be checked${tried ? ` · last tried ${tried}` : ''}`);
  }
  for (const { source, checked_at: checkedAt } of snapshot.followed_unavailable || []) {
    lines.push(`Live status for followed ${SOURCE_LABELS[source]} channels is unavailable · last tried ${clockTime(checkedAt)}`);
  }
  if (hasContent && snapshot.state === 'partial') lines.push('Showing a partial feed while more categories warm up.');
  else if (hasContent && snapshot.stale) lines.push('Showing the last-known feed while discovery refreshes.');
  return lines;
}

/** "Started 2h ago" for the hero's label line. */
export function startedAgo(iso: string | null | undefined, now: Date = new Date()): string | null {
  const at = iso ? new Date(iso).getTime() : Number.NaN;
  if (Number.isNaN(at)) return null;
  const minutes = Math.max(0, Math.round((now.getTime() - at) / 60_000));
  return minutes < 60 ? `Started ${minutes}m ago` : minutes < 1440 ? `Started ${Math.floor(minutes / 60)}h ago` : `Started ${Math.floor(minutes / 1440)}d ago`;
}

export type RailMemory = { entries: RemoteEntry[]; ended: Set<string> };

/**
 * One rail across a refresh: the visit's order stays, entries update in place, newcomers append,
 * and a card that left while focused stays (ENDED) until focus leaves its rail. `focusedCard` is the focused card's key
 * when focus is inside this rail, else null.
 */
export function mergeRail(previous: RailMemory | undefined, incoming: readonly RemoteEntry[], focusedCard: string | null): RailMemory {
  if (!previous) return { entries: [...incoming], ended: new Set() };
  const fresh = new Map(incoming.map((item, index) => [entryKey(item, index), item]));
  const entries: RemoteEntry[] = [];
  const ended = new Set<string>();
  for (const old of previous.entries) {
    const key = entryKey(old);
    const next = fresh.get(key);
    if (next) {
      entries.push(next);
      fresh.delete(key);
    } else if (focusedCard !== null && (key === focusedCard || previous.ended.has(key))) {
      entries.push(old);
      ended.add(key);
    }
  }
  entries.push(...fresh.values());
  return { entries, ended };
}
