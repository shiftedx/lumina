/**
 * The YouTube tab's Channels view: saved YouTube grouped by channel as Infuse groups it,
 * each a 16:9 tile of its newest saved video with the series count badge of unwatched videos. A tile opens the channel
 * page on "In your library" when its channel id is known, else the Videos wall filtered to that channel..
 */
import { Search } from '../../luminaIcons';
import { type FormEvent, type ReactNode, useEffect, useState } from 'react';

import { listLibraryChannels } from '../../api';
import { ContextualSearch } from '../../app/ContextualSearch';
import type { LibraryChannelResponse } from '../../types';
import { Select } from '../../ui';
import { channelPagePath, followAppLink, rememberChannel } from '../channels/channelMention';
import { ChannelAvatar } from '../gallery/ChannelAvatar';
import { GalleryArt } from '../gallery/GalleryArt';
import { HeroFrame } from '../gallery/HeroFrame';
import { fallbackColour } from '../gallery/galleryModel';
import { ArtMarker } from '../gallery/PosterCard';
import { relativeAge, remoteArt } from '../gallery/remoteModel';
import { moveFocus } from '../media/focusNav';
import './channelsView.css';

const SIZES = '(max-width: 599px) 100vw, (max-width: 1023px) 50vw, 320px';

export function LibraryViewSwitch({ current, onWallChange }: { current: 'videos' | 'channels'; onWallChange: (state: string) => void }) {
  return (
    <div aria-label="View" className="g-chips" role="group">
      <button aria-pressed={current === 'videos'} className="g-chip" onClick={() => onWallChange('')} type="button">Videos</button>
      <button aria-pressed={current === 'channels'} className="g-chip" onClick={() => onWallChange('view=channels')} type="button">Channels</button>
    </div>
  );
}

const tileHref = (channel: LibraryChannelResponse, streamingEnabled: boolean) => (streamingEnabled && channel.channel_id ? channelPagePath(channel.channel_id, 'library') : `/video/youtube?${new URLSearchParams({ channel: channel.uploader })}`);
const plural = (count: number) => `${count} video${count === 1 ? '' : 's'}`;

export function ChannelsView({ state, onWallChange, lenses, viewSwitch, streamingEnabled = false }: { streamingEnabled?: boolean; state?: string; onWallChange: (state: string) => void; lenses?: ReactNode; viewSwitch: ReactNode }) {
  const params = new URLSearchParams(state ?? '');
  const sort = params.get('sort') === 'name' ? 'name' : 'recent';
  const search = params.get('search')?.trim().slice(0, 200) || '';
  const [searchDraft, setSearchDraft] = useState(search);
  const [channels, setChannels] = useState<LibraryChannelResponse[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => setSearchDraft(search), [search]);
  useEffect(() => {
    let active = true;
    setFailed(false);
    listLibraryChannels(sort).then((list) => { if (active) setChannels(list); }, () => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [sort, attempt]);
  const onRoute = ({ nextSearch = search, nextSort = sort }: { nextSearch?: string; nextSort?: typeof sort }) => {
    const next = new URLSearchParams({ view: 'channels' });
    if (nextSort === 'name') next.set('sort', 'name');
    const normalized = nextSearch.trim().slice(0, 200);
    if (normalized) next.set('search', normalized);
    onWallChange(next.toString());
  };
  const submitSearch = (event: FormEvent) => { event.preventDefault(); onRoute({ nextSearch: searchDraft }); };
  const filteredChannels = channels?.filter((channel) => !search || [channel.name, channel.uploader].filter(Boolean).join(' ').toLowerCase().includes(search.toLowerCase())) ?? null;
  const body = failed ? (
    <div className="g-inline-error" role="alert"><p>Lumina could not load your channels.</p><button className="g-button g-button-text" onClick={() => setAttempt((value) => value + 1)} type="button">Try again</button></div>
  ) : !filteredChannels ? (
    <div aria-busy="true" aria-label="Loading" className="g-channels-tiles" role="status">{Array.from({ length: 8 }, (_, index) => <span className="g-still-slot" key={index} />)}</div>
  ) : !filteredChannels.length ? (
    <div className="g-empty-result"><p>Nothing in your saved channels matches “{search}”.</p><button className="g-button g-button-text" onClick={() => onRoute({ nextSearch: '' })} type="button">Clear search</button></div>
  ) : (
    <div className="g-channels-tiles">
      {filteredChannels.map((channel, index) => {
        const age = relativeAge(channel.newest_at);
        const activity = channel.unwatched_count ? `${channel.unwatched_count} unwatched` : 'Up to date';
        return (
          <a aria-label={[channel.name, plural(channel.count), channel.unwatched_count ? `${channel.unwatched_count} unwatched` : null].filter(Boolean).join(', ')} className="g-channel-card" data-focus-item href={tileHref(channel, streamingEnabled)} key={channel.key} onClick={(event) => { if (streamingEnabled && channel.channel_id) rememberChannel({ id: channel.channel_id, name: channel.name, avatarUrl: channel.avatar_url }); followAppLink(event); }}>
            <span className="g-still-frame g-channel-card-frame">
              <GalleryArt alt="" art={remoteArt(`/api/library/${encodeURIComponent(channel.newest_item_id)}/artwork`)} card={{ name: channel.name }} colour={{ colour: fallbackColour(channel.key), fromPalette: true }} kind="still" position={index} priority={index < 8 ? 1 : 2} sizes={SIZES} />
              {channel.unwatched_count ? <ArtMarker marker={{ kind: 'count', count: channel.unwatched_count }} /> : null}
              {channel.avatar_url ? <ChannelAvatar className="g-channel-card-avatar" name={channel.name} size={40} url={channel.avatar_url} /> : null}
            </span>
            <span aria-hidden="true" className="g-still-caption"><span className="g-still-title">{channel.name}</span><span className="g-label">{[plural(channel.count), age ? `newest ${age}` : null].filter(Boolean).join(' · ')}</span><span className="g-channel-card-activity">{activity}</span></span>
          </a>
        );
      })}
    </div>
  );
  const hero = !failed && !search ? filteredChannels?.[0] : undefined;
  const heroArt = hero ? `/api/library/${encodeURIComponent(hero.newest_item_id)}/artwork` : null;
  const channelCount = filteredChannels?.length;
  return (
    <div className={`surface gallery g-wall g-library-channels is-video-browse g-youtube-browse${hero ? ' has-browse-hero' : ''}`} onKeyDown={(event) => moveFocus(event, { targets: ':is(a, button):not(:disabled)', horizontalExit: '[role="combobox"]' })}>
      <div className="g-browse-context">{lenses}<ContextualSearch label="Search saved channels"><form className="g-search" onSubmit={submitSearch} role="search"><Search aria-hidden="true" /><input aria-label="Search saved channels" className="g-input" maxLength={200} onChange={(event) => setSearchDraft(event.currentTarget.value)} onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); setSearchDraft(''); onRoute({ nextSearch: '' }); } }} placeholder="Search saved channels" type="search" value={searchDraft} /></form></ContextualSearch></div>
      {hero && heroArt ? <section aria-label={hero.name} className="g-browse-hero">
        <HeroFrame className="g-browse-hero-art" placement="home" src={heroArt}><GalleryArt alt="" art={remoteArt(heroArt)} card={null} colour={{ colour: fallbackColour(hero.key), fromPalette: true }} kind="still" priority={1} sizes="100vw" /></HeroFrame>
        <div className="g-browse-hero-copy"><p className="g-browse-hero-context">Creator spotlight · {plural(hero.count)}</p><h2>{hero.name}</h2><p>{[hero.unwatched_count ? `${hero.unwatched_count} unwatched` : 'Up to date', relativeAge(hero.newest_at) ? `newest ${relativeAge(hero.newest_at)}` : null].filter(Boolean).join(' · ')}</p><a className="g-button is-secondary" href={tileHref(hero, streamingEnabled)} onClick={(event) => { if (streamingEnabled && hero.channel_id) rememberChannel({ id: hero.channel_id, name: hero.name, avatarUrl: hero.avatar_url }); followAppLink(event); }}>Browse</a></div>
      </section> : null}
      <header className="g-masthead g-youtube-library-head"><h1 className={hero ? 'sr-only' : undefined}>YouTube</h1>{hero ? <h2>Your creators</h2> : null}{search ? <h2>Results for “{search}”</h2> : null}{channelCount !== undefined ? <p className="g-label g-kicker">{search ? `${channelCount} matching saved channel${channelCount === 1 ? '' : 's'}` : `${channelCount} channel${channelCount === 1 ? '' : 's'} in your library`}</p> : null}</header>
      <div className="g-toolbar">
        {viewSwitch}
        <label className="g-sort"><span className="g-label">Sort</span><Select aria-label="Sort" onChange={(event) => onRoute({ nextSort: event.target.value === 'name' ? 'name' : 'recent' })} value={sort}><option value="recent">Recently updated</option><option value="name">Name A–Z</option></Select></label>
      </div>
      {body}
    </div>
  );
}
