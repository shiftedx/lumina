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

export type WatchRelatedProps = {
  items: YouTubeSearchResult[];
  autoplay: boolean;
  autoplayIndex: number;
  showAutoplay: boolean;
  loading: boolean;
  remote: boolean;
  onOpen: (item: YouTubeSearchResult) => void;
  onAutoplayChange: (autoplay: boolean) => void;
};

export function WatchRelated({ items, autoplay, autoplayIndex, showAutoplay, loading, remote, onOpen, onAutoplayChange }: WatchRelatedProps) {
  const playsNextId = `g-plays-next-${useId().replace(/:/g, '')}`;
  const observe = useRecoImpressions(items.find((item) => item.reco)?.reco?.list_id ?? null);
  return (
    <RecoPersonalScope value={isPersonal(items)}>
    <RecoImpressionScope value={observe}>
      <section aria-labelledby="up-next-title" className="g-up-next">
        <header>
          <h2 id="up-next-title">Up next</h2>
          {showAutoplay ? <label className="g-autoplay"><input checked={autoplay} onChange={(event) => onAutoplayChange(event.currentTarget.checked)} type="checkbox" /><span>Autoplay next video</span></label> : null}
        </header>
        {items.length ? null : <p className="g-list-row-meta">{loading ? 'Finding related videos…' : remote ? 'No related videos from this source yet.' : 'Related videos appear here when you stream from a source.'}</p>}
        {items.map((item, index) => {
          const next = index === autoplayIndex && autoplay;
          return (
            <RecoCard key={item.webpage_url || item.id || index} reco={item.reco} target={remoteTarget(item)}>
                <RemoteStillCard
                  actions={next ? <span className="g-label g-plays-next" id={playsNextId}>Plays next</span> : null}
                  describedBy={next ? playsNextId : undefined}
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
