/**
 * Up next: the autoplay switch and one compact row per
 * related entry, a 160px still with the member's markers and the LIVE badge, the serif title and a channel label, then
 * the server's reason and the `…` menu. A row the member hides becomes its "Hidden. Undo" line in place. The autoplay
 * target is described as "Plays next" after its reason.
 */
import { useId } from 'react';

import type { YouTubeSearchResult } from '../../types';
import { RemoteStillCard } from '../gallery/RemoteStillCard';
import { RecoCard } from '../reco/recoFeedback';
import { remoteTarget } from '../reco/recoModel';
import { RecoPersonalScope, isPersonal } from '../reco/recoPersonal';
import { RecoImpressionScope, useRecoImpressions } from '../reco/useRecoImpressions';
import { Switch } from '../../ui';

export type WatchRelatedProps = {
  items: YouTubeSearchResult[];
  autoplay: boolean;
  autoplayIndex: number;
  showAutoplay: boolean;
  loading: boolean;
  error?: unknown | null;
  remote: boolean;
  onOpen: (item: YouTubeSearchResult) => void;
  onAutoplayChange: (autoplay: boolean) => void;
  onRetry?: () => void;
};

export function WatchRelated({ items, autoplay, autoplayIndex, showAutoplay, loading, error = null, remote, onOpen, onAutoplayChange, onRetry }: WatchRelatedProps) {
  const ids = `g-up-next-${useId().replace(/:/g, '')}`;
  const playsNextId = `${ids}-plays-next`;
  const nextItem = autoplay && autoplayIndex >= 0 ? items[autoplayIndex] : null;
  const nextTitle = nextItem?.title || 'Untitled video';
  const observe = useRecoImpressions(items.find((item) => item.reco)?.reco?.list_id ?? null);
  return (
    <RecoPersonalScope value={isPersonal(items)}>
    <RecoImpressionScope value={observe}>
      <section aria-labelledby="up-next-title" className="g-up-next">
        <header>
          <h2 id="up-next-title">Up next</h2>
          {showAutoplay ? <Switch checked={autoplay} className="g-autoplay" hint={nextItem ? `Next: ${nextTitle}` : 'Plays a related video when this one ends.'} label="Autoplay next video" onChange={onAutoplayChange} /> : null}
        </header>
        {items.length ? null : error ? <p className="g-list-row-meta" role="status">Couldn’t load suggestions. {onRetry ? <button className="g-text-button" data-focus-item disabled={loading} onClick={onRetry} type="button">{loading ? 'Retrying…' : 'Retry'}</button> : null}</p> : <p className="g-list-row-meta">{loading ? 'Finding related videos…' : remote ? 'No related videos from this source yet.' : 'Related videos appear here when you stream from a source.'}</p>}
        {items.map((item, index) => {
          const next = index === autoplayIndex && autoplay;
          const reasonId = `${ids}-reason-${index}`;
          const reason = item.reco?.reason;
          const describedBy = [next ? playsNextId : null, reason ? reasonId : null].filter(Boolean).join(' ') || undefined;
          return (
            <RecoCard key={item.webpage_url || item.id || index} reco={item.reco} target={remoteTarget(item)}>
                <RemoteStillCard
                  actions={next || reason ? <>{next ? <span className="g-label g-plays-next" id={playsNextId}>Plays next</span> : null}{reason ? <div className="g-list-row-meta" id={reasonId}>{reason}</div> : null}</> : null}
                  describedBy={describedBy}
                  item={item}
                  onOpen={onOpen}
                  position={index}
                  priority={3}
                  shape="compact"
                  sizes="160px"
                />
            </RecoCard>
          );
        })}
      </section>
    </RecoImpressionScope>
    </RecoPersonalScope>
  );
}
