/**
 * The story so far: what happened before the member's next episode. It shows the assistant's points from
 * the dialogue when a recap exists, else the episode guide's own overviews (not AI output, so it shows even with the
 * recap switch off). It never waits more than 8 s for the AI.
 */
import { useEffect, useState } from 'react';

import { getRecap, requestRecap } from '../../api';
import type { RecapResponse } from '../../types';
import { episodeCode } from '../titles/titleModel';
import { usePollJob } from '../watch/usePollJob';

export const STORY_ABANDON_MS = 8_000;
const DONE: ReadonlySet<string> = new Set(['succeeded', 'failed', 'fallback']);
const FIRST = 3;
const MOST = 8;

export function StorySoFar({ episodeId }: { episodeId: string }) {
  const [recap, setRecap] = useState<RecapResponse | null>(null);
  const [expired, setExpired] = useState(false);
  const [all, setAll] = useState(false);
  useEffect(() => {
    let current = true;
    setRecap(null);
    setExpired(false);
    setAll(false);
    const timer = window.setTimeout(() => setExpired(true), STORY_ABANDON_MS);
    getRecap(episodeId)
      // Ask once; a refused request (AI off, nothing to recap, busy) falls back to the guide.
      .then((found) => (found.state === 'none' ? requestRecap(episodeId).catch(() => ({ ...found, state: 'fallback' as const })) : found))
      .then((next) => { if (current) setRecap(next); }, () => undefined);
    return () => { current = false; window.clearTimeout(timer); };
  }, [episodeId]);
  const settled = recap !== null && (DONE.has(recap.state) || expired);
  usePollJob(recap && !settled ? episodeId : null, getRecap, DONE, setRecap);
  if (!recap || !settled) return null;

  const ai = recap.state === 'succeeded' && recap.points.length > 0;
  const entries: Array<{ label: string | null; text: string }> = ai
    ? recap.points.map((point) => ({ label: null, text: point.text }))
    : recap.fallback.flatMap((entry) => (entry.overview ? [{ label: [episodeCode(entry), entry.name].filter(Boolean).join(' · '), text: entry.overview }] : []));
  if (!entries.length) return null;
  return (
    <section aria-labelledby="t-story-heading" className="t-story">
      <div className="t-story-head">
        <h2 className="t-h2" id="t-story-heading">The story so far</h2>
        <span className="t-source g-label">{ai ? 'From the episodes’ dialogue' : 'From the episode guide'}</span>
      </div>
      <ul className="t-points">
        {entries.slice(0, all ? MOST : FIRST).map((entry, index) => (
          <li key={index}>{entry.label ? <><span className="t-point-label g-label">{entry.label}</span> </> : null}{entry.text}</li>
        ))}
      </ul>
      {!all && entries.length > FIRST ? <button className="g-text-button" data-focus-item onClick={() => setAll(true)} type="button">Read all</button> : null}
    </section>
  );
}
