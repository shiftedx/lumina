import { useEffect, useMemo, useState } from 'react';

/** True on a phone-width viewport; false where matchMedia is missing (jsdom). */
export function useNarrow(query = '(max-width: 640px)'): boolean {
  const media = useMemo(() => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query) : null), [query]);
  const [narrow, setNarrow] = useState(media?.matches ?? false);
  useEffect(() => {
    if (!media) return undefined;
    const update = () => setNarrow(media.matches);
    media.addEventListener('change', update);
    update();
    return () => media.removeEventListener('change', update);
  }, [media]);
  return narrow;
}
