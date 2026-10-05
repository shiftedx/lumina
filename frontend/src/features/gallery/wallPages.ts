/**
 * Pages of one wall query: keyed by start_index, loaded one request at a time (a letter seek for name
 * sort, the cursor chain otherwise) and kept for the browser session, so Back shows the wall at once and then
 * refreshes what is on screen.
 */
import { useMemo, useSyncExternalStore } from 'react';

import { ApiRequestError, listTitles, type TitleListQuery } from '../../api';
import type { TitleLetter, TitlePage, TitleSummary } from '../../types';

export const FIRST_PAGE = 60;
export const NEXT_PAGE = 120;
const KEPT_QUERIES = 5; // one per wall kind: movies, shows, anime, albums, artists

type PageRequest = { cursor: string | null; letter: string | null; limit: number };
type Loaded = { start: number; items: TitleSummary[]; request: PageRequest; next: string | null; round: number };
export type TitleLister = (query: TitleListQuery, options?: { signal?: AbortSignal }) => Promise<TitlePage>;

const FIRST: PageRequest = { cursor: null, letter: null, limit: FIRST_PAGE };
const sameRequest = (a: PageRequest | null, b: PageRequest | null): boolean => Boolean(a && b && a.cursor === b.cursor && a.letter === b.letter);

export class WallStore {
  total: number | null = null;
  letters: TitleLetter[] = [];
  /** Where the wall was, and the poster to refocus, when it is shown again. */
  scrollY = 0;
  focusIndex: number | null = null;
  /** Set when a title page or search takes over, so their scroll moves are not recorded as the wall's. */
  frozen = false;
  private readonly pages = new Map<number, Loaded>();
  private wanted: [number, number] = [0, 0];
  private failedRequest: PageRequest | null = null;
  private lastRequest: PageRequest | null = null;
  private refreshing: PageRequest[] = [];
  /** Bumped by revalidate(); a page requested in a newer round replaces older pages it overlaps. */
  private round = 0;
  /** A rejected cursor restarts the wall once; a second rejection before any later page loads shows Try again. */
  private restarted = false;
  private inFlight: AbortController | null = null;
  private readonly listeners = new Set<() => void>();
  private version = 0;

  constructor(private readonly query: TitleListQuery, private readonly list: TitleLister = listTitles) {}

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    this.pump();
    return () => {
      this.listeners.delete(listener);
      if (!this.listeners.size) this.inFlight?.abort();
    };
  };

  readonly snapshot = (): number => this.version;

  get failed(): boolean {
    return this.failedRequest !== null;
  }

  itemAt(index: number): TitleSummary | undefined {
    if (this.total !== null && index >= this.total) return undefined;
    let found: TitleSummary | undefined;
    for (const page of this.pages.values()) found = page.items[index - page.start] ?? found;
    return found;
  }

  /** The wall shows indices first..last: load whatever is missing there. */
  ensure(first: number, last: number): void {
    this.wanted = [first, last];
    this.pump();
  }

  retry(): void {
    this.failedRequest = null;
    this.lastRequest = null;
    this.emit();
    this.pump();
  }

  /**
   * Back on a cached wall: re-request the first page (for the total) and every loaded page overlapping first..last,
   * replacing them in place. Also clears a failure, so returning to a failed wall recovers.
   */
  revalidate(first: number, last: number): void {
    const running = this.inFlight ? this.lastRequest : null;
    this.failedRequest = null;
    this.lastRequest = null;
    if (this.total !== null) {
      this.round += 1;
      const queue = (request: PageRequest) => { if (!this.refreshing.some((queued) => sameRequest(queued, request) && queued.limit === request.limit)) this.refreshing.push(request); };
      queue(FIRST);
      // A page in flight may answer from before the change: ask for it again in this round.
      if (running) queue(running);
      for (const page of this.pages.values()) if (page.start <= last && page.start + page.items.length > first) queue(page.request);
    }
    this.emit();
    this.pump();
  }

  freeze(scrollY: number, focusIndex: number | null): void {
    this.frozen = true;
    this.scrollY = scrollY;
    this.focusIndex = focusIndex;
  }

  private reset(): void {
    this.pages.clear();
    this.total = null;
    this.letters = [];
    this.refreshing = [];
    this.lastRequest = null;
  }

  private next(): PageRequest | null {
    if (this.total === null) return FIRST;
    const refresh = this.refreshing.shift();
    if (refresh) return refresh;
    const first = Math.max(0, this.wanted[0]);
    const last = Math.min(this.total - 1, this.wanted[1]);
    let gap = -1;
    for (let index = first; index <= last; index += 1) {
      if (!this.itemAt(index)) { gap = index; break; }
    }
    if (gap < 0) return null;
    // Name sort seeks from the greatest letter anchor at or before the gap; other sorts follow cursors from the start.
    const anchor = this.query.sort === 'name' ? [...this.letters].reverse().find((entry) => entry.index <= gap) : undefined;
    const floor = anchor?.index ?? 0;
    let from: Loaded | undefined;
    for (const page of this.pages.values()) {
      const end = page.start + page.items.length;
      if (page.next && page.start >= floor && end <= gap && (!from || end > from.start + from.items.length)) from = page;
    }
    if (from) return { cursor: from.next, letter: null, limit: NEXT_PAGE };
    return anchor ? { cursor: null, letter: anchor.letter, limit: NEXT_PAGE } : null;
  }

  private pump(): void {
    if (this.inFlight || this.failedRequest || !this.listeners.size) return;
    const refresh = this.total !== null && this.refreshing.length > 0;
    const request = this.next();
    // A request that just ran and still leaves the same gap would loop; stop until retry().
    if (!request || (!refresh && sameRequest(request, this.lastRequest))) return;
    this.lastRequest = request;
    const controller = new AbortController();
    const round = this.round;
    this.inFlight = controller;
    this.list({ ...this.query, cursor: request.cursor, letter: request.letter, limit: request.limit }, { signal: controller.signal }).then((page) => {
      const start = page.start_index ?? 0;
      if (page.total !== undefined && page.total !== null) this.total = page.total;
      // a server without totals (the old mocked API) shows only what its first page holds.
      else if (this.total === null) this.total = start + page.items.length;
      // The end of the chain is the end of the list, whatever an earlier first page said (it may have shrunk since).
      if (!page.next_cursor) this.total = start + page.items.length;
      if (page.letters) this.letters = page.letters;
      if (request.cursor || request.letter) this.restarted = false;
      const end = start + page.items.length;
      // Titles added or removed since an older round shift positions: drop its pages this one overlaps, so none repeats.
      for (const [key, old] of this.pages) if (old.round < round && old.start < end && old.start + old.items.length > start) this.pages.delete(key);
      this.pages.set(start, { start, items: page.items, request, next: page.next_cursor ?? null, round });
    }, (failure: unknown) => {
      if (controller.signal.aborted) { this.lastRequest = null; return; }
      // A stale or forged cursor after a deploy: start again from the first page.
      if (failure instanceof ApiRequestError && failure.status === 400 && (request.cursor || request.letter) && !this.restarted) {
        this.restarted = true;
        this.reset();
        return;
      }
      this.failedRequest = request;
    }).finally(() => {
      if (this.inFlight === controller) this.inFlight = null;
      this.emit();
      this.pump();
    });
  }

  private emit(): void {
    this.version += 1;
    for (const listener of this.listeners) listener();
  }
}

const stores = new Map<string, WallStore>();

/** The session's store for one wall query; the three most recently used queries are kept. */
export function wallStore(key: string, query: TitleListQuery): WallStore {
  const store = stores.get(key) ?? new WallStore(query);
  stores.delete(key);
  stores.set(key, store);
  if (stores.size > KEPT_QUERIES) stores.delete(stores.keys().next().value as string);
  return store;
}

/** The store for `key`, re-rendering the caller whenever its pages change. `query` must be derived from `key`. */
export function useWallStore(key: string, query: TitleListQuery): WallStore {
  const store = useMemo(() => wallStore(key, query), [key]); // query is a function of key
  useSyncExternalStore(store.subscribe, store.snapshot);
  return store;
}

/** Drops every cached wall: tests, and a sign-out or member switch (one member's titles and marks never reach another). */
export function forgetWallStores(): void {
  stores.clear();
}
