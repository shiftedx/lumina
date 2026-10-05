import type { ChannelCandidate, SearchSource, SourceAutomation } from './types';

// Mirror of the backend channel-identity normalization so a pasted address, a
// category suggestion, and an existing follow share one identity everywhere.
const YOUTUBE_HOSTS = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com']);
const CANONICAL_YOUTUBE_HOST = 'www.youtube.com';
const YOUTUBE_CHANNEL_PREFIXES = new Set(['channel', 'c', 'user']);
// Mirror of backend kick_public: any public Kick link names one lowercase channel slug.
const KICK_HOSTS = new Set(['kick.com', 'www.kick.com']);
const KICK_RESERVED = new Set(['video', 'categories', 'search', 'auth']);

export function isKickAddress(url: string): boolean {
  try {
    return KICK_HOSTS.has(new URL(url).hostname.toLowerCase());
  } catch {
    return false;
  }
}

/** The provider a follow's address belongs to; null for anything unrecognized. */
export function followProvider(url: string): SearchSource | null {
  let host: string;
  try {
    host = new URL(url).hostname.toLowerCase();
  } catch {
    return null;
  }
  const matches = (domain: string) => host === domain || host.endsWith(`.${domain}`);
  if (matches('youtube.com') || host === 'youtu.be') return 'youtube';
  if (matches('twitch.tv')) return 'twitch';
  if (matches('kick.com')) return 'kick';
  if (matches('soundcloud.com')) return 'soundcloud';
  return null;
}

export function normalizeChannelAddress(value: string): string | null {
  let raw = value.trim();
  if (!raw) return null;
  if (!raw.includes('://')) raw = `https://${raw}`;
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return null;
  }
  let host = parsed.hostname.toLowerCase();
  if (!host || !host.includes('.')) return null;
  if (KICK_HOSTS.has(host)) {
    const slug = parsed.pathname.split('/').filter(Boolean)[0]?.toLowerCase();
    if (parsed.port || parsed.username || !slug || !/^[a-z0-9_-]{1,64}$/.test(slug) || KICK_RESERVED.has(slug)) return null;
    return `https://kick.com/${slug}`;
  }
  let path = parsed.pathname.replace(/\/+$/, '');
  let search = parsed.search;
  if (YOUTUBE_HOSTS.has(host)) {
    host = CANONICAL_YOUTUBE_HOST;
    // Handles are case-insensitive and channel tabs name the same creator.
    const segments = path.split('/').filter(Boolean);
    if (segments[0]?.startsWith('@') && segments[0].length > 1) {
      path = `/${segments[0].toLowerCase()}`;
      search = '';
    } else if (segments.length >= 2 && YOUTUBE_CHANNEL_PREFIXES.has(segments[0].toLowerCase())) {
      path = `/${segments[0].toLowerCase()}/${segments[1]}`;
      search = '';
    }
  }
  const port = parsed.port ? `:${parsed.port}` : '';
  return `https://${host}${port}${path}${search}`;
}

function isSupportedChannelAddress(url: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  const segments = parsed.pathname.split('/').filter(Boolean);
  if (parsed.hostname.toLowerCase() === CANONICAL_YOUTUBE_HOST) {
    if (!segments.length) return false;
    if (segments[0].startsWith('@') && segments[0].length > 1) return true;
    return segments.length >= 2 && YOUTUBE_CHANNEL_PREFIXES.has(segments[0].toLowerCase());
  }
  return segments.length > 0;
}

function displayNameFromAddress(url: string): string {
  const parsed = new URL(url);
  const segments = parsed.pathname.split('/').filter(Boolean);
  if (!segments.length) return parsed.hostname;
  const last = segments[segments.length - 1];
  return last.startsWith('@') && last.length > 1 ? last.slice(1) : last;
}

export function resolveChannelAddressCandidate(value: string, followedIdentities: Set<string> = new Set()): ChannelCandidate | null {
  const identity = normalizeChannelAddress(value);
  if (!identity || !isSupportedChannelAddress(identity)) return null;
  return {
    channel_key: identity,
    source_url: identity,
    display_name: displayNameFromAddress(identity),
    ...(isKickAddress(identity) ? { source: 'kick', source_label: 'Kick' } : { source: 'youtube', source_label: 'YouTube' }),
    artwork_url: null,
    category_keys: [],
    following: followedIdentities.has(identity),
  };
}

export interface ChannelSubscriptionAdapter {
  pause: (automationId: string) => Promise<SourceAutomation>;
  resume: (automationId: string) => Promise<SourceAutomation>;
  setAutomaticAcquisition: (automationId: string, enabled: boolean) => Promise<SourceAutomation>;
  remove: (automationId: string) => Promise<void>;
}

export type UnfollowConfirmation = Readonly<{
  automationId: string;
  sourceIdentity: string;
}>;

export function channelSourceIdentity(sourceUrl: string): string {
  const value = sourceUrl.trim();
  try {
    const parsed = new URL(value);
    parsed.hash = '';
    parsed.pathname = parsed.pathname.replace(/\/+$/, '') || '/';
    return parsed.toString().replace(/\/$/, '');
  } catch {
    return value.replace(/\/+$/, '');
  }
}

export function findChannelBySource(channels: SourceAutomation[], sourceUrl: string): SourceAutomation | undefined {
  const identity = channelSourceIdentity(sourceUrl);
  return channels.find((channel) => channelSourceIdentity(channel.source_url) === identity);
}

export type ChannelSubscriptionStatus = {
  state: 'following' | 'paused' | 'attention';
  label: string;
  error: string | null;
  recovery: 'retry' | null;
  summary: { discovered: number; queued: number; failed: number };
};

function summaryCount(summary: Record<string, unknown>, key: string): number {
  const value = summary[key];
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0;
}

export function channelSubscriptionStatus(automation: SourceAutomation): ChannelSubscriptionStatus {
  const summary = {
    discovered: summaryCount(automation.last_run_summary || {}, 'discovered'),
    queued: summaryCount(automation.last_run_summary || {}, 'queued'),
    failed: summaryCount(automation.last_run_summary || {}, 'failed'),
  };
  const summaryError = automation.last_run_summary?.error;
  const error = automation.last_error || (typeof summaryError === 'string' ? summaryError : null);
  if (error) {
    return {
      state: 'attention',
      label: 'Last check failed',
      error,
      recovery: 'retry',
      summary,
    };
  }
  return {
    state: automation.active ? 'following' : 'paused',
    label: automation.active ? 'Following' : 'Paused',
    error: null,
    recovery: null,
    summary,
  };
}

export function createChannelSubscriptionCommands(adapter: ChannelSubscriptionAdapter) {
  return {
    pause(automation: SourceAutomation) {
      return adapter.pause(automation.id);
    },
    resume(automation: SourceAutomation) {
      return adapter.resume(automation.id);
    },
    setAutomaticAcquisition(automation: SourceAutomation, enabled: boolean) {
      return adapter.setAutomaticAcquisition(automation.id, enabled);
    },
    prepareUnfollow(automation: SourceAutomation): UnfollowConfirmation {
      return {
        automationId: automation.id,
        sourceIdentity: channelSourceIdentity(automation.source_url),
      };
    },
    async confirmUnfollow(automation: SourceAutomation, confirmation: UnfollowConfirmation): Promise<void> {
      if (
        automation.id !== confirmation.automationId
        || channelSourceIdentity(automation.source_url) !== confirmation.sourceIdentity
      ) {
        throw new Error('The channel no longer matches this unfollow confirmation. Review it and try again.');
      }
      await adapter.remove(automation.id);
    },
  };
}

export function createSessionGuardedChannelSubscriptionCommands(
  adapter: ChannelSubscriptionAdapter,
  session: { capture: () => number; isCurrent: (token: number) => boolean },
) {
  async function guarded<T>(operation: () => Promise<T>): Promise<T> {
    const token = session.capture();
    const result = await operation();
    if (!session.isCurrent(token)) throw new DOMException('Request cancelled.', 'AbortError');
    return result;
  }
  return createChannelSubscriptionCommands({
    pause: (id) => guarded(() => adapter.pause(id)),
    resume: (id) => guarded(() => adapter.resume(id)),
    setAutomaticAcquisition: (id, enabled) => guarded(() => adapter.setAutomaticAcquisition(id, enabled)),
    remove: (id) => guarded(() => adapter.remove(id)),
  });
}

export type ChannelSubscriptionCommands = ReturnType<typeof createChannelSubscriptionCommands>;
