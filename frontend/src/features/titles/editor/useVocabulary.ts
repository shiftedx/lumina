import { useEffect, useState } from 'react';

import { getMetadataVocabulary, getSessionUserId } from '../../../api';
import type { VocabularyField } from '../../../types';

const TTL_MS = 60_000;
// Vocabulary is the household's visible titles for one member, so the cache is per user: a member switch never reads the last member's suggestions.
const cache = new Map<string, { at: number; values: string[] }>();
const cacheKey = (field: VocabularyField) => `${getSessionUserId() ?? ''}:${field}`;

/** Suggestions are a courtesy: empty until loaded, and a failure is swallowed. */
export function useVocabulary(field: VocabularyField | undefined): string[] {
  const [values, setValues] = useState<string[]>(() => (field ? cache.get(cacheKey(field))?.values ?? [] : []));
  useEffect(() => {
    if (!field) return undefined;
    const key = cacheKey(field);
    const hit = cache.get(key);
    if (hit && Date.now() - hit.at < TTL_MS) { setValues(hit.values); return undefined; }
    setValues([]);
    let current = true;
    getMetadataVocabulary(field).then((entries) => {
      const next = entries.map((entry) => entry.value);
      cache.set(key, { at: Date.now(), values: next });
      if (current) setValues(next);
    }, () => undefined);
    return () => { current = false; };
  }, [field]);
  return values;
}

export const clearVocabularyCache = () => cache.clear();
