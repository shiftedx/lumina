/**
 * What a recommendation card's menu offers and sends. Pure: no React, no network.
 * The names here are frozen; the rest (keys, ids, rail order, drop predicate) are this
 * track's helpers.
 */
import type { RecoAnnotation, RemoteEntry, SuppressionList, SuppressRecommendationInput, TitleSummary } from '../../types';
import { youtubeChannelId } from '../channels/channelMention';
import { remoteQueueRef } from '../watch/WatchQueue';

export type RecoTarget = { kind: 'remote'; item: RemoteEntry } | { kind: 'title'; title: TitleSummary };
export type RecoFeedback = 'not_interested' | 'fewer' | 'hide_channel';
export type RecoMenuItem = { id: 'why' | 'queue' | RecoFeedback; label: string };

export const remoteTarget = (item: RemoteEntry): RecoTarget => ({ kind: 'remote', item });
export const titleTarget = (title: TitleSummary): RecoTarget => ({ kind: 'title', title });

/** Names in menu labels and undo lines are cut at 32 characters, as the server cuts reasons. */
const NAME_MAX = 32;
const clip = (text: string, max: number): string => (text.length > max ? `${text.slice(0, max - 1)}…` : text);
/** The suppression endpoint's limits: a longer value is a 422 that would lose the member's choice. */
const cut = (value: string | null | undefined, max: number): string | null => (value ? value.slice(0, max) : null);
/** A cut address is a different address, so an over-long one is left out instead. */
const address = (value: string | null | undefined): string | null => (value && value.length <= 2_048 ? value : null);

/** What the card is called in the hidden set: the address, else the id; a title's id. */
export const targetKey = (target: RecoTarget): string => (target.kind === 'title' ? target.title.id : target.item.webpage_url || target.item.id || target.item.title || '');

const channelName = (target: RecoTarget): string | null => (target.kind === 'remote' ? target.item.uploader?.trim() || null : null);
const knowsChannel = (target: RecoTarget): boolean => target.kind === 'remote' && Boolean(channelName(target) || youtubeChannelId(target.item));
const channelLabel = (target: RecoTarget): string => {
  const name = channelName(target);
  return name ? clip(name, NAME_MAX) : 'this channel';
};

/** The id the reason line carries, so a card's `aria-describedby` can point at it. Unique per list position. */
export const reasonIdFor = (reco: RecoAnnotation): string => `reco-${reco.list_id}-${reco.position}`;

/**
 * The menu. "Why this?" is listed only where the caption (and so the reason line) is hidden: otherwise the reason is on
 * screen and the item would only repeat it. A remote card with an address to queue offers "Add to queue" (1.5.1 parity,
 * "Play next" is dropped). A remote entry with no channel name and no channel id has no channel to show
 * fewer of or to hide. `personal` false (the kill switch is off, so the server sent no annotation) omits "Show fewer",
 * which the legacy policy ignores.
 */
export function recoMenuItems(target: RecoTarget, captionHidden: boolean, personal = true): RecoMenuItem[] {
  const items: RecoMenuItem[] = captionHidden ? [{ id: 'why', label: 'Why this?' }] : [];
  if (target.kind === 'remote' && remoteQueueRef(target.item)) items.push({ id: 'queue', label: 'Add to queue' });
  items.push({ id: 'not_interested', label: 'Not interested' });
  if (knowsChannel(target)) {
    const channel = channelLabel(target);
    if (personal) items.push({ id: 'fewer', label: `Show fewer from ${channel}` });
    items.push({ id: 'hide_channel', label: `Don't recommend ${channel}` });
  }
  return items;
}

/** The one line that replaces the card for 8 s; the Undo button follows it. */
export const undoMessage = (feedback: RecoFeedback, target: RecoTarget): string => (feedback === 'fewer' ? `Showing fewer from ${channelLabel(target)}.` : 'Hidden.');

/** The body of POST /api/discovery/suppressions: the stable channel, the title, and the list it was used on. */
export function suppressionInput(feedback: RecoFeedback, target: RecoTarget, reco: RecoAnnotation | null): SuppressRecommendationInput {
  const origin = { list_id: reco?.list_id ?? null, key: reco?.key ?? null };
  if (target.kind === 'title') return { scope: 'title', title_id: target.title.id, title: cut(target.title.name, 512), ...origin };
  const { item } = target;
  const channel = { source: item.source ?? 'youtube', uploader: cut(item.uploader?.trim(), 512), channel_id: youtubeChannelId(item), channel_url: address(item.uploader_url) };
  if (feedback === 'not_interested') {
    return { scope: 'item', ...channel, source_id: cut(item.id, 512), source_url: address(item.webpage_url), title: cut(item.title, 512), ...origin };
  }
  return { scope: feedback === 'fewer' ? 'fewer' : 'channel', ...channel, ...origin };
}

/**
 * Which of the Up next candidates leave the rail when an Undo window closes: the item itself, or for Don't recommend
 * every item of that channel (the server vetoes it everywhere; this only makes the removal immediate).
 */
export function relatedDropPredicate(feedback: RecoFeedback, target: RecoTarget): (candidate: RemoteEntry) => boolean {
  if (target.kind !== 'remote') return () => false;
  if (feedback === 'hide_channel') {
    const channel = channelName(target)?.toLowerCase() ?? '';
    return (candidate) => Boolean(channel) && (candidate.uploader?.trim().toLowerCase() || '') === channel;
  }
  const key = targetKey(target);
  return (candidate) => Boolean(key) && targetKey(remoteTarget(candidate)) === key;
}

/**
 * Whether the member had hidden or shown fewer of this channel before following it. Following removes those rows on the
 * server and the response says nothing, so the client decides from the list it already holds and shows
 * "We'll recommend {channel} again." Matches by channel name, as the list does.
 */
export function followHadSuppression(list: Pick<SuppressionList, 'channels'> & Partial<SuppressionList>, channel: string): boolean {
  const name = channel.trim().toLowerCase();
  return Boolean(name) && [...list.channels, ...(list.fewer ?? [])].some((entry) => entry.channel_name?.trim().toLowerCase() === name);
}

/**
 * Explore's category rails in the member's order (`category_order`). Rail keys are `popular-{category}`; a
 * category the order does not name keeps its place after those it does.
 */
export function orderRails<T extends { key: string }>(rails: readonly T[], order: readonly string[] | null | undefined): T[] {
  if (!order?.length) return [...rails];
  const rank = new Map(order.map((key, index) => [`popular-${key}`, index]));
  return rails
    .map((rail, index) => ({ rail, index }))
    .sort((a, b) => (rank.get(a.rail.key) ?? order.length) - (rank.get(b.rail.key) ?? order.length) || a.index - b.index)
    .map(({ rail }) => rail);
}
