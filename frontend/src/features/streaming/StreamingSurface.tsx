/**
 * Streaming (2.3.0): one page for YouTube, Twitch and Kick. It composes the Live, Explore and Subscriptions screens in
 * their embedded mode behind a provider switch (shown only when the member turned another provider on). Home is
 * Explore's front page for YouTube and Live for Twitch/Kick; "live" is the Live screen; "search" is Explore's results;
 * "channels" is the Subscriptions screen LuminaApp builds. A provider the member turned off never appears: its results
 * are filtered out here and the switch does not offer it.
 */
import { Search } from 'lucide-react';
import { cloneElement, type ComponentProps, type FormEvent, isValidElement, type ReactElement, type ReactNode, useEffect, useMemo, useState } from 'react';

import { SOURCE_LABELS } from '../../luminaModel';
import { Masthead, SegmentedControl } from '../../ui';
import { BlockedSurface, providerBlocked, useAccess } from '../access/access';
import { ExploreSurface } from '../explore/ExploreSurface';
import { LiveSurface } from '../live/LiveSurface';
import { isBackKey, moveFocus } from '../media/focusNav';
import './streaming.css';
import { isProviderVisible } from './providers';
import type { RemoteEntry } from '../../types';

export type StreamingProvider = 'youtube' | 'twitch' | 'kick';
export type StreamingView = 'home' | 'live' | 'search' | 'channels';
export interface StreamingSurfaceProps {
  view: StreamingView;
  provider: StreamingProvider;
  providers: StreamingProvider[];
  query?: string;
  rail?: string;
  onNavigate: (next: { view: StreamingView; provider?: StreamingProvider; query?: string; rail?: string }) => void;
  live: ComponentProps<typeof LiveSurface>;
  explore: ComponentProps<typeof ExploreSurface>;
  channels: ReactNode;
  /** Latest uploads from followed channels (the Your channels feed), for the "From your channels" row. */
  fromChannels?: RemoteEntry[];
}

const VIEWS: Array<{ value: StreamingView; label: string }> = [{ value: 'home', label: 'Browse' }, { value: 'live', label: 'Live now' }, { value: 'channels', label: 'Your channels' }];

export function StreamingSurface({ view: requestedView, provider: requested, providers, query = '', rail, onNavigate, live, explore, channels, fromChannels }: StreamingSurfaceProps) {
  const { access } = useAccess();
  const liveBlocked = providerBlocked(access, 'live');
  // A followed-only member sees only followed channels; with Live blocked, a provider's front page is its followed channels too.
  const view: StreamingView = access?.followed_only ? 'channels' : liveBlocked && requestedView === 'home' && requested !== 'youtube' ? 'channels' : requestedView;
  // A provider the member turned off (or the household blocked) falls back to the first one left, YouTube when it is on.
  const provider = providers.includes(requested) ? requested : providers[0] ?? 'youtube';
  const name = SOURCE_LABELS[provider];
  const [draft, setDraft] = useState(query);
  useEffect(() => setDraft(query), [query]);

  // YouTube and unknown sources are always visible; Twitch and Kick only when the member turned them on (the PM swaps in providers.ts' isProviderVisible).
  const visible = (item: { source?: string }) => isProviderVisible(providers, item.source) && !(item.source === 'youtube' && providerBlocked(access, 'youtube'));
  const liveProps = useMemo(() => ({ ...live, snapshot: live.snapshot && { ...live.snapshot, hero: live.snapshot.hero.filter(visible), items: live.snapshot.items.filter(visible) } }), [live, providers]); // eslint-disable-line react-hooks/exhaustive-deps
  const popularProp = useMemo(() => explore.popular && { ...explore.popular, items: explore.popular.items.filter(visible), for_you: explore.popular.for_you?.filter(visible) }, [explore.popular, providers]); // eslint-disable-line react-hooks/exhaustive-deps
  const results = useMemo(() => {
    const allowed = (source?: string) => (provider === 'youtube' ? providers.includes((source || 'youtube') as StreamingProvider) || source === 'soundcloud' : source === provider);
    return {
      results: explore.results.filter((item) => allowed(item.source)),
      sourceErrors: explore.sourceErrors?.filter((failure) => allowed(failure.source)),
      libraryResults: provider === 'youtube' ? explore.libraryResults : [],
      titleResults: provider === 'youtube' ? explore.titleResults : [],
    };
  }, [explore.results, explore.sourceErrors, explore.libraryResults, explore.titleResults, provider, providers]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (provider === 'youtube') explore.onSearch(draft);
    else onNavigate({ view: 'search', provider, query: draft });
  };
  const goLive = () => onNavigate({ view: 'live', provider });
  const channelsView = isValidElement(channels) ? cloneElement(channels as ReactElement<{ embedded?: boolean; provider?: string; key?: string }>, { embedded: true, provider: provider === 'youtube' ? undefined : provider, key: provider }) : channels;
  const current = view === 'search' ? 'home' : view;
  const views = access?.followed_only ? VIEWS.filter((entry) => entry.value === 'channels') : liveBlocked ? VIEWS.filter((entry) => entry.value !== 'live') : VIEWS;
  const searchable = provider === 'youtube' && !access?.followed_only && !providerBlocked(access, 'open_search');

  let body: ReactNode;
  if (view === 'live' && liveBlocked) body = <BlockedSurface what="Live" />;
  else if (view === 'channels') body = channelsView;
  else if (view === 'live' || (provider !== 'youtube' && view === 'home')) body = <LiveSurface {...liveProps} embedded onRailChange={(next) => onNavigate({ view: 'live', provider, rail: next ?? undefined })} provider={provider} rail={rail ?? null} />;
  else body = <ExploreSurface {...explore} {...results} popular={popularProp} embedded fromChannels={fromChannels} onSeeAllChannels={() => onNavigate({ view: 'channels', provider })} onSeeAllLive={goLive} provider={provider} query={view === 'search' ? query : ''} />;

  return (
    // The shell parks focus on the h1 when a See all wall opens, outside the embedded screen's own Back handler (the screens preventDefault theirs) and its arrow handler, so the h1 routes arrows itself.
    <div className="surface gallery g-streaming" onKeyDown={(event) => { if (rail && !query && (view === 'home' || view === 'live') && isBackKey(event)) { event.preventDefault(); window.history.back(); return; } if (event.target === event.currentTarget.querySelector('h1')) moveFocus(event); }}>
      <Masthead
        actions={!searchable ? undefined : (
          // Only YouTube search exists server-side; Twitch and Kick show their live and followed channels instead.
          <form className="g-search" onSubmit={submit} role="search">
            <Search aria-hidden="true" />
            <label className="sr-only" htmlFor="g-streaming-q">{`Search ${name}`}</label>
            <input className="g-input" id="g-streaming-q" onChange={(event) => setDraft(event.currentTarget.value)} placeholder={`Search ${name}`} type="search" value={draft} />
          </form>
        )}
        title="Streaming"
      />
      <div className="g-toolbar g-streaming-bar" onKeyDown={moveFocus}>
        {views.length > 1 ? <SegmentedControl hideLegend legend="Streaming view" onChange={(next) => onNavigate({ view: next, provider })} options={views} value={current} /> : null}
        {providers.length > 1 ? <SegmentedControl hideLegend legend="Provider" onChange={(next) => onNavigate({ view: view === 'search' ? 'home' : view, provider: next })} options={providers.map((value) => ({ value, label: SOURCE_LABELS[value] }))} value={provider} /> : null}
      </div>
      {body}
    </div>
  );
}
