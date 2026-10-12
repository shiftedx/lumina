/**
 * Subscriptions: masthead and lede, Live from your follows (the live snapshot's followed
 * hero), the provider chips and Find a channel, avatar tiles (YouTube → its channel page, Twitch and Kick → their
 * follow detail), the channels needing attention and the Latest wall. /subscriptions/{id} for a YouTube follow
 * redirects to its channel page.
 */
import { useCanDownload } from '../access/access';
import { CircleAlert, CircleUserRound, RefreshCw, Search } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { resolveChannel } from '../../api';
import { type ChannelSubscriptionCommands, channelSourceIdentity, followProvider } from '../../channelSubscriptions';
import { SOURCE_LABELS } from '../../luminaModel';
import { SUBSCRIPTION_FEED_LIMIT, type SubscriptionChannelOutcome, subscriptionItems } from '../../subscriptionFeed';
import type { LibraryItem, LiveSnapshot, RemoteEntry, SearchSource, SourceAutomation, YouTubeSearchResult } from '../../types';
import { ChannelAvatar } from '../gallery/ChannelAvatar';
import { Masthead } from '../../ui';
import { LiveBadge } from '../gallery/LiveBadge';
import { RemoteStillCard } from '../gallery/RemoteStillCard';
import { StillRail } from '../gallery/StillRail';
import { moveFocus } from '../media/focusNav';
import { channelPagePath, followAppLink, openAppPath, rememberChannel, youtubeChannelId } from './channelMention';
import { FollowSettingsDialog } from './FollowSettingsDialog';
import './channels.css';

const WALL_SIZES = '(max-width: 599px) 100vw, 320px';

/** A follow's UC… id when its address carries it (a /channel/ address); null for handles (resolved on demand). */
export const followChannelId = (channel: SourceAutomation): string | null => (followProvider(channel.source_url) === 'youtube' ? youtubeChannelId({ channel_url: channel.source_url }) : null);

/** Whether a follow is live now per the snapshot's followed hero: same address, same channel id, or same name. */
export function followIsLive(channel: SourceAutomation, hero: readonly RemoteEntry[]): boolean {
  const identity = channelSourceIdentity(channel.source_url);
  const id = followChannelId(channel);
  const name = channel.label.trim().toLowerCase();
  return hero.some((entry) => entry.capabilities?.lifecycle === 'live' && (
    (entry.uploader_url && channelSourceIdentity(entry.uploader_url) === identity)
    || (id !== null && youtubeChannelId(entry) === id)
    || (entry.uploader ?? '').trim().toLowerCase() === name
  ));
}

/** "5 min ago" for a server timestamp; naive timestamps are UTC. */
export function checkedAgo(value: string | null | undefined, now = Date.now()): string | null {
  if (!value) return null;
  const time = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`).getTime();
  if (Number.isNaN(time)) return null;
  const seconds = Math.round((time - now) / 1000);
  const format = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  for (const [unit, size] of [['day', 86400], ['hour', 3600], ['minute', 60]] as const) {
    if (Math.abs(seconds) >= size) return format.format(Math.round(seconds / size), unit);
  }
  return 'just now';
}

function statusCopy(outcome: SubscriptionChannelOutcome): { text: string; problem: boolean } {
  const { channel, status } = outcome;
  if (status === 'authentication-required') return { text: 'Members-only content', problem: true };
  if (status === 'failed') return { text: 'Last check failed', problem: true };
  if (status === 'loading') return { text: 'Waiting for its first check', problem: false };
  const ago = checkedAgo(channel.last_checked_at);
  const checked = ago ? `Checked ${ago}` : 'Not checked yet';
  return { text: channel.active ? checked : `Paused · ${checked.toLowerCase()}`, problem: false };
}

type ChannelsSurfaceProps = {
  channels: SourceAutomation[];
  channelId: string | null;
  outcomes: SubscriptionChannelOutcome[];
  refreshing: boolean;
  onOpen: (item: YouTubeSearchResult) => void;
  onOpenChannel: (channelId: string | null) => void;
  onQueue: (item: YouTubeSearchResult) => void;
  isQueueing: (item: YouTubeSearchResult) => boolean;
  onExplore: () => void;
  onRetry: () => void;
  library: LibraryItem[];
  commands: ChannelSubscriptionCommands;
  onAutomationChange: (automation: SourceAutomation) => void;
  onAutomationRemoved: (automationId: string) => void;
  onRecover: (automation: SourceAutomation) => Promise<void>;
  /** The live snapshot, polled at 30 s while Subscriptions is visible (LuminaApp); its hero is the single source of who is live. */
  live?: LiveSnapshot | null;
  /** Inside the Streaming page: a level-2 masthead, and the page's provider starts as the filter. */
  embedded?: boolean;
  provider?: SearchSource;
};

function SaveAction({ item, busy, onQueue }: { item: RemoteEntry; busy: boolean; onQueue: (item: RemoteEntry) => void }) {
  const canDownload = useCanDownload();
  if (item.capabilities?.can_acquire === false || !canDownload) return null;
  return <button className="g-text-button g-button-text g-remote-action" data-focus-item disabled={Boolean(item.saved_item_id) || busy} onClick={() => onQueue(item)} type="button">{item.saved_item_id ? 'Saved' : busy ? 'Saving…' : 'Save'}</button>;
}

function LatestWall({ items, props }: { items: RemoteEntry[]; props: ChannelsSurfaceProps }) {
  return (
    <div className="g-channels-wall">
      {items.map((item, index) => <RemoteStillCard actions={<SaveAction busy={props.isQueueing(item)} item={item} onQueue={props.onQueue} />} item={item} key={item.webpage_url || item.id || index} onOpen={props.onOpen} position={index} priority={index < 8 ? 2 : 3} sizes={WALL_SIZES} />)}
    </div>
  );
}

function ChannelTile({ outcome, live }: { outcome: SubscriptionChannelOutcome; live: boolean }) {
  const { channel } = outcome;
  const status = statusCopy(outcome);
  const provider = followProvider(channel.source_url);
  const id = followChannelId(channel);
  const href = id ? channelPagePath(id) : `/subscriptions/${channel.id}`;
  const label = [provider ? SOURCE_LABELS[provider] : null, status.problem ? 'Last check failed' : status.text, channel.auto_download ? 'Auto-save on' : null].filter(Boolean).join(' · ');
  const name = [channel.label, live ? 'live' : null, label].filter(Boolean).join(', ');
  return (
    <a aria-label={name} className={`g-channel-tile${status.problem ? ' is-problem' : ''}`} data-focus-item href={href} onClick={(event) => { if (id) rememberChannel({ id, name: channel.label, avatarUrl: outcome.channelArtworkUrl }); followAppLink(event); }}>
      <ChannelAvatar live={live} name={channel.label} size={96} url={outcome.channelArtworkUrl} />
      <span className="g-channel-tile-name">{channel.label}{live ? <LiveBadge state="live" surface="paper" /> : null}</span>
      <span className="g-label">{status.problem ? <CircleAlert aria-hidden="true" className="g-channel-alert" /> : null}{label}</span>
    </a>
  );
}

/** /subscriptions/{id}: a YouTube follow becomes its channel page (history replaced); Twitch and Kick keep this detail. */
function FollowDetail({ outcome, props }: { outcome: SubscriptionChannelOutcome; props: ChannelsSurfaceProps }) {
  const { channel } = outcome;
  const [settings, setSettings] = useState(false);
  const [redirecting, setRedirecting] = useState(followProvider(channel.source_url) === 'youtube');
  useEffect(() => {
    if (followProvider(channel.source_url) !== 'youtube') return undefined;
    let active = true;
    const id = followChannelId(channel);
    const go = (channelId: string) => { rememberChannel({ id: channelId, name: channel.label, avatarUrl: outcome.channelArtworkUrl }); openAppPath(channelPagePath(channelId), true); };
    if (id) go(id);
    else resolveChannel(channel.source_url).then(({ channel_id: resolved }) => { if (active) go(resolved); }, () => { if (active) setRedirecting(false); });
    return () => { active = false; };
  }, [channel.id]);
  if (redirecting) return <div aria-busy="true" aria-label={`Opening ${channel.label}`} className="surface gallery g-channels" role="status" />;
  const live = followIsLive(channel, props.live?.hero ?? []);
  const provider = followProvider(channel.source_url);
  const status = statusCopy(outcome);
  return (
    <div className="surface gallery g-channels g-follow-detail" onKeyDown={moveFocus}>
      <header className="g-channel-header">
        <ChannelAvatar live={live} name={channel.label} size={120} url={outcome.channelArtworkUrl} />
        <div className="g-channel-header-copy">
          <h1 id="channel-detail-title" tabIndex={-1}>{channel.label}</h1>
          <p className="g-label">{[provider ? SOURCE_LABELS[provider] : null, status.text].filter(Boolean).join(' · ')}</p>
          <div className="g-channel-actions" data-focus-row>
            <button className="g-button" data-focus-item onClick={() => setSettings(true)} type="button">Follow settings</button>
            <a className="g-button" data-focus-item href={channel.source_url} rel="noopener noreferrer" target="_blank">Open on {provider ? SOURCE_LABELS[provider] : 'the original site'}</a>
          </div>
        </div>
      </header>
      {live ? <StillRail eager heading="Live now" items={(props.live?.hero ?? []).filter((entry) => followIsLive(channel, [entry]))} onOpen={props.onOpen} railKey="follow-live" /> : null}
      <section aria-labelledby="g-follow-latest" className="g-channels-section">
        <h2 id="g-follow-latest">Latest</h2>
        {outcome.items.length ? <LatestWall items={outcome.items} props={props} /> : <p className="g-label">{status.problem ? 'No videos known yet.' : 'No recent videos.'}</p>}
      </section>
      {settings ? <FollowSettingsDialog automation={channel} commands={props.commands} onChange={props.onAutomationChange} onClose={() => setSettings(false)} onRecover={props.onRecover} onRemoved={(id) => { props.onOpenChannel(null); props.onAutomationRemoved(id); }} /> : null}
    </div>
  );
}

export function ChannelsSurface(props: ChannelsSurfaceProps) {
  const { channels, channelId, outcomes, refreshing, live } = props;
  const [provider, setProvider] = useState<SearchSource | 'all'>(props.provider ?? 'all');
  const [query, setQuery] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  // Opening a follow detail focuses its heading; leaving it (Back, unfollow) focuses the masthead, so focus is never lost.
  const shownId = useRef(channelId);
  useEffect(() => {
    if (shownId.current === channelId) return;
    shownId.current = channelId;
    (channelId ? document.getElementById('channel-detail-title') : heading.current)?.focus();
  }, [channelId]);
  const detail = channelId ? outcomes.find((outcome) => outcome.channel.id === channelId) : null;
  if (channelId && detail) return <FollowDetail outcome={detail} props={props} />;
  if (channelId) {
    return (
      <div className="surface gallery g-channels">
        <h1 id="channel-detail-title" tabIndex={-1}>You don’t follow this channel</h1>
        <p>It may have been unfollowed. Videos you already saved stay in your library.</p>
        <button className="g-button" onClick={() => props.onOpenChannel(null)} type="button">Show all channels</button>
      </div>
    );
  }
  const hero = live?.hero ?? [];
  const liveHero = hero.filter((entry) => entry.capabilities?.lifecycle === 'live');
  const failures = outcomes.filter((outcome) => outcome.status === 'authentication-required' || outcome.status === 'failed');
  const loading = refreshing || outcomes.some((outcome) => outcome.status === 'loading');
  const providers = [...new Set(channels.map((channel) => followProvider(channel.source_url)).filter((value): value is SearchSource => Boolean(value)))];
  const needle = query.trim().toLowerCase();
  const shown = outcomes.filter((outcome) => (provider === 'all' || followProvider(outcome.channel.source_url) === provider) && (!needle || outcome.channel.label.toLowerCase().includes(needle)));
  const videos = subscriptionItems(shown);
  const feedTruncated = videos.length === SUBSCRIPTION_FEED_LIMIT && shown.reduce((count, outcome) => count + outcome.items.length, 0) > videos.length;
  const newest = channels.map((channel) => channel.last_checked_at).filter(Boolean).sort().at(-1);
  const liveCount = channels.filter((channel) => followIsLive(channel, liveHero)).length;
  const kicker = [`${channels.length} channel${channels.length === 1 ? '' : 's'}`, `${liveCount} live now`, newest ? `checked ${checkedAgo(newest)}` : null].filter(Boolean).join(' · ');
  const refresh = channels.length ? <button className="g-button" data-focus-item onClick={props.onRetry} type="button"><RefreshCw aria-hidden="true" className={loading ? 'spin' : ''} />{loading ? 'Restart channel check' : 'Refresh channels'}</button> : null;
  return (
    <div aria-busy={loading} className={props.embedded ? 'gallery g-channels' : 'surface gallery g-channels'} onKeyDown={moveFocus}>
      <p aria-atomic="true" aria-live="polite" className="sr-only" role="status">{loading ? `Checking ${channels.length} followed channels.` : `Subscription update complete. ${failures.length ? `${failures.length} need attention.` : 'All channels are available.'}`}</p>
      {props.embedded ? (
        <Masthead actions={refresh} headingId="subscriptions-title" headingRef={heading} kicker={channels.length ? kicker : undefined} level={2} title="Your channels" />
      ) : (
        <header className="g-masthead">
          <h1 id="subscriptions-title" ref={heading} tabIndex={-1}>Subscriptions</h1>
          {channels.length ? <p className="g-label g-kicker">{kicker}</p> : null}
          {refresh}
        </header>
      )}
      <p className="g-channels-lede">Following here never subscribes you on the platform.</p>
      {!channels.length ? (
        <div className="g-empty-result"><p className="g-channels-empty-title">You're not following any channels yet.</p><button className="g-button" data-focus-item onClick={props.onExplore} type="button">Explore</button></div>
      ) : (
        <>
          {liveHero.length ? <StillRail eager heading="Live from your follows" items={liveHero} kicker={`${liveHero.length} live`} onOpen={props.onOpen} railKey="follows-live" showProvider={providers.length > 1} /> : null}
          <div className="g-toolbar">
            {providers.length > 1 ? <div aria-label="Filter by source" className="g-chips" role="group">{(['all', ...providers] as const).map((value) => <button aria-pressed={provider === value} className="g-chip" data-focus-item key={value} onClick={() => setProvider(value)} type="button">{value === 'all' ? 'All sources' : SOURCE_LABELS[value]}</button>)}</div> : null}
            {channels.length > 1 ? <label className="g-search"><Search aria-hidden="true" /><span className="sr-only">Find a followed channel</span><input className="g-input" onChange={(event) => setQuery(event.currentTarget.value)} placeholder="Find a channel" type="search" value={query} /></label> : null}
          </div>
          {shown.length ? <div className="g-channel-grid" data-focus-row>{shown.map((outcome) => <ChannelTile key={outcome.channel.id} live={followIsLive(outcome.channel, liveHero)} outcome={outcome} />)}</div> : <p className="g-label" role="status">No followed channels match this filter.</p>}
          {failures.length ? (
            <section aria-labelledby="subscription-recovery-title" className="g-channels-section">
              <h2 id="subscription-recovery-title">Channels needing attention</h2>
              <p className="g-label">Last-known videos stay visible. These problems clear after the next successful check.</p>
              <ul className="g-attention">{failures.map((outcome) => <li key={outcome.channel.id}><CircleUserRound aria-hidden="true" /><span className="g-attention-name">{outcome.channel.label}</span><span className="g-label">{outcome.status === 'authentication-required' ? 'This channel publishes members-only content, which Lumina cannot inspect without an account.' : outcome.message || 'This channel could not be loaded.'}</span></li>)}</ul>
              <button className="g-button" data-focus-item data-subscription-retry onClick={props.onRetry} type="button"><RefreshCw aria-hidden="true" className={refreshing ? 'spin' : ''} /> {refreshing ? 'Restart retry' : 'Retry channels'}</button>
            </section>
          ) : null}
          <section aria-labelledby="g-channels-latest" className="g-channels-section">
            <h2 id="g-channels-latest">Latest from these channels</h2>
            {feedTruncated ? <p className="g-label">Showing {SUBSCRIPTION_FEED_LIMIT} recent videos, balanced across followed channels.</p> : null}
            {loading && !videos.length ? <div aria-busy="true" aria-label="Loading followed channels" className="g-channels-wall" role="status">{Array.from({ length: 8 }, (_, index) => <span className="g-still-slot" key={index} />)}</div>
              : videos.length ? <LatestWall items={videos} props={props} />
                : !failures.length && shown.length ? <p className="g-label">Every followed channel is up to date.</p> : null}
          </section>
        </>
      )}
    </div>
  );
}
