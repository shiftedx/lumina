/**
 * Where a YouTube channel is mentioned (a byline, a live hero, a channel tile), what the mention already knows, so the
 * channel page can draw its header before the response.
 */
import type { MouseEvent } from 'react';

import type { ChannelPageTab } from '../../app/routes';

export type ChannelMention = { id: string; name: string; avatarUrl?: string | null };

export const YOUTUBE_CHANNEL_ID = /^UC[0-9A-Za-z_-]{22}$/;
const CHANNEL_PATH = /^\/channel\/(UC[0-9A-Za-z_-]{22})(?:\/|$)/;
const MAX_REMEMBERED = 64;
const remembered = new Map<string, ChannelMention>();

const isYouTubeHost = (host: string) => host === 'youtube.com' || host.endsWith('.youtube.com');

/** The UC… id of an entry or preview: uploader_id or channel_id when it matches, else parsed from a /channel/UC… URL. */
export function youtubeChannelId(value: { uploader_id?: string | null; channel_id?: string | null; uploader_url?: string | null; channel_url?: string | null }): string | null {
  for (const candidate of [value.channel_id, value.uploader_id]) if (candidate && YOUTUBE_CHANNEL_ID.test(candidate)) return candidate;
  for (const address of [value.channel_url, value.uploader_url]) {
    if (!address) continue;
    try {
      const parsed = new URL(address);
      const match = isYouTubeHost(parsed.hostname.toLowerCase()) ? CHANNEL_PATH.exec(parsed.pathname) : null;
      if (match) return match[1];
    } catch {
      // Not an address: try the next one.
    }
  }
  return null;
}

export function rememberChannel(mention: ChannelMention): void {
  if (!YOUTUBE_CHANNEL_ID.test(mention.id)) return;
  remembered.delete(mention.id);
  remembered.set(mention.id, mention);
  if (remembered.size > MAX_REMEMBERED) remembered.delete(remembered.keys().next().value as string);
}

export const recallChannel = (id: string): ChannelMention | null => remembered.get(id) ?? null;

export const channelPagePath = (id: string, tab?: ChannelPageTab): string => `/channel/youtube/${id}${tab && tab !== 'videos' ? `?tab=${tab}` : ''}`;

/**
 * An in-app <a href> click (a byline, a channel tile, "Recordings →"): push the href and let LuminaApp's popstate
 * handler open the route, so no surface needs a navigation prop. Modified and middle clicks stay the browser's.
 */
export function followAppLink(event: MouseEvent<HTMLAnchorElement>): void {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const href = event.currentTarget.getAttribute('href');
  if (!href || !href.startsWith('/')) return;
  event.preventDefault();
  openAppPath(href);
}

/** Open an in-app path as if the member followed a link (`replace`: a redirect, no new history entry). */
export function openAppPath(path: string, replace = false): void {
  if (replace) window.history.replaceState(null, '', path);
  else window.history.pushState(null, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}
