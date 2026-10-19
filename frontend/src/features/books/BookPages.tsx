import { useEffect, useRef, useState } from 'react';

import { getSessionUserId } from '../../api';
import { Skeleton } from '../../ui';
import { getBookPageBytes } from './booksApi';

// Encoded blobs only: decoded pixels belong to the visible image elements.
const PAGE_BYTES = 24 * 1024 * 1024;
const CACHE_BYTES = 48 * 1024 * 1024;
const CACHE_ENTRIES = 4;
type PageResource = { controller: AbortController; bytes: number; url?: string; loading: boolean };

/** One reader's short-lived page window; nothing is persisted or shared across accounts/books. */
export function BookPages({ bookId, userId, pages, count, direction, onError }: {
  bookId: string; userId: string; pages: number[]; count: number; direction: 'ltr' | 'rtl'; onError: () => void;
}) {
  const cache = useRef(new Map<number, PageResource>());
  const visible = useRef(pages);
  visible.current = pages;
  const decoded = useRef(new Set<number>());
  const alive = useRef(true);
  const [, refresh] = useState(0);
  const wanted = pages.join(',');

  function remove(index: number) {
    const entry = cache.current.get(index);
    if (!entry) return;
    entry.controller.abort();
    if (entry.url) URL.revokeObjectURL(entry.url);
    cache.current.delete(index);
    decoded.current.delete(index);
  }

  function load(index: number, speculative = false) {
    if (index < 0 || index >= count || getSessionUserId() !== userId || cache.current.has(index)) return;
    if (speculative && (document.hidden || (navigator as Navigator & { connection?: { saveData?: boolean } }).connection?.saveData)) return;
    const used = () => [...cache.current.values()].reduce((total, entry) => total + entry.bytes, 0);
    // Reserve the maximum response size for each fetch, including lookahead. Evict only off-screen resources.
    for (const [old] of cache.current) {
      if (used() + PAGE_BYTES <= CACHE_BYTES && cache.current.size < CACHE_ENTRIES) break;
      if (!visible.current.includes(old)) remove(old);
    }
    if (used() + PAGE_BYTES > CACHE_BYTES || cache.current.size >= CACHE_ENTRIES) return;
    const entry: PageResource = { controller: new AbortController(), bytes: PAGE_BYTES, loading: true };
    cache.current.set(index, entry);
    void getBookPageBytes(bookId, index, entry.controller.signal, speculative).then((blob) => {
      if (!alive.current || entry.controller.signal.aborted || cache.current.get(index) !== entry || getSessionUserId() !== userId) return;
      entry.bytes = blob.size;
      entry.url = URL.createObjectURL(blob);
      entry.loading = false;
      refresh((value) => value + 1);
    }).catch(() => {
      if (entry.controller.signal.aborted || !alive.current) return;
      cache.current.delete(index);
      if (visible.current.includes(index)) onError();
    });
  }

  useEffect(() => {
    alive.current = true;
    const visibilityChanged = () => {
      if (document.hidden) {
        for (const [index, entry] of cache.current) if (entry.loading && !visible.current.includes(index)) remove(index);
      } else if (visible.current.every((index) => decoded.current.has(index))) {
        load(visible.current[visible.current.length - 1] + 1, true);
      }
    };
    document.addEventListener('visibilitychange', visibilityChanged);
    return () => {
      alive.current = false;
      document.removeEventListener('visibilitychange', visibilityChanged);
      for (const index of cache.current.keys()) remove(index);
    };
  }, []);

  useEffect(() => {
    const first = pages[0];
    const last = pages[pages.length - 1];
    for (const [index, entry] of cache.current) {
      // Reuse a promoted lookahead request; cancel abandoned jumps and retain one completed previous page.
      if (index < first - 1 || index > last + 1 || (entry.loading && index === first - 1)) remove(index);
    }
    for (const index of pages) load(index);
    refresh((value) => value + 1);
  }, [bookId, userId, wanted]);

  function loaded(index: number) {
    decoded.current.add(index);
    if (visible.current.every((page) => decoded.current.has(page))) load(visible.current[visible.current.length - 1] + 1, true);
  }

  return <div className={`books-manga-pages${pages.length === 2 ? ' is-spread' : ''}`} dir={direction}>
    {pages.map((index) => cache.current.get(index)?.url
      ? <img alt={`Page ${index + 1} of ${count}`} decoding="async" key={index} onError={onError} onLoad={() => loaded(index)} src={cache.current.get(index)!.url} />
      : <Skeleton count={1} key={index} label={`Loading page ${index + 1}…`} shape="poster" />)}
  </div>;
}
