/**
 * A YouTube channel's page: banner, header (avatar, name, kicker, description, actions),
 * a live band, tabs from the response plus "In your library", and a 16:9 wall (9:16 for shorts) paged 60 then 120. The
 * header draws at once from the mention that opened it. Follow is optimistic; Following opens Follow settings; unfollow
 * only through their confirmation.
 */
import { useCanDownload } from '../access/access';
import { Check, ExternalLink, LoaderCircle, Plus } from 'lucide-react';
import { type KeyboardEvent, useEffect, useMemo, useState } from 'react';

import { createLiveRecording, getChannelPage, listLibraryChannels } from '../../api';
import type { ChannelPageTab } from '../../app/routes';
import { type ChannelSubscriptionCommands, findChannelBySource } from '../../channelSubscriptions';
import type { ChannelPageResponse, LibraryChannelResponse, LibraryItem, RemoteEntry, SourceAutomation } from '../../types';
import { formatCompactNumber } from '../../utils';
import { ChannelAvatar } from '../gallery/ChannelAvatar';
import { GalleryArt } from '../gallery/GalleryArt';
import { fallbackColour } from '../gallery/galleryModel';
import { LiveBadge } from '../gallery/LiveBadge';
import { RemoteStillCard } from '../gallery/RemoteStillCard';
import { remoteArt } from '../gallery/remoteModel';
import { StillCard } from '../gallery/StillCard';
import { usePagedLibrary } from '../gallery/StillWall';
import { isBackKey, moveFocus } from '../media/focusNav';
import { recallChannel } from './channelMention';
import { API_TAB, channelFailure, channelKicker, type ChannelSort, EMPTY_TAB, libraryChannelFor, PAGE_FIRST, PAGE_MAX, pageLimitNote, pageTabs, sortEntries } from './channelPageModel';
import { FollowSettingsDialog } from './FollowSettingsDialog';

export type ChannelPageProps = {
  channelId: string;
  tab: ChannelPageTab;
  onTabChange: (tab: ChannelPageTab) => void;
  follows: SourceAutomation[];
  onFollow: (channel: { name: string; url: string }) => Promise<SourceAutomation>;
  commands: ChannelSubscriptionCommands;
  onAutomationChange: (automation: SourceAutomation) => void;
  onAutomationRemoved: (id: string) => void;
  onRecover: (automation: SourceAutomation) => Promise<void>;
  onOpen: (item: RemoteEntry) => void;
  onQueue: (item: RemoteEntry) => void;
  isQueueing: (item: RemoteEntry) => boolean;
  onPlayLibrary: (item: LibraryItem) => void;
};

type Load = { key: string; data: ChannelPageResponse | null; failure: 'unavailable' | 'unreachable' | null; loading: boolean };
const WALL_SIZES = '(max-width: 599px) 100vw, (max-width: 1023px) 50vw, 320px';

export function ChannelPage(props: ChannelPageProps) {
  const { channelId, tab, onTabChange, follows, onOpen, onQueue, isQueueing, onPlayLibrary } = props;
  const mention = recallChannel(channelId);
  const apiTab = tab === 'library' ? 'videos' : API_TAB[tab];
  const [limit, setLimit] = useState<60 | 120>(PAGE_FIRST);
  const [attempt, setAttempt] = useState(0);
  const key = `${channelId}:${apiTab}:${limit}:${attempt}`;
  const [load, setLoad] = useState<Load>({ key: '', data: null, failure: null, loading: true });
  const [library, setLibrary] = useState<LibraryChannelResponse[] | null>(null);
  const [sort, setSort] = useState<ChannelSort>('latest');
  const [expanded, setExpanded] = useState(false);
  const [optimistic, setOptimistic] = useState(false);
  const [followError, setFollowError] = useState<string | null>(null);
  const [settings, setSettings] = useState(false);
  const canDownload = useCanDownload();
  const [recording, setRecording] = useState<'idle' | 'starting' | 'recording' | 'failed'>('idle');

  useEffect(() => { setLimit(PAGE_FIRST); setSort('latest'); }, [channelId, tab]);
  useEffect(() => {
    const controller = new AbortController();
    setLoad((current) => ({ ...current, key, loading: true, failure: null }));
    getChannelPage(channelId, { tab: apiTab, limit }, { signal: controller.signal }).then(
      (data) => setLoad({ key, data, failure: null, loading: false }),
      (error: unknown) => { if (!controller.signal.aborted) setLoad((current) => ({ key, data: current.data?.channel.id === channelId ? current.data : null, failure: channelFailure(error), loading: false })); },
    );
    return () => controller.abort();
  }, [key]);
  useEffect(() => { listLibraryChannels().then(setLibrary, () => setLibrary([])); }, [channelId]);

  const data = load.data?.channel.id === channelId ? load.data : null;
  const name = data?.channel.name ?? mention?.name ?? '';
  const saved = library ? libraryChannelFor(library, channelId, name || null) : null;
  const savedPages = usePagedLibrary(saved && (tab === 'library' || load.failure) ? { kind: 'video', source: 'youtube', group: saved.uploader, sort: 'recent', limit: PAGE_FIRST } : null);
  const follow = (data?.channel.follow_id ? follows.find((entry) => entry.id === data.channel.follow_id) : null) ?? (data ? findChannelBySource(follows, data.channel.url) ?? null : null);
  const following = Boolean(follow) || optimistic;
  const tabs = pageTabs(data, saved?.count ?? 0);
  const wall = data?.tab === apiTab ? data : null; // a response for another tab only lends its header
  const entries = useMemo(() => sortEntries(wall?.entries ?? [], sort), [wall, sort]);
  const canonical = data?.channel.url ?? `https://www.youtube.com/channel/${channelId}`;

  async function followChannel() {
    if (!data) return;
    setOptimistic(true);
    setFollowError(null);
    try {
      await props.onFollow({ name: data.channel.name, url: data.channel.url });
    } catch {
      setFollowError('Lumina could not follow this channel. Try again.');
    } finally {
      setOptimistic(false);
    }
  }
  async function record(url: string) {
    setRecording('starting');
    try {
      await createLiveRecording({ source_url: url, start_intent: 'live_edge', fallback_policy: 'allow_live_edge' });
      setRecording('recording');
    } catch {
      setRecording('failed');
    }
  }
  const onTabKeys = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = tabs.findIndex(([value]) => value === tab);
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const [next] = tabs[(index + step + tabs.length) % tabs.length];
    onTabChange(next);
    requestAnimationFrame(() => document.getElementById(`channel-tab-${next}`)?.focus());
  };

  const live = data?.channel.live ?? null;
  const description = data?.channel.description ?? null;
  const banner = remoteArt(data?.channel.banner_url);
  const busy = load.loading && load.key !== key;
  const savedWall = savedPages.items.length ? (
    <div className="g-channel-wall">{savedPages.items.map((item, index) => <StillCard item={item} key={item.id} kind="video" onPlay={onPlayLibrary} position={index} priority={index < 8 ? 2 : 3} shape="still" sizes={WALL_SIZES} />)}</div>
  ) : null;

  let body;
  if (load.failure && load.key === key) {
    body = (
      <>
        <div className="g-inline-error" role="alert">
          <p className="g-channel-state">{load.failure === 'unavailable' ? 'This channel isn\'t available.' : 'Lumina couldn\'t reach YouTube for this channel.'}</p>
          {load.failure === 'unavailable'
            ? <a className="g-button" href={canonical} rel="noopener noreferrer" target="_blank"><ExternalLink aria-hidden="true" /> Open on YouTube</a>
            : <button className="g-button g-button-text" data-focus-item onClick={() => setAttempt((value) => value + 1)} type="button">Try again</button>}
        </div>
        {savedWall ? <section aria-labelledby="g-channel-saved" className="g-channels-section"><h2 id="g-channel-saved">In your library</h2>{savedWall}</section> : null}
      </>
    );
  } else if (tab === 'library') {
    body = savedWall ?? <div aria-busy="true" className="g-channel-wall" />;
  } else if (!wall || busy) {
    body = <div aria-busy="true" className={`g-channel-wall${tab === 'shorts' ? ' is-short' : ''}`}>{Array.from({ length: 8 }, (_, index) => <span className="g-still-slot" key={index} />)}</div>;
  } else if (wall.restricted) {
    body = <p className="g-channel-state">YouTube requires an account for these videos.</p>;
  } else if (!entries.length) {
    body = <p className="g-channel-state">{EMPTY_TAB[tab]}</p>;
  } else {
    const note = pageLimitNote(limit, wall.has_more);
    body = (
      <>
        <div className="g-toolbar g-channel-toolbar">
          <label className="g-sort g-select"><span className="g-label">Sort</span><select aria-label="Sort" onChange={(event) => setSort(event.target.value as ChannelSort)} value={sort}><option value="latest">Latest</option><option value="popular">Popular</option></select></label>
        </div>
        <div aria-busy={load.loading} className={`g-channel-wall${tab === 'shorts' ? ' is-short' : ''}`}>
          {entries.map((item, index) => (
            <RemoteStillCard
              actions={item.capabilities?.can_acquire === false || !canDownload ? undefined : <button className="g-text-button g-button-text g-remote-action" data-focus-item disabled={Boolean(item.saved_item_id) || isQueueing(item)} onClick={() => onQueue(item)} type="button">{item.saved_item_id ? 'Saved' : isQueueing(item) ? 'Saving…' : 'Save'}</button>}
              item={item} key={item.webpage_url || item.id || index} onOpen={onOpen} position={index} priority={index < 8 ? 2 : 3}
              shape={tab === 'shorts' ? 'short' : 'still'} sizes={tab === 'shorts' ? '180px' : WALL_SIZES}
            />
          ))}
        </div>
        {limit === PAGE_FIRST && wall.has_more ? <button className="g-button g-channel-more" data-focus-item onClick={() => setLimit(PAGE_MAX)} type="button">Show more</button> : null}
        {note ? <p className="g-label g-channel-note">{note}</p> : null}
      </>
    );
  }

  return (
    <div className="surface gallery g-channel-page" onKeyDown={(event) => { if (isBackKey(event)) { event.preventDefault(); window.history.back(); } else moveFocus(event); }}>
      <div className={`g-channel-banner${banner ? '' : ' is-field'}`} style={banner ? undefined : { backgroundColor: fallbackColour(channelId) }}>
        {banner ? <GalleryArt alt="" art={banner} card={null} colour={{ colour: fallbackColour(channelId), fromPalette: true }} kind="backdrop" priority={1} sizes="100vw" /> : null}
      </div>
      <header className="g-channel-header">
        <ChannelAvatar live={Boolean(live)} name={name || 'Channel'} size={120} url={data?.channel.avatar_url ?? mention?.avatarUrl} />
        <div className="g-channel-header-copy">
          <h1 tabIndex={-1}>{name || <span aria-hidden="true" className="g-channel-name-slot" />}{data?.channel.verified ? <Check aria-label="Verified" className="g-channel-verified" /> : null}{live ? <LiveBadge state="live" surface="paper" /> : null}</h1>
          {data ? <p className="g-label g-kicker">{channelKicker(data)}</p> : null}
          {description ? (
            <div className="g-channel-description">
              <p className={expanded ? 'is-open' : ''}>{description}</p>
              <button className="g-text-button" data-focus-item onClick={() => setExpanded((value) => !value)} type="button">{expanded ? 'Less' : 'More'}</button>
            </div>
          ) : null}
          <div className="g-channel-actions" data-focus-row>
            {following
              ? <button aria-pressed="true" className="g-button" data-focus-item disabled={!follow} onClick={() => setSettings(true)} type="button"><Check aria-hidden="true" /> Following</button>
              : <button className="g-button is-primary" data-focus-item disabled={!data} onClick={() => void followChannel()} type="button"><Plus aria-hidden="true" /> Follow</button>}
            {follow ? <button className="g-button" data-focus-item onClick={() => setSettings(true)} type="button">Follow settings</button> : null}
            <a className="g-button" data-focus-item href={canonical} rel="noopener noreferrer" target="_blank"><ExternalLink aria-hidden="true" /> Open on YouTube</a>
          </div>
          {followError ? <p className="g-channel-error" role="alert">{followError}</p> : null}
        </div>
      </header>
      {live ? (
        <section aria-labelledby="g-channel-live" className="g-channel-live">
          <h2 className="sr-only" id="g-channel-live">Live now</h2>
          <span className="g-channel-live-still"><GalleryArt alt="" art={remoteArt(live.artwork_url)} card={{ name: live.title || name }} colour={{ colour: fallbackColour(live.webpage_url), fromPalette: true }} kind="still" priority={1} sizes="480px" /></span>
          <div className="g-channel-live-copy">
            <LiveBadge state="live" surface="paper" />
            <p className="g-channel-live-title">{live.title}</p>
            {live.view_count ? <p className="g-label">{formatCompactNumber(live.view_count)} watching</p> : null}
            <div className="g-channel-actions" data-focus-row>
              <button className="g-button is-primary" data-focus-item onClick={() => onOpen({ webpage_url: live.webpage_url, title: live.title, uploader: name, artwork_url: live.artwork_url })} type="button">Watch live</button>
              {!canDownload ? null : recording === 'recording' ? <p className="g-label g-record-status" role="status"><span aria-hidden="true" className="g-record-dot" />Recording</p>
                : <button className="g-button" data-focus-item disabled={recording === 'starting'} onClick={() => void record(live.webpage_url)} type="button">{recording === 'starting' ? <LoaderCircle aria-hidden="true" className="spin" /> : null}Record</button>}
            </div>
            {recording === 'failed' ? <p className="g-channel-error" role="alert">Lumina could not start this recording.</p> : null}
          </div>
        </section>
      ) : null}
      <div aria-label="Channel" className="g-tabs t-tabs g-channel-tabs" onKeyDown={onTabKeys} role="tablist">
        {tabs.map(([value, label]) => <button aria-controls="g-channel-panel" aria-selected={value === tab} className="g-label" data-focus-item id={`channel-tab-${value}`} key={value} onClick={() => onTabChange(value)} role="tab" tabIndex={value === tab ? 0 : -1} type="button">{label}</button>)}
      </div>
      <div aria-labelledby={`channel-tab-${tab}`} id="g-channel-panel" role="tabpanel">{body}</div>
      {settings && follow ? <FollowSettingsDialog automation={follow} commands={props.commands} onChange={props.onAutomationChange} onClose={() => setSettings(false)} onRecover={props.onRecover} onRemoved={props.onAutomationRemoved} /> : null}
    </div>
  );
}
