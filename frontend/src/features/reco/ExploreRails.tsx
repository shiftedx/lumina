/**
 * Explore's recommendation rails: For you (the server's `for_you`, with reasons) and the
 * category rails (the same cards with the menu and no reason). `StillRail` owns its cards, so a card cannot be swapped
 * for its Undo row inside it: a hidden card leaves the rail at once and its Undo row sits directly above (contracts
 * gap G-R5-9).
 */
import { type ReactNode, useMemo } from 'react';

import { getPopularWall } from '../../api';

import type { RemoteEntry } from '../../types';
import { StillRail } from '../gallery/StillRail';
import { RecoHiddenRows, useRecoFilter } from './recoFeedback';
import { remoteTarget } from './recoModel';
import { RecoImpressionScope, useRecoImpressions } from './useRecoImpressions';

export function ForYouRail({ items, onOpen, heading = 'For you' }: { items: readonly RemoteEntry[]; onOpen: (item: RemoteEntry) => void; heading?: string }) {
  const visible = useRecoFilter(items, remoteTarget);
  const observe = useRecoImpressions(items.find((item) => item.reco)?.reco?.list_id ?? null);
  if (!items.length) return null;
  return (
    <RecoImpressionScope value={observe}>
      <RecoHiddenRows rail="for-you" targets={items.map(remoteTarget)} />
      {visible.length ? (
        <StillRail
          recoFor={(item) => ({ target: remoteTarget(item), reco: item.reco ?? null, rail: 'for-you' })}
          heading={heading}
          items={visible}
          onOpen={onOpen}
          railKey="for-you"
        />
      ) : null}
    </RecoImpressionScope>
  );
}

export type CategoryRailProps = {
  rail: { key: string; label: string; entries: readonly RemoteEntry[] };
  onOpen: (item: RemoteEntry) => void;
  onSeeAll?: (railKey: string) => void;
  expanded?: boolean;
  eager?: boolean;
  /** The surface's own actions for a card (Save and the like, under the card). */
  extra?: (item: RemoteEntry) => ReactNode;
};

export function CategoryRail({ rail, onOpen, onSeeAll, expanded = false, eager = false, extra }: CategoryRailProps) {
  const visible = useRecoFilter(rail.entries, remoteTarget);
  // Rail keys are `popular-<category>`; the wall endpoint takes the bare category key.
  const loadPage = useMemo(() => (expanded && rail.key.startsWith('popular-') ? (cursor: string | null) => getPopularWall(rail.key.slice(8), cursor) : undefined), [expanded, rail.key]);
  return (
    <>
      <RecoHiddenRows rail={rail.key} targets={rail.entries.map(remoteTarget)} />
      <StillRail
        actionsFor={extra}
        recoFor={(item) => ({ target: remoteTarget(item), reco: null, rail: rail.key })}
        eager={eager}
        expanded={expanded}
        heading={rail.label}
        items={visible}
        loadPage={loadPage}
        onOpen={onOpen}
        onSeeAll={onSeeAll}
        railKey={rail.key}
      />
    </>
  );
}
