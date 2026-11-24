import { type KeyboardEvent, type MouseEvent, useCallback, useEffect, useState } from 'react';

import { getLibrarySections } from '../../api';
import type { VideoLibraryView } from '../../app/routes';
import type { LibrarySections } from '../../types';
import { TabScroller } from '../../ui';
import type { LibraryLens } from './libraryLens';

export type LibraryPlace = LibraryLens | 'all' | 'collections';
export type LibraryTool = 'collections' | 'deleted';

const SECTION_KEYS: ReadonlyArray<keyof LibrarySections> = ['movies', 'shows', 'anime', 'albums', 'artists', 'saved_audio', 'youtube', 'recordings', 'deleted'];
const FOCUSABLE = 'a[href], button:not(:disabled), select:not(:disabled), input:not(:disabled)';
const storageKey = (member: string) => `lumina.sections.${member}`;

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
export function useLibrarySections(member: string): { sections: LibrarySections | null; refresh: () => void } {
  const [state, setState] = useState(() => ({ member, sections: readStoredSections(member) }));
  const [attempt, setAttempt] = useState(0);
  const sections = state.member === member ? state.sections : readStoredSections(member);
  useEffect(() => {
    const controller = new AbortController();
    getLibrarySections({ signal: controller.signal }).then((sections) => {
      if (controller.signal.aborted) return;
      storeSections(member, sections);
      setState({ member, sections });
    }, () => {
      if (!controller.signal.aborted) setState((previous) => previous.member === member ? previous : { member, sections: readStoredSections(member) });
    });
    return () => controller.abort();
  }, [member, attempt]);
  const refresh = useCallback(() => setAttempt((value) => value + 1), []);
  useEffect(() => {
    const foreground = () => { if (document.visibilityState === 'visible') refresh(); };
    document.addEventListener('visibilitychange', foreground);
    return () => document.removeEventListener('visibilitychange', foreground);
  }, [refresh]);
  return { sections, refresh };
}

/** A plain primary click opens the place in the app; ctrl, meta, shift, alt and middle clicks stay the browser's. */
function followInApp(event: MouseEvent<HTMLAnchorElement>, open: () => void): void {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  open();
}

export type LibraryTabsProps = {
  current: VideoLibraryView | LibraryTool;
  sections: LibrarySections | null;
  /** Vault owners always see Deleted, to restore anyone's file. */
  isAdmin: boolean;
  onOpen: (place: LibraryTool) => void;
};

export function LibraryTabs({ current, sections, isAdmin, onOpen }: LibraryTabsProps) {
  const deletedShown = isAdmin || (sections?.deleted ?? 0) > 0;
  /** Left and Right step along the row and stop at its ends; Down leaves for the page's next control. */
  const keys = (event: KeyboardEvent<HTMLElement>) => {
    const row = event.currentTarget;
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
  const link = (place: LibraryTool, label: string) => (
    <a aria-current={current === place ? 'page' : undefined} className="g-tabs-deleted" href={`/library/${place}`} onClick={(event) => followInApp(event, () => onOpen(place))}>{label}</a>
  );
  return (
    <TabScroller activeKey={`${current}:${deletedShown}`} ariaLabel="Library tools" as="nav" focusRow onKeyDown={keys} rowClassName="g-tabs">
      {link('collections', 'Collections')}
      {deletedShown ? link('deleted', 'Deleted') : null}
    </TabScroller>
  );
}
