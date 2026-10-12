/**
 * Home's Live shelf: the member's followed channels live now, then popular live, as 16:9
 * stills with the shared LiveBadge and the provider as a text kicker. Polled every 60 s while mounted;
 * HomeSurface mounts it only while the shelf is visible in the member's layout and near the viewport, so a hidden shelf,
 * edit mode and every other surface cost nothing, and usePolledSnapshot skips ticks while the tab is hidden. Cards are
 * keyed by URL, so a refresh keeps images and focus.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react';

import { isProviderVisible, useStreamingProviders } from '../streaming/providers';
import { getLiveDiscovery } from '../../api';
import { usePolledSnapshot } from '../../app/usePolledSnapshot';
import { liveNotices, liveShelfEntries, type LiveShelfEntry } from '../../liveRails';
import { SOURCE_LABELS } from '../../luminaModel';
import type { LiveSnapshot, YouTubeSearchResult } from '../../types';
import { formatCompactNumber } from '../../utils';
import { ArtMenu } from '../gallery/ArtMenu';
import { fallbackColour } from '../gallery/galleryModel';
import { LiveBadge } from '../gallery/LiveBadge';
import { StillFrame } from '../gallery/StillCard';
import { cachedShelf, rememberShelf } from './homeCache';
import { HomeShelf } from './HomeShelf';
import type { ShelfStatus } from './homeShelves';

export type LiveShelfProps = {
  /** The signed-in member: the poll key (home-live:{id}) and the session cache. */
  userId: string;
  onOpenRemote: (item: YouTubeSearchResult) => void;
  /** "See all" → the Live surface. */
  onSeeAll: () => void;
  /** Each change of what the shelf holds, for edit mode's row status. */
  onStatus: (status: ShelfStatus) => void;
};

/** Home's Live refresh; the Live surface keeps 30 s. */
export const LIVE_POLL_MS = 60_000;
const SECTION = 'live';
/** The shelf unmounts with Home and its poll key names the member, so the hook's own `active` flag is the only gate needed. */
const MOUNTED_SESSION = { captureSessionToken: () => 0, isSessionTokenCurrent: () => true };

const keyOf = (entry: YouTubeSearchResult): string => entry.webpage_url || entry.id || '';
const providerOf = (entry: YouTubeSearchResult): string | null => entry.source_label || (entry.source ? SOURCE_LABELS[entry.source] : null) || null;

/** Caption line 2: "Following · Channel · 12.4K watching" for a followed channel, else without "Following". */
export function liveLine({ entry, followed }: LiveShelfEntry): string {
  const watching = entry.view_count != null ? `${formatCompactNumber(entry.view_count)} watching` : null;
  return [followed ? 'Following' : null, entry.uploader, watching].filter(Boolean).join(' · ');
}

/** "Live: {title}, {channel}, {n} watching on {Provider}", plus ", a channel you follow". */
export function liveLabel({ entry, followed }: LiveShelfEntry): string {
  const provider = providerOf(entry);
  const watching = entry.view_count != null ? `${entry.view_count.toLocaleString()} watching` : null;
  const where = watching && provider ? `${watching} on ${provider}` : watching ?? (provider ? `on ${provider}` : null);
  const parts = [entry.title || 'Untitled stream', entry.uploader, where].filter(Boolean);
  return `Live: ${parts.join(', ')}${followed ? ', a channel you follow' : ''}`;
}

/** The focused card's index and stable key in the live section, or both null. */
function focusedCard(entries: readonly LiveShelfEntry[]): { key: string | null; index: number | null } {
  const section = document.querySelector(`[data-shelf-section="${SECTION}"]`);
  if (!section?.contains(document.activeElement)) return { key: null, index: null };
  const index = [...section.querySelectorAll('.h-row > li')].findIndex((card) => card.contains(document.activeElement));
  return index < 0 ? { key: null, index: null } : { key: keyOf(entries[index]?.entry), index };
}

/**
 * E-I1: where to refocus a card after a refresh re-ranks the shelf. Cards are keyed by URL, so recovery follows the
 * same stream to its new position; only when that stream has left the list does it fall back to the old index (the
 * nearest remaining card), rather than silently landing on whatever now sits at that index.
 */
export function recoverFocusIndex(key: string | null, indexBefore: number | null, entriesNow: readonly LiveShelfEntry[]): number | null {
  if (indexBefore === null) return null;
  const byKey = key !== null ? entriesNow.findIndex((item) => keyOf(item.entry) === key) : -1;
  return byKey >= 0 ? byKey : indexBefore;
}

export function LiveShelf({ userId, onOpenRemote, onSeeAll, onStatus }: LiveShelfProps) {
  const [snapshot, setSnapshot] = useState<LiveSnapshot | null>(() => cachedShelf<LiveSnapshot>(userId, SECTION) ?? null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  /** The focused card's key and index when a refresh was applied; the layout effect below recovers focus if it moved or went. */
  const focusedBefore = useRef<{ key: string | null; index: number | null }>({ key: null, index: null });

  usePolledSnapshot(`home-live:${userId}:${attempt}`, MOUNTED_SESSION, (isCurrent) => getLiveDiscovery().then((next) => {
    if (!isCurrent()) return;
    focusedBefore.current = focusedCard(liveShelfEntries(snapshot));
    rememberShelf(userId, SECTION, next);
    setSnapshot(next);
    setFailed(false);
  }, () => {
    // A failed refresh (a 429 from the shared popular limit included) keeps any data; the next tick retries.
    if (isCurrent()) setFailed(true);
  }), LIVE_POLL_MS);

  const { providers } = useStreamingProviders();
  const entries = liveShelfEntries(snapshot).filter(({ entry }) => isProviderVisible(providers, entry.source));
  const status: ShelfStatus = entries.length ? 'items'
    : (failed && !snapshot) || (snapshot?.state === 'failed') ? 'failed'
      : !snapshot || snapshot.state === 'loading' ? 'loading' : 'empty';
  useEffect(() => { onStatus(status); }, [status]); // eslint-disable-line react-hooks/exhaustive-deps -- report changes only

  useLayoutEffect(() => {
    const { key, index } = focusedBefore.current;
    focusedBefore.current = { key: null, index: null };
    const section = document.querySelector(`[data-shelf-section="${SECTION}"]`);
    if (index === null || !section || section.contains(document.activeElement)) return;
    const target = recoverFocusIndex(key, index, entries);
    const cardsNow = [...section.querySelectorAll('.h-row > li')];
    const node = (target !== null ? cardsNow[target] : undefined) ?? cardsNow.at(-1);
    (node?.querySelector<HTMLElement>('[data-focus-item]') ?? section.querySelector<HTMLElement>('h2'))?.focus();
  }, [snapshot]); // eslint-disable-line react-hooks/exhaustive-deps -- entries is derived from snapshot each render

  if (status === 'empty') return null;
  const stale = entries.length > 0 && Boolean(snapshot && (snapshot.stale || snapshot.state === 'stale' || snapshot.state === 'partial'));
  const lines = [...liveNotices(snapshot), ...(stale ? ['Showing the last-known live streams.'] : [])];
  return (
    <HomeShelf
      failedText="Live streams are unavailable right now."
      heading="Live now"
      itemKey={({ entry }) => keyOf(entry)}
      items={entries}
      notes={lines.length ? <div className="h-notes">{lines.map((line) => <p className="h-note" key={line}>{line}</p>)}</div> : null}
      onRetry={() => { setFailed(false); setAttempt((value) => value + 1); }}
      renderCard={(live, slot) => (
        <StillFrame
          {...slot}
          art={live.entry.artwork_url ? { url: live.entry.artwork_url, widths: [] } : null}
          badge={<LiveBadge className="is-corner" state="live" surface="art" />}
          colour={{ colour: fallbackColour(keyOf(live.entry)), fromPalette: true }}
          data-focus-item
          kicker={providerOf(live.entry)?.toUpperCase() ?? null}
          label={liveLabel(live)}
          line={liveLine(live)}
          menu={<ArtMenu subject={{ kind: 'remote', item: live.entry }} />}
          name={live.entry.title || 'Untitled stream'}
          onActivate={() => onOpenRemote(live.entry)}
          shape="still"
        />
      )}
      sectionKey={SECTION}
      seeAll={{ label: 'See all', onClick: onSeeAll }}
      shape="still"
      state={status === 'items' ? 'ready' : status === 'failed' ? 'failed' : 'loading'}
    />
  );
}
