/**
 * A 16:9 still card for anything from the web: the still card body around the proxied
 * artwork, the LIVE badge top-left, the member's marker top-right, a scrim label, and the editorial caption. One button
 * opens the watch page; Record, Schedule and Save sit below the caption as their own focus stops.
 */
import type { ReactNode } from 'react';

import { SOURCE_LABELS } from '../../luminaModel';
import type { RemoteEntry } from '../../types';
import { ArtMenu } from './ArtMenu';
import { GalleryArt } from './GalleryArt';
import type { LoadPriority } from './imageLoader';
import { LiveBadge, liveBadgeState } from './LiveBadge';
import { ArtMarker } from './PosterCard';
import { usePlaybackIntent } from './usePlaybackIntent';
import { remoteArt, remoteColour, remoteLabel, remoteLine, remoteMarker, remoteProvider, remoteScrim } from './remoteModel';

export type RemoteStillCardProps = {
  item: RemoteEntry;
  onOpen: (item: RemoteEntry) => void;
  priority: LoadPriority;
  /** Queue order inside the priority class: row * 1000 + column. */
  position?: number;
  sizes: string;
  /** Title and label line under the art; false on phone walls. */
  caption?: boolean;
  /** 'still' 16:9 (default), 'short' 9:16, 'compact' the 160px Up next row. */
  shape?: 'still' | 'short' | 'compact';
  /** The provider name on the scrim, for surfaces that mix providers. */
  showProvider?: boolean;
  /** Secondary text buttons under the caption (Record, Schedule, Save), each its own focus stop. */
  actions?: ReactNode;
  /** An id whose text the card's accessible description reads (Record's boundary copy). */
  describedBy?: string;
  /** Show ENDED whatever the lifecycle (a live card kept while focused after it left the snapshot). */
  ended?: boolean;
  /** Roving tabindex from StillRail; walls leave it unset. */
  tabIndex?: number;
  onFocus?: () => void;
};

export function RemoteStillCard({ item, onOpen, priority, position = 0, sizes, caption = true, shape = 'still', showProvider = false, actions, describedBy, ended = false, tabIndex, onFocus }: RemoteStillCardProps) {
  const badge = liveBadgeState(item.capabilities?.lifecycle, ended);
  const scrim = remoteScrim(item, ended);
  const line = remoteLine(item);
  const title = item.title || 'Untitled video';
  // Resting on the card, its art or its ⋯ menu starts resolving the stream; only playable single videos.
  const intent = usePlaybackIntent(item.kind !== 'playlist' && item.capabilities?.can_play !== false ? item.webpage_url || null : null);
  return (
    <div className={`g-still-card g-remote-card is-${shape}${item.kind === 'playlist' ? ' is-playlist' : ''}${badge ? ' has-badge' : ''}`} data-remote-key={item.webpage_url || item.id || undefined} {...intent}>
      <button aria-describedby={describedBy} aria-label={remoteLabel(item, ended)} className="g-still" data-focus-item onClick={() => onOpen(item)} onFocus={onFocus} tabIndex={tabIndex} type="button">
        <span className="g-still-frame">
          <GalleryArt alt="" art={remoteArt(item.artwork_url)} card={{ name: title }} colour={remoteColour(item)} kind="still" position={position} priority={priority} sizes={sizes} />
          {badge ? <LiveBadge className="is-corner" startsAt={item.capabilities?.scheduled_start} state={badge} surface="art" /> : null}
          <ArtMarker marker={remoteMarker(item, ended)} small />
          {scrim || showProvider ? (
            <span aria-hidden="true" className="g-remote-scrim">
              <span className="g-label">{scrim}</span>
              {showProvider ? <span className="g-label g-remote-provider">{SOURCE_LABELS[remoteProvider(item)]}</span> : null}
            </span>
          ) : null}
        </span>
        {caption ? <span aria-hidden="true" className="g-still-caption"><span className="g-still-title">{title}</span>{line ? <span className="g-label">{line}</span> : null}</span> : null}
      </button>
      <ArtMenu subject={{ kind: 'remote', item }} />
      {actions ? <div className="g-remote-actions">{actions}</div> : null}
    </div>
  );
}
