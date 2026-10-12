/**
 * Explore: without a query, the masthead, the search field, a Live now teaser from the
 * cached live snapshot (fetched once per visit), typographic category tiles and one rail per popular category; with a
 * query, results grouped by kind in a fixed order with Source chips and jump links. Moved from the old browse surfaces with
 * its props kept.
 */
import { useCanDownload } from '../access/access';
import { CircleAlert, RotateCcw } from 'lucide-react';
import { type FormEvent, type KeyboardEvent, memo, type ReactNode, useEffect, useRef, useState } from 'react';

import { getLiveDiscovery } from '../../api';
import { EXPLORER_CATEGORIES } from '../../explorerCategories';
import { isProviderVisible, useStreamingProviders } from '../streaming/providers';
import { SOURCE_LABELS } from '../../luminaModel';
import type { LibraryItem, LiveSnapshot, PopularSnapshot, RemoteEntry, SearchSource, SearchSourceError, TitleSummary, YouTubeSearchResult } from '../../types';
import { followAppLink, openAppPath, rememberChannel } from '../channels/channelMention';
import { ChannelAvatar } from '../gallery/ChannelAvatar';
import { PosterCard } from '../gallery/PosterCard';
import { RemoteStillCard } from '../gallery/RemoteStillCard';
import { StillCard } from '../gallery/StillCard';
import { StillRail } from '../gallery/StillRail';
import { isBackKey, moveFocus } from '../media/focusNav';
import { CategoryRail, ForYouRail } from '../reco/ExploreRails';
import { RecoRowOwnersProvider, rowOwners } from '../reco/recoFeedback';
import { RecoPersonalScope } from '../reco/recoPersonal';
import { orderRails, remoteTarget } from '../reco/recoModel';
import { channelTarget, exploreGroups, exploreKicker, GROUP_ORDER, type GroupKey, LIBRARY_GROUP_MAX, liveTeaser, popularNotice, popularRails } from './exploreModel';
import { REMOTE_CARD_SIZES, remoteProvider } from '../gallery/remoteModel';
import './explore.css';

const sourceLabel = (key: string) => SOURCE_LABELS[key as SearchSource] || key;

export function FilterChips<T extends string>({ label, options, value, onChange }: { label: string; options: Array<[T, string]>; value: T; onChange: (value: T) => void }) {
  return <div aria-label={label} className="g-chips" role="group">{options.map(([key, text]) => <button aria-pressed={value === key} className="g-chip" data-focus-item key={key} onClick={() => onChange(key)} type="button">{text}</button>)}</div>;
}

export type ExploreSurfaceProps = {
  query: string;
  results: YouTubeSearchResult[];
  loading: boolean;
  error: string | null;
  onSearch: (query: string) => void;
  onOpen: (item: YouTubeSearchResult) => void;
  onQueue: (item: YouTubeSearchResult) => void;
  isQueueing: (item: YouTubeSearchResult) => boolean;
  library: LibraryItem[];
  popular: PopularSnapshot | null;
  popularError: string | null;
  sourceErrors?: SearchSourceError[];
  libraryResults?: LibraryItem[];
  onOpenLibrary?: (item: LibraryItem) => void;
  onLoadMore?: () => void;
  titleResults?: TitleSummary[];
  onOpenTitle?: (title: TitleSummary) => void;
  /** "See all" on a popular rail, in the address (?rail=); local when LuminaApp does not pass it. */
  rail?: string | null;
  onRailChange?: (rail: string | null) => void;
  /** Inside the Streaming page: no masthead or search field (the page has them), the page's provider scopes Live now,
   * the order is Live now, For you, Browse by category, then the popular rails, and Live now's See all goes through onSeeAllLive. */
  embedded?: boolean;
  provider?: SearchSource;
  onSeeAllLive?: () => void;
  /** Embedded on Streaming: the latest uploads from followed channels, shown after Live now. */
  fromChannels?: RemoteEntry[];
  onSeeAllChannels?: () => void;
};

function SaveAction({ item, busy, onQueue }: { item: RemoteEntry; busy: boolean; onQueue: (item: RemoteEntry) => void }) {
  const canDownload = useCanDownload();
  if (item.capabilities?.can_acquire === false || !canDownload) return null;
  const saved = Boolean(item.saved_item_id);
  return <button className="g-text-button g-button-text g-remote-action" data-focus-item disabled={saved || busy} onClick={() => onQueue(item)} type="button">{saved ? 'Saved' : busy ? 'Saving…' : 'Save'}</button>;
}

function RailSlot({ heading }: { heading: string }) {
  return <section aria-busy="true" aria-label={`Loading ${heading}`} className="g-rail g-rail-loading"><h2>{heading}</h2><div className="g-rail-slots">{[0, 1, 2, 3].map((slot) => <span className="g-still-slot" key={slot} />)}</div></section>;
}

function ExploreSurfaceView(props: ExploreSurfaceProps) {
  const { query, results: allResults, loading, error, onSearch, onOpen, onQueue, isQueueing, popular, popularError, sourceErrors = [], libraryResults = [], onOpenLibrary, onLoadMore, titleResults = [], onOpenTitle } = props;
  const { providers } = useStreamingProviders();
  const results = allResults.filter((item) => isProviderVisible(providers, item.source));
  const [draft, setDraft] = useState(query);
  const [source, setSource] = useState('all');
  const [live, setLive] = useState<LiveSnapshot | null>(null);
  const [liveLoading, setLiveLoading] = useState(true);
  const [localRail, setLocalRail] = useState<string | null>(null);
  const rail = props.onRailChange ? props.rail ?? null : localRail;
  const changeRail = props.onRailChange ?? setLocalRail;
  const lastSeeAll = useRef<string | null>(null);
  // Back from a See all wall: focus returns to the rail's See all; a deep link has no remembered rail, so focus is left alone.
  useEffect(() => {
    if (rail !== null || !lastSeeAll.current) return;
    const key = lastSeeAll.current;
    lastSeeAll.current = null;
    // A frame later: the shell's route-change heading focus is already queued and would otherwise land after this.
    const frame = requestAnimationFrame(() => [...document.querySelectorAll<HTMLElement>('section.g-rail')].find((section) => section.dataset.railKey === key)?.querySelector<HTMLElement>('.g-rail-all')?.focus());
    return () => cancelAnimationFrame(frame);
  }, [rail]);
  useEffect(() => { setDraft(query); setSource('all'); }, [query]);
  // Decision: the cached live snapshot, once on entry, never polled here.
  useEffect(() => {
    let active = true;
    getLiveDiscovery().then((snapshot) => { if (active) setLive(snapshot); }).catch(() => { /* the teaser is simply absent */ }).finally(() => { if (active) setLiveLoading(false); });
    return () => { active = false; };
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (rail && !query && isBackKey(event)) { event.preventDefault(); if (props.onRailChange) window.history.back(); else changeRail(null); return; }
    moveFocus(event);
  };
  const submit = (event: FormEvent) => { event.preventDefault(); onSearch(draft); };
  const actionsFor = (item: RemoteEntry) => <SaveAction busy={isQueueing(item)} item={item} onQueue={onQueue} />;
  const { embedded, provider, onSeeAllLive } = props;
  const shell = embedded ? 'gallery g-explore' : 'surface gallery g-explore';
  const masthead = embedded ? (query ? (
    <header className="g-masthead g-explore-results-head">
      <h2 className="g-explore-results-title" tabIndex={-1}>Results for “{query}”</h2>
      <a className="g-text-button" data-focus-item href="/streaming" onClick={followAppLink}>Clear</a>
    </header>
  ) : null) : query ? (
    <header className="g-masthead g-explore-results-head">
      <h1 className="g-explore-results-title">Results for “{query}”</h1>
      <a className="g-text-button" data-focus-item href="/streaming" onClick={followAppLink}>Clear</a>
    </header>
  ) : (
    <header className="g-masthead"><h1 tabIndex={-1}>Explore</h1><p className="g-label g-kicker">{exploreKicker(popular)}</p></header>
  );
  const searchField = embedded ? null : (
    <form className="g-explore-search" onSubmit={submit} role="search">
      <label className="sr-only" htmlFor="g-explore-q">Search</label>
      <input id="g-explore-q" onChange={(event) => setDraft(event.currentTarget.value)} placeholder="Search YouTube, SoundCloud and your library" type="search" value={draft} />
    </form>
  );

  if (!query) {
    const rails = orderRails(popularRails(popular), popular?.category_order);
    const expanded = rail ? rails.find((entry) => entry.key === rail) : null;
    // The new policy always sends category_order; the legacy (kill switch off) branch never does, and ignores "Show fewer".
    const personal = Boolean(popular?.category_order?.length);
    const shownRails = expanded ? [expanded] : [{ key: 'for-you', entries: popular?.for_you ?? [] }, ...rails];
    const owners = rowOwners(shownRails.map((entry) => ({ key: entry.key, targets: entry.entries.map(remoteTarget) })));
    if (expanded) {
      return <div className={shell} onKeyDown={onKeyDown}>{masthead}<RecoPersonalScope value={personal}><RecoRowOwnersProvider value={owners}><CategoryRail eager expanded extra={actionsFor} onOpen={onOpen} rail={expanded} /></RecoRowOwnersProvider></RecoPersonalScope></div>;
    }
    const teaser = liveTeaser(live).filter((item) => (!provider || remoteProvider(item) === provider) && isProviderVisible(providers, item.source));
    // One compact, sideways-scrolling row (owner, 2.3.1): Live now and the rows stay above the fold, and nothing shifts.
    const categories = (
      <nav aria-label="Browse by category" className="g-chips g-category-chips" data-focus-row>
        {EXPLORER_CATEGORIES.map((category) => <button className="g-chip" data-focus-item key={category.key} onClick={() => onSearch(category.query)} type="button">{category.label}</button>)}
      </nav>
    );
    const notice = popularNotice(popular);
    let popularBody: ReactNode = null;
    if (rails.length) popularBody = rails.map((entry) => <CategoryRail key={entry.key} onOpen={onOpen} onSeeAll={(key) => { lastSeeAll.current = key; changeRail(key); }} rail={entry} />);
    else if (popularError || popular?.state === 'failed') popularBody = <div className="g-empty-result" key="popular-failed"><p className="g-explore-empty-title">Popular is taking a pause.</p><p className="g-label">{popularError || popular?.error || 'Previously gathered results are unavailable.'}</p></div>;
    else if (popular && popular.state !== 'loading') popularBody = <div className="g-empty-result" key="popular-empty"><p className="g-explore-empty-title">Nothing popular yet.</p><p>Discovery will try fresh categories shortly.</p></div>;
    else popularBody = <div aria-busy="true" aria-label="Loading Popular" className="g-rail-slots" key="popular-loading" role="status">{[0, 1, 2, 3].map((slot) => <span className="g-still-slot" key={slot} />)}</div>;
    return (
      <div className={shell} onKeyDown={onKeyDown}>
        <RecoPersonalScope value={personal}><RecoRowOwnersProvider value={owners}>
        {masthead}
        {searchField}
        {/* While a row loads it holds its place (heading + card slots), so rows below never jump when it lands (CLS). */}
        {liveLoading && !teaser.length ? <RailSlot heading="Live now" /> : null}
        {teaser.length ? <StillRail eager heading="Live now" items={teaser} liveCount={provider ? null : live?.live_total} onOpen={onOpen} onSeeAll={onSeeAllLive ?? (() => openAppPath('/streaming/live'))} railKey="live-now" seeAllHref="/streaming/live" showProvider={!provider} /> : null}
        {embedded && props.fromChannels?.length ? <StillRail heading="From your channels" items={props.fromChannels.filter((item) => isProviderVisible(providers, item.source))} onOpen={onOpen} onSeeAll={props.onSeeAllChannels} railKey="from-channels" seeAllHref="/streaming/channels" /> : null}
        {!popular && !popularError ? <RailSlot heading={embedded ? 'Recommended for you' : 'For you'} /> : null}
        <ForYouRail heading={embedded ? 'Recommended for you' : undefined} items={popular?.for_you ?? []} onOpen={onOpen} />
        {categories}
        {popularBody}
        {/* Below the rows, so a late notice never pushes them down (CLS). */}
        {notice ? <p className="g-label g-explore-notice" role="status"><CircleAlert aria-hidden="true" /> {notice}</p> : null}
        </RecoRowOwnersProvider></RecoPersonalScope>
      </div>
    );
  }

  const sources = [...new Set([...results.map((item) => item.source || 'youtube'), ...sourceErrors.map((failure) => failure.source)])];
  const shown = results.filter((item) => source === 'all' || (item.source || 'youtube') === source);
  const groups = exploreGroups(source === 'library' ? [] : shown);
  const libraryShown = onOpenLibrary && (source === 'all' || source === 'library') ? libraryResults.slice(0, LIBRARY_GROUP_MAX) : [];
  const titlesShown = onOpenTitle && (source === 'all' || source === 'library') ? titleResults.slice(0, LIBRARY_GROUP_MAX) : [];
  const present = GROUP_ORDER.filter(([key]) => groups[key].length);
  const hasResults = results.length > 0 || libraryResults.length > 0 || titleResults.length > 0;
  // Live and Shorts are rails with their own heading (rail-results-…); the others are sections headed g-explore-….
  const headingId = (key: GroupKey) => (key === 'live' || key === 'shorts' ? `rail-results-${key}` : `g-explore-${key}`);
  const jump = (key: GroupKey) => document.getElementById(headingId(key))?.scrollIntoView({ block: 'start' });
  const group = (key: GroupKey, heading: string, body: ReactNode) => <section aria-labelledby={`g-explore-${key}`} className="g-explore-group" key={key}><h2 id={`g-explore-${key}`} tabIndex={-1}>{heading}</h2>{body}</section>;
  const wall = (entries: RemoteEntry[], shape: 'still' | 'short' = 'still') => (
    <div aria-busy={loading} className={`g-explore-wall is-${shape}`}>
      {entries.map((item, index) => <RemoteStillCard actions={actionsFor(item)} item={item} key={item.webpage_url || item.id || index} onOpen={onOpen} position={index} priority={index < 8 ? 2 : 3} shape={shape} showProvider={sources.length > 1} sizes={shape === 'short' ? '180px' : REMOTE_CARD_SIZES} />)}
    </div>
  );
  const bodies: Record<GroupKey, () => ReactNode> = {
    channels: () => (
      <div className="g-explore-channels" data-focus-row>
        {groups.channels.map((item) => {
          const target = channelTarget(item);
          const content = <><ChannelAvatar name={item.title || item.uploader || ''} size={96} url={item.artwork_url} /><span className="g-explore-channel-name">{item.title || item.uploader}</span><span className="g-label">{sourceLabel(item.source || 'youtube')}</span></>;
          return target
            ? <a className="g-explore-channel" data-focus-item href={target.href} key={item.webpage_url || item.id} onClick={(event) => { if (target.id) rememberChannel({ id: target.id, name: item.title || '', avatarUrl: item.artwork_url }); followAppLink(event); }}>{content}</a>
            : <button className="g-explore-channel" data-focus-item key={item.webpage_url || item.id} onClick={() => onOpen(item)} type="button">{content}</button>;
        })}
      </div>
    ),
    live: () => <StillRail eager heading="Live now" items={groups.live} onOpen={onOpen} railKey="results-live" showProvider={sources.length > 1} />,
    videos: () => <>{wall(groups.videos)}{!loading && onLoadMore && source !== 'library' ? <button className="g-button g-explore-more" data-focus-item onClick={onLoadMore} type="button">More results</button> : null}</>,
    shorts: () => <StillRail heading="Shorts" items={groups.shorts} onOpen={onOpen} railKey="results-shorts" shape="short" />,
    playlists: () => wall(groups.playlists),
  };

  let status: ReactNode = null;
  if (error && !hasResults) status = <div className="g-inline-error" role="alert"><p className="g-explore-empty-title">Explore is unavailable right now.</p><button className="g-button g-button-text" data-focus-item onClick={() => onSearch(query)} type="button">Try again</button></div>;
  else if (loading && !hasResults) status = <div aria-busy="true" aria-label="Searching" className="g-explore-wall" role="status">{Array.from({ length: 8 }, (_, index) => <span className="g-still-slot" key={index} />)}</div>;
  else if (!loading && !present.length && !libraryShown.length && !titlesShown.length && !sourceErrors.length) {
    status = <div className="g-empty-result"><p className="g-explore-empty-title">{source !== 'all' && hasResults ? `Nothing from ${sourceLabel(source)} matches “${query}”.` : `Nothing found for “${query}”.`}</p><p>Try a broader phrase, a creator name, or paste the exact video URL.</p></div>;
  }

  return (
    <div className={shell} onKeyDown={onKeyDown}>
      {masthead}
      {searchField}
      {hasResults ? (
        <div className="g-toolbar">
          <FilterChips label="Source" onChange={setSource} options={[['all', 'All'], ...sources.map((key): [string, string] => [key, sourceLabel(key)]), ...(libraryResults.length && onOpenLibrary ? [['library', `Your library (${libraryResults.length})`] as [string, string]] : [])]} value={source} />
          {present.length >= 3 ? <nav aria-label="Jump to" className="g-explore-jump">{present.map(([key, , short], index) => <span key={key}>{index ? <span aria-hidden="true"> · </span> : null}<button className="g-text-button" data-focus-item onClick={() => jump(key)} type="button">{short}</button></span>)}</nav> : null}
        </div>
      ) : null}
      {sourceErrors.filter((failure) => source === 'all' || source === failure.source).map((failure) => (
        <div className="g-explore-source-error" key={failure.source} role="alert">
          <p><strong>{sourceLabel(failure.source)} results are unavailable</strong> <span className="g-label">{failure.message}</span></p>
          {failure.retryable ? <button className="g-button" data-focus-item disabled={loading} onClick={() => onSearch(query)} type="button"><RotateCcw aria-hidden="true" /> Retry {sourceLabel(failure.source)}</button> : null}
        </div>
      ))}
      {status}
      {titlesShown.length || libraryShown.length ? (
        <section aria-labelledby="g-explore-library" className="g-explore-group">
          <h2 id="g-explore-library" tabIndex={-1}>In your library</h2>
          {titlesShown.length ? <div className="g-explore-posters">{titlesShown.map((title, index) => <PosterCard key={title.id} onOpen={onOpenTitle!} position={index} priority={2} sizes="168px" title={title} />)}</div> : null}
          {libraryShown.length ? <div className="g-explore-wall">{libraryShown.map((item, index) => <StillCard item={item} key={item.id} kind="video" onPlay={onOpenLibrary!} position={index} priority={2} shape="still" sizes={REMOTE_CARD_SIZES} />)}</div> : null}
        </section>
      ) : null}
      {present.map(([key, heading]) => (key === 'live' || key === 'shorts' ? <div className="g-explore-group" key={key}>{bodies[key]()}</div> : group(key, heading, bodies[key]())))}
    </div>
  );
}

export const ExploreSurface = memo(ExploreSurfaceView);
