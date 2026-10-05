import { useEffect, useMemo, useRef, useState } from 'react';

import { ApiRequestError, refreshFollows } from './api';
import { SOURCE_LABELS } from './luminaModel';
import type { PreviewEntry, SearchSource, SourceAutomation, YouTubeSearchResult } from './types';

export const SUBSCRIPTION_FEED_LIMIT = 60;
// A refresh that never reports back (lost event stream) stops spinning after this.
const REFRESH_SETTLE_TIMEOUT_MS = 120_000;

export type SubscriptionChannelStatus = 'loading' | 'ready' | 'empty' | 'authentication-required' | 'failed';

export type SubscriptionChannelOutcome = {
  channel: SourceAutomation;
  status: SubscriptionChannelStatus;
  items: YouTubeSearchResult[];
  channelArtworkUrl: string | null;
  message: string | null;
};

interface SubscriptionAcquisitionFeedOptions {
  channels: SourceAutomation[];
  enabled: boolean;
  refreshGeneration: number;
  sessionIdentity: string | null;
  captureSessionToken: () => number;
  isSessionTokenCurrent: (token: number) => boolean;
  onFailure: (error: unknown) => void;
  onSessionExpired: () => void;
  onSettled: (outcomes: SubscriptionChannelOutcome[]) => void;
}

function authenticationRequired(message: string): boolean {
  return /authenticat|sign[ -]?in|log[ -]?in|cookies?|members?[ -]?only|private (?:video|feed|source)|video is private/i.test(message);
}

function itemFromEntry(entry: PreviewEntry, channel: SourceAutomation): YouTubeSearchResult {
  const provider = entry.capabilities?.provider;
  const source: SearchSource = provider && provider in SOURCE_LABELS ? provider as SearchSource : 'youtube';
  return {
    id: entry.id,
    title: entry.title,
    uploader: entry.uploader,
    duration: entry.duration,
    thumbnail: entry.thumbnail,
    artwork_url: entry.artwork_url,
    channel_artwork_url: channel.artwork_url,
    webpage_url: entry.webpage_url,
    availability: entry.availability,
    capabilities: entry.capabilities,
    view_count: entry.view_count,
    published_at: entry.published_at,
    uploader_id: entry.channel_id,
    uploader_url: entry.channel_url,
    saved_item_id: entry.saved_item_id,
    progress: entry.progress,
    source,
    source_label: SOURCE_LABELS[source],
  };
}

/** A follow's last-known feed state as the server's single refresh path recorded it. */
export function followOutcome(channel: SourceAutomation): SubscriptionChannelOutcome {
  // Failed checks keep the last-known entries, so available videos stay visible.
  const items = (channel.feed_entries || []).map((entry) => itemFromEntry(entry, channel));
  const error = channel.last_error || null;
  const status: SubscriptionChannelStatus = error
    ? authenticationRequired(error) ? 'authentication-required' : 'failed'
    : !channel.last_checked_at && channel.active ? 'loading'
      : items.length ? 'ready' : 'empty';
  return { channel, status, items, channelArtworkUrl: channel.artwork_url || null, message: error };
}

/**
 * The mixed-source Subscriptions feed. Provider work happens once per follow on
 * the server (bounded, coalesced across viewers); this hook only derives the feed
 * from follow state and asks the server to refresh when the member requests it.
 */
export function useSubscriptionAcquisitionFeed(options: SubscriptionAcquisitionFeedOptions) {
  const outcomes = useMemo(() => options.channels.map(followOutcome), [options.channels]);
  // Follow id -> last check time when a refresh was requested; settled once each moved on.
  const [pending, setPending] = useState<Map<string, string | null> | null>(null);
  const callbacks = useRef(options);
  callbacks.current = options;

  useEffect(() => {
    if (!options.refreshGeneration || !options.enabled || !options.sessionIdentity) return undefined;
    let cancelled = false;
    const token = callbacks.current.captureSessionToken();
    refreshFollows().then((follows) => {
      if (cancelled || !callbacks.current.isSessionTokenCurrent(token)) return;
      setPending(new Map(follows.map((follow) => [follow.id, follow.last_checked_at || null])));
    }).catch((failure) => {
      if (cancelled || !callbacks.current.isSessionTokenCurrent(token)) return;
      if (failure instanceof ApiRequestError && failure.status === 401) callbacks.current.onSessionExpired();
      else callbacks.current.onFailure(failure);
    });
    return () => { cancelled = true; };
  }, [options.enabled, options.refreshGeneration, options.sessionIdentity]);

  const refreshing = pending !== null && options.channels.some(
    (channel) => pending.has(channel.id) && (channel.last_checked_at || null) === pending.get(channel.id),
  );

  useEffect(() => {
    if (pending === null) return undefined;
    if (!refreshing) {
      setPending(null);
      callbacks.current.onSettled(outcomes);
      return undefined;
    }
    const timeout = window.setTimeout(() => setPending(null), REFRESH_SETTLE_TIMEOUT_MS);
    return () => window.clearTimeout(timeout);
  }, [outcomes, pending, refreshing]);

  return {
    outcomes,
    refreshing,
    videos: useMemo(() => subscriptionItems(outcomes), [outcomes]),
  };
}

export function subscriptionItems(outcomes: SubscriptionChannelOutcome[]): YouTubeSearchResult[] {
  const seen = new Set<string>();
  const items: YouTubeSearchResult[] = [];
  const longestChannel = Math.max(0, ...outcomes.map((outcome) => outcome.items.length));
  for (let itemIndex = 0; itemIndex < longestChannel; itemIndex += 1) {
    for (const outcome of outcomes) {
      const item = outcome.items[itemIndex];
      if (!item) continue;
      const key = item.id || item.webpage_url || item.title || '';
      if (!key || seen.has(key)) continue;
      seen.add(key);
      items.push(item);
      if (items.length === SUBSCRIPTION_FEED_LIMIT) return items;
    }
  }
  return items;
}
