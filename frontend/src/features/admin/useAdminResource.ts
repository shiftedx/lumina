import { type SetStateAction, useCallback, useEffect, useRef, useState } from 'react';

import { errorMessage } from '../../utils';

/** Loads one admin resource; stale or post-unmount responses are dropped. `fetcher` must be stable. */
export function useAdminResource<T>(fetcher: () => Promise<T>, failure: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const request = useRef(0);

  const reload = useCallback(() => {
    const token = ++request.current;
    setLoading(true);
    fetcher().then((next) => { if (token === request.current) { setData(next); setError(null); } })
      .catch((loadError: unknown) => { if (token === request.current) setError(errorMessage(loadError, failure)); })
      .finally(() => { if (token === request.current) setLoading(false); });
  }, [fetcher, failure]);
  useEffect(() => { reload(); return () => { request.current += 1; }; }, [reload]);
  // Data put in place (a save or action response) is fresher than any reload still in flight.
  const put = useCallback((next: SetStateAction<T | null>) => { request.current += 1; setLoading(false); setData(next); }, []);

  return { data, setData: put, error, loading, reload };
}
