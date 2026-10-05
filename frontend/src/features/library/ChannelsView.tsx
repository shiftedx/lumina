/**
 * The YouTube tab's Channels view: saved YouTube grouped by channel as Infuse groups it,
 * each a 16:9 tile of its newest saved video with the series count badge of unwatched videos. A tile opens the channel
 * page on "In your library" when its channel id is known, else the Videos wall filtered to that channel..
 */
import { type ReactNode, useEffect, useState } from 'react';

import { listLibraryChannels } from '../../api';
import type { LibraryChannelResponse } from '../../types';
import { channelPagePath, followAppLink, rememberChannel } from '../channels/channelMention';
import { ChannelAvatar } from '../gallery/ChannelAvatar';
import { GalleryArt } from '../gallery/GalleryArt';
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

const tileHref = (channel: LibraryChannelResponse) => (channel.channel_id ? channelPagePath(channel.channel_id, 'library') : `/library/youtube?${new URLSearchParams({ channel: channel.uploader })}`);
const plural = (count: number) => `${count} video${count === 1 ? '' : 's'}`;

export function ChannelsView({ state, onWallChange, lenses, viewSwitch }: { state?: string; onWallChange: (state: string) => void; lenses?: ReactNode; viewSwitch: ReactNode }) {
  const sort = new URLSearchParams(state ?? '').get('sort') === 'name' ? 'name' : 'recent';
  const [channels, setChannels] = useState<LibraryChannelResponse[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setFailed(false);
    listLibraryChannels(sort).then((list) => { if (active) setChannels(list); }, () => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [sort, attempt]);
  const body = failed ? (
    <div className="g-inline-error" role="alert"><p>Lumina could not load your channels.</p><button className="g-button g-button-text" onClick={() => setAttempt((value) => value + 1)} type="button">Try again</button></div>
  ) : !channels ? (
    <div aria-busy="true" aria-label="Loading" className="g-channels-tiles" role="status">{Array.from({ length: 8 }, (_, index) => <span className="g-still-slot" key={index} />)}</div>
  ) : (
    <div className="g-channels-tiles">
      {channels.map((channel, index) => {
        const age = relativeAge(channel.newest_at);
        return (
          <a aria-label={[channel.name, plural(channel.count), channel.unwatched_count ? `${channel.unwatched_count} unwatched` : null].filter(Boolean).join(', ')} className="g-channel-card" data-focus-item href={tileHref(channel)} key={channel.key} onClick={(event) => { if (channel.channel_id) rememberChannel({ id: channel.channel_id, name: channel.name, avatarUrl: channel.avatar_url }); followAppLink(event); }}>
            <span className="g-still-frame g-channel-card-frame">
              <GalleryArt alt="" art={remoteArt(`/api/library/${encodeURIComponent(channel.newest_item_id)}/artwork`)} card={{ name: channel.name }} colour={{ colour: fallbackColour(channel.key), fromPalette: true }} kind="still" position={index} priority={index < 8 ? 1 : 2} sizes={SIZES} />
              {channel.unwatched_count ? <ArtMarker marker={{ kind: 'count', count: channel.unwatched_count }} /> : null}
              {channel.avatar_url ? <ChannelAvatar className="g-channel-card-avatar" name={channel.name} size={40} url={channel.avatar_url} /> : null}
            </span>
            <span aria-hidden="true" className="g-still-caption"><span className="g-still-title">{channel.name}</span><span className="g-label">{[plural(channel.count), age ? `newest ${age}` : null].filter(Boolean).join(' · ')}</span></span>
          </a>
        );
      })}
    </div>
  );
  return (
    <div className="surface gallery g-wall g-library-channels" onKeyDown={(event) => moveFocus(event, { targets: ':is(a, button, select):not(:disabled)', horizontalExit: 'select' })}>
      {lenses}
      <header className="g-masthead"><h1>YouTube</h1>{channels ? <p className="g-label g-kicker">{`${channels.length} channel${channels.length === 1 ? '' : 's'}`}</p> : null}</header>
      <div className="g-toolbar">
        {viewSwitch}
        <label className="g-sort g-select"><span className="g-label">Sort</span><select onChange={(event) => onWallChange(event.target.value === 'name' ? 'view=channels&sort=name' : 'view=channels')} value={sort}><option value="recent">Recently updated</option><option value="name">Name A–Z</option></select></label>
      </div>
      {body}
    </div>
  );
}
