import { useEffect, useRef, useState, type ImgHTMLAttributes, type ReactNode } from 'react';

import { apiBaseUrl } from './api';

export type ArtworkProps = Pick<ImgHTMLAttributes<HTMLImageElement>, 'alt' | 'className' | 'loading' | 'sizes'> & {
  fallback?: ReactNode;
  src?: string | null;
};

export function resolveArtworkUrl(src: string | null | undefined): string | null {
  if (!src?.startsWith('/api/')) return null;
  return `${apiBaseUrl}${src}`;
}

/** HTTP status of a finished image load (Resource Timing); a 404 is permanent, only transient failures retry. */
function artworkStatus(url: string): number | undefined {
  const entries = globalThis.performance?.getEntriesByName?.(url) as Array<PerformanceResourceTiming & { responseStatus?: number }> | undefined;
  return entries?.at(-1)?.responseStatus;
}

// Transient failures (429 from the per-member artwork budget during a fast scroll, 5xx)
// retry until the 60s rate window has fully turned over, so settled cards paint.
// fixed schedule, not Retry-After (an <img> cannot read headers).
const RETRY_DELAYS_MS = [250, 750, 5_000, 15_000, 45_000];

export function Artwork({ alt = '', className, fallback, loading = 'lazy', sizes, src }: ArtworkProps) {
  const resolved = resolveArtworkUrl(src);
  const [seenSrc, setSeenSrc] = useState(resolved);
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const [loadedSrc, setLoadedSrc] = useState<string | null>(null);
  const [retryAttempt, setRetryAttempt] = useState(0);
  const retryTimerRef = useRef<ReturnType<typeof globalThis.setTimeout> | null>(null);

  // Failure, load and retry state belong to the URL they were seen for. Reset during render (not in an effect), so a
  // load or error that lands before effects run is never wiped (the 1.3.1 fix), and a URL that failed earlier gets
  // a fresh try when it comes back.
  if (seenSrc !== resolved) {
    setSeenSrc(resolved);
    setFailedSrc(null);
    setLoadedSrc(null);
    setRetryAttempt(0);
  }

  useEffect(() => {
    if (retryTimerRef.current !== null) globalThis.clearTimeout(retryTimerRef.current);
    return () => {
      if (retryTimerRef.current !== null) globalThis.clearTimeout(retryTimerRef.current);
    };
  }, [resolved]);

  const retrySeparator = resolved?.includes('?') ? '&' : '?';
  const displaySource = resolved && retryAttempt ? `${resolved}${retrySeparator}lumina_retry=${retryAttempt}` : resolved;

  if (!displaySource || failedSrc === displaySource) {
    return <span aria-label={alt || undefined} className={`artwork-placeholder thumbnail-placeholder ${className || ''}`} role={alt ? 'img' : undefined}>{fallback === undefined ? <span aria-hidden="true" className="artwork-placeholder-mark"><i /><i /><i /></span> : fallback}</span>;
  }

  return <img alt={alt} className={className} data-artwork-loading={loadedSrc === displaySource ? undefined : 'true'} decoding="async" loading={loading} onError={(event) => {
    setFailedSrc(displaySource);
    if (retryAttempt >= RETRY_DELAYS_MS.length || artworkStatus(event.currentTarget.currentSrc || event.currentTarget.src) === 404) return;
    retryTimerRef.current = globalThis.setTimeout(() => {
      setRetryAttempt((attempt) => attempt + 1);
      setFailedSrc(null);
    }, RETRY_DELAYS_MS[retryAttempt]);
  }} onLoad={() => setLoadedSrc(displaySource)} sizes={sizes} src={displaySource} />;
}
