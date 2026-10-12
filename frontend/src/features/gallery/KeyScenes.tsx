/**
 * Key scenes: up to three lines quoted from something the member has finished, each a jump into the
 * player at that second. It renders nothing unless the server says they are available.
 */
import { useEffect, useState } from 'react';

import { getKeyScenes } from '../../api';
import type { KeyScenes as KeyScenesData, TitleDetail } from '../../types';
import { formatDuration } from '../../utils';
import { episodeCode } from '../titles/titleModel';
import { loadTitle } from './titleCache';
import { AI_TIMEOUT } from './titlePageModel';

type Loaded = { scenes: KeyScenesData; itemId: string; source: string };

export function KeyScenes({ title, onPlay }: { title: Pick<TitleDetail, 'id' | 'type'>; onPlay: (itemId: string, startSeconds?: number) => void }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  useEffect(() => {
    let current = true;
    setLoaded(null);
    void (async () => {
      const scenes = await getKeyScenes(title.id, AI_TIMEOUT);
      if (!scenes.available || !scenes.item_id || !scenes.scenes.length) return;
      // A show's scenes come from one finished episode; its code names the section ("Key scenes from S2 · E3").
      const source = title.type === 'series' && scenes.title_id ? episodeCode(await loadTitle(scenes.title_id).catch(() => ({}))) : '';
      if (current) setLoaded({ scenes, itemId: scenes.item_id, source });
    })().catch(() => undefined);
    return () => { current = false; };
  }, [title.id, title.type]);
  if (!loaded) return null;
  return (
    <section aria-labelledby="t-scenes-heading" className="t-section t-scenes">
      <h2 className="t-h2" id="t-scenes-heading">{loaded.source ? `Key scenes from ${loaded.source}` : 'Key scenes'}</h2>
      <ul className="t-quotes" data-focus-row>
        {loaded.scenes.scenes.map((scene, index) => {
          const seconds = Math.floor(scene.start_ms / 1000);
          const clock = formatDuration(seconds);
          return (
            <li className="t-quote" key={index}>
              <blockquote className="t-quote-text">“{scene.quote}”</blockquote>
              <p className="t-quote-caption">{scene.caption}</p>
              <button aria-label={`Play from ${clock}`} className="g-button g-button-text t-quote-play" data-focus-item onClick={() => onPlay(loaded.itemId, seconds)} type="button"><span aria-hidden="true">▶ {clock}</span></button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
