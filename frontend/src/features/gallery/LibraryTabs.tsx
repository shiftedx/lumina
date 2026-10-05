/**
 * The Library's tab row: All, then each lens with something the member can see, and
 * the quiet Deleted link. It renders at once from the member's last sections answer, kept in localStorage, and follows
 * GET /api/library/sections.
 */
import { type KeyboardEvent, type MouseEvent, useCallback, useEffect, useRef, useState } from 'react';

import { getLibrarySections } from '../../api';
import type { LibrarySections } from '../../types';
import { LENS_LABELS, LIBRARY_LENSES, type LibraryLens } from './libraryLens';

/** Where in the Library a member can be: a lens, the All landing at /library, or the Collections pages. */
export type LibraryPlace = LibraryLens | 'all' | 'collections';
type Tab = Exclude<LibraryPlace, 'deleted' | 'collections'>;

const TABS: readonly Tab[] = ['all', ...LIBRARY_LENSES.filter((lens): lens is Exclude<LibraryLens, 'deleted'> => lens !== 'deleted')];
const SECTION_KEYS: ReadonlyArray<keyof LibrarySections> = ['movies', 'shows', 'anime', 'albums', 'artists', 'saved_audio', 'youtube', 'recordings', 'deleted'];
const FOCUSABLE = 'a[href], button:not(:disabled), select:not(:disabled), input:not(:disabled)';
const storageKey = (member: string) => `lumina.sections.${member}`;

export const placePath = (place: LibraryPlace): string => (place === 'all' ? '/library' : `/library/${place}`);

/** A stored answer counts only with every count a whole number; an older or edited value is ignored. */
function asSections(value: unknown): LibrarySections | null {
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  if (!SECTION_KEYS.every((key) => Number.isSafeInteger(record[key]) && (record[key] as number) >= 0)) return null;
  return Object.fromEntries(SECTION_KEYS.map((key) => [key, record[key]])) as unknown as LibrarySections;
}

export function readStoredSections(member: string): LibrarySections | null {
  try {
    const raw = window.localStorage.getItem(storageKey(member));
    return raw ? asSections(JSON.parse(raw)) : null;
  } catch {
    return null; // storage blocked, or not JSON
  }
}

export function forgetStoredSections(member: string): void {
  try { window.localStorage.removeItem(storageKey(member)); } catch { /* storage blocked */ }
}

function storeSections(member: string, sections: LibrarySections): void {
  try { window.localStorage.setItem(storageKey(member), JSON.stringify(sections)); } catch { /* storage blocked or full */ }
}

/** What the member can see per tab: the stored answer at once, then the server's. `refresh` asks again. */
export function useLibrarySections(member: string): { sections: LibrarySections | null; failed: boolean; refresh: () => void } {
  const [state, setState] = useState(() => ({ member, sections: readStoredSections(member), failed: false }));
  const [attempt, setAttempt] = useState(0);
  const current = state.member === member ? state : { member, sections: readStoredSections(member), failed: false };
  useEffect(() => {
    const controller = new AbortController();
    getLibrarySections({ signal: controller.signal }).then((sections) => {
      storeSections(member, sections);
      setState({ member, sections, failed: false });
    }, () => {
      if (!controller.signal.aborted) setState((previous) => ({ member, sections: previous.member === member ? previous.sections : readStoredSections(member), failed: true }));
    });
    return () => controller.abort();
  }, [member, attempt]);
  const refresh = useCallback(() => {
    setState((previous) => ({ ...previous, failed: false }));
    setAttempt((value) => value + 1);
  }, []);
  return { sections: current.sections, failed: current.failed, refresh };
}

/** All always; Music for albums or saved audio; every other tab when its count is above 0. */
export function tabShown(tab: Tab, sections: LibrarySections | null): boolean {
  if (tab === 'all') return true;
  if (!sections) return false;
  if (tab === 'music') return sections.albums + sections.saved_audio > 0;
  return sections[tab] > 0;
}

/** A plain primary click opens the place in the app; ctrl, meta, shift, alt and middle clicks stay the browser's. */
export function followInApp(event: MouseEvent<HTMLAnchorElement>, open: () => void): void {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  open();
}

export type LibraryTabsProps = {
  current: LibraryPlace;
  sections: LibrarySections | null;
  /** Vault owners always see Deleted, to restore anyone's file. */
  isAdmin: boolean;
  onOpen: (place: LibraryPlace) => void;
};

export function LibraryTabs({ current, sections, isAdmin, onOpen }: LibraryTabsProps) {
  const nav = useRef<HTMLElement>(null);
  // Phone: the row scrolls sideways; the current tab starts in view once it is drawn (sections can arrive later).
  // Only the row moves, never the window: that would undo a restored scroll.
  const placed = useRef(false);
  useEffect(() => {
    const row = nav.current;
    const tab = row?.querySelector<HTMLElement>('[aria-current="page"]');
    if (placed.current || !row || !tab) return;
    placed.current = true;
    row.scrollLeft = tab.offsetLeft - row.offsetLeft;
  }, [sections]);
  /** Left and Right step along the row and stop at its ends; Down leaves for the page's next control. */
  const keys = (event: KeyboardEvent<HTMLElement>) => {
    const row = nav.current;
    if (!row) return;
    const links = [...row.querySelectorAll<HTMLAnchorElement>('a[href]')];
    const at = links.indexOf(document.activeElement as HTMLAnchorElement);
    if (at < 0) return;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      links[at + (event.key === 'ArrowLeft' ? -1 : 1)]?.focus();
      event.preventDefault();
    } else if (event.key === 'ArrowDown') {
      const page = row.closest('.gallery') ?? document.body;
      const next = [...page.querySelectorAll<HTMLElement>(FOCUSABLE)].find((element) => !row.contains(element) && Boolean(row.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING));
      if (next) {
        next.focus();
        event.preventDefault();
      }
    }
  };
  const link = (place: LibraryPlace, label: string, className: string) => (
    <a aria-current={current === place ? 'page' : undefined} className={className} href={placePath(place)} key={place} onClick={(event) => followInApp(event, () => onOpen(place))}>{label}</a>
  );
  return (
    <nav aria-label="Library" className="g-tabs" data-focus-row onKeyDown={keys} ref={nav}>
      {TABS.filter((tab) => tabShown(tab, sections)).map((tab) => link(tab, tab === 'all' ? 'All' : LENS_LABELS[tab], 'g-tab'))}
      {link('collections', 'Collections', 'g-tabs-deleted')}
      {isAdmin || (sections?.deleted ?? 0) > 0 ? link('deleted', LENS_LABELS.deleted, 'g-tabs-deleted') : null}
    </nav>
  );
}
