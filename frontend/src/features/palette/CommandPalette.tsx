import { Search, X } from 'lucide-react';
import { type KeyboardEvent, type ReactNode, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { providerBlocked, useAccess } from '../access/access';
import { getLibraryAutomation, scanLibraries, searchLibrary, youtubeSearch } from '../../api';
import type { PaletteMode } from '../../app/commands';
import { isUrl, libraryThumbnail } from '../../luminaModel';
import { ChannelAvatar } from '../gallery/ChannelAvatar';
import { GalleryArt } from '../gallery/GalleryArt';
import { cardColour } from '../gallery/galleryModel';
import { remoteArt, remoteColour } from '../gallery/remoteModel';
import type { LibraryItem, LocalSearchMatch, LocalSearchResponse, SearchHistoryEntry, SourceAutomation, TitleSummary, UserProfile, YouTubeSearchResult } from '../../types';
import { Dialog, EmptyState, ErrorState, Field, fieldProps, IconButton, Input, Kbd, Spinner, StatusText, TextButton, useToast } from '../../ui';
import { filterActions, type PaletteContext, scanSummary } from './actions';
import { buildPalette, cleanQuery, flatOptions, keepActive, moveActive, type PaletteOption } from './paletteModel';
import { isApplePlatform } from '../../ui/platform';
import './palette.css';

export interface CommandPaletteProps {
  open: boolean;
  mode: PaletteMode;
  initialQuery?: string;
  onClose: () => void;
  user: UserProfile;
  history: SearchHistoryEntry[];
  channels: SourceAutomation[];
  context: PaletteContext;
  sessionKey: string;
  captureSessionToken: () => number;
  isSessionTokenCurrent: (token: number) => boolean;
  onRemoveHistory: (entryId: string) => void;
  onClearHistory: () => void;
  onOpenTitle: (title: TitleSummary) => void;
  onOpenMoment: (match: LocalSearchMatch) => void;
  onOpenLibraryItem: (item: LibraryItem) => void;
  onOpenChannel: (channel: SourceAutomation) => void;
  onOpenRemote: (result: YouTubeSearchResult) => void;
  onOpenUrl: (url: string) => void;
  onSearchEverything: (query: string) => void;
}

// Local and YouTube answers land independently: library results never wait for a slow provider (criterion 9).
type Results = { session: string; query: string; local: LocalSearchResponse | null; remote: YouTubeSearchResult[]; localFailed: boolean; remoteFailed: boolean; localDone: boolean; remoteDone: boolean };
const NONE: Results = { session: '', query: '', local: null, remote: [], localFailed: false, remoteFailed: false, localDone: false, remoteDone: false };
const DEBOUNCE_MS = 200;

export function CommandPalette(props: CommandPaletteProps) {
  const noOpen = providerBlocked(useAccess().access, 'open_search');
  const { open, mode, initialQuery = '', onClose, user, history, channels, context, sessionKey, captureSessionToken, isSessionTokenCurrent } = props;
  const [query, setQuery] = useState(initialQuery);
  const [results, setResults] = useState<Results>(NONE);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const [retry, setRetry] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const hintId = useId();
  const typed = cleanQuery(query);
  const toast = useToast();
  const [scanRoots, setScanRoots] = useState<readonly { id: string; label: string }[]>([]);
  const isAdmin = user.role === 'admin';

  // Once per open, admins only; a failure just leaves the per-root rows out.
  useEffect(() => {
    if (!open || !isAdmin) { setScanRoots([]); return undefined; }
    let live = true;
    getLibraryAutomation().then((state) => { if (live) setScanRoots(state.roots.map((root) => ({ id: root.root_id, label: root.label }))); }, () => undefined);
    return () => { live = false; };
  }, [open, isAdmin, sessionKey]);

  const runScan = useCallback((rootId?: string) => {
    scanLibraries(rootId).then(
      (result) => toast({ tone: 'success', message: scanSummary(result) }),
      () => toast({ tone: 'error', message: "Couldn't start the scan. Try again from Settings." }),
    );
  }, [toast]);
  const fullContext = useMemo<PaletteContext>(() => ({ ...context, scanRoots, scanLibraries: runScan }), [context, scanRoots, runScan]);

  // A reopening or another member starts clean: no query, error or result of the previous one survives.
  const seen = useRef({ open, sessionKey });
  useEffect(() => {
    const before = seen.current;
    seen.current = { open, sessionKey };
    // Reset when it opens again or the member changes; closing alone changes nothing.
    if (!open || (before.open && before.sessionKey === sessionKey)) return;
    setQuery(initialQuery); setResults(NONE); setActiveId(null); setLinkError(null); setAnnouncement('');
  }, [open, sessionKey, initialQuery]);

  // Fetch: debounced, cancelled when stale, dropped when the session changes.
  useEffect(() => {
    if (!open || mode === 'link' || typed.length < 2 || isUrl(typed)) { setResults(NONE); return undefined; }
    let live = true;
    const token = captureSessionToken();
    const current = () => live && isSessionTokenCurrent(token);
    const land = (patch: Partial<Results>) => setResults((value) => (value.query === typed && value.session === sessionKey ? { ...value, ...patch } : value));
    const timer = window.setTimeout(() => {
      setResults({ ...NONE, query: typed, session: sessionKey });
      searchLibrary(typed, 12).then(
        (local) => { if (current()) land({ local, localDone: true }); },
        () => { if (current()) land({ localFailed: true, localDone: true }); },
      );
      (noOpen ? Promise.resolve({ items: [] }) : youtubeSearch({ query: typed, limit: 5 })).then(
        (remote) => { if (current()) land({ remote: remote.items, remoteDone: true }); },
        () => { if (current()) land({ remoteFailed: true, remoteDone: true }); },
      );
    }, DEBOUNCE_MS);
    return () => { live = false; window.clearTimeout(timer); };
  }, [open, mode, typed, retry, sessionKey, captureSessionToken, isSessionTokenCurrent, noOpen]);

  // Answers belong to the member who asked: after a member change nothing of the previous one renders, even for a frame.
  const shown = results.session === sessionKey ? results : NONE;
  const groups = useMemo(() => buildPalette({
    query: typed, mode, user, history, channels,
    local: shown.query === typed ? shown.local : null,
    remote: shown.query === typed ? shown.remote : [],
    actions: filterActions(typed, fullContext).filter((action) => !(noOpen && action.id === 'add-link')),
  }).filter((group) => !(noOpen && (group.id === 'link' || group.id === 'everything' || group.id === 'youtube'))), [typed, mode, user, history, channels, shown, fullContext, noOpen]);
  const options = flatOptions(groups);
  const current = keepActive(options, activeId);
  const loading = shown.session !== '' && shown.query === typed && !(shown.localDone && shown.remoteDone); // NONE (nothing asked yet) is not loading
  const settled = shown.query === typed && shown.localDone && shown.remoteDone;
  const failed = settled && shown.localFailed && shown.remoteFailed;
  const nothing = settled && typed.length >= 2 && !failed && options.every((option) => option.kind === 'everything' || option.kind === 'action' || option.kind === 'goto');

  // One polite count per settled search, never per keystroke.
  useEffect(() => {
    if (!settled || typed.length < 2) return;
    const count = options.filter((option) => option.kind !== 'everything').length;
    setAnnouncement(count === 1 ? '1 result' : `${count} results`);
  }, [settled, typed]);

  function activate(option: PaletteOption | undefined) {
    if (!option) return;
    if (option.kind === 'recent') { setQuery(option.label); return; }
    if (option.kind === 'action') { onClose(); option.action.run(fullContext); return; }
    onClose();
    if (option.kind === 'title') props.onOpenTitle(option.title);
    else if (option.kind === 'moment') props.onOpenMoment(option.match);
    else if (option.kind === 'video') props.onOpenLibraryItem(option.item);
    else if (option.kind === 'channel') props.onOpenChannel(option.channel);
    else if (option.kind === 'youtube') props.onOpenRemote(option.result);
    else if (option.kind === 'goto') context.navigate(option.path);
    else if (option.kind === 'link') props.onOpenUrl(option.url);
    else props.onSearchEverything(option.query);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    const move = (to: Parameters<typeof moveActive>[2]) => { event.preventDefault(); setActiveId(moveActive(options, current, to)); };
    if (event.key === 'ArrowDown') move('next');
    else if (event.key === 'ArrowUp') move('prev');
    else if (event.key === 'PageDown') move('pageDown');
    else if (event.key === 'PageUp') move('pageUp');
    else if (event.key === 'Home' && event.ctrlKey) move('first');
    else if (event.key === 'End' && event.ctrlKey) move('last');
    else if (event.key === 'Enter' && event.nativeEvent.isComposing) return;
    else if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); if (typed && mode === 'search') { onClose(); props.onSearchEverything(typed); } }
    else if (event.key === 'Enter') {
      event.preventDefault();
      if (mode === 'link' && typed && !options.length) { setLinkError("That doesn't look like a link."); return; }
      activate(options.find((option) => option.id === current));
    } else if (event.key === 'Delete') {
      const option = options.find((candidate) => candidate.id === current);
      if (option?.kind === 'recent') { event.preventDefault(); props.onRemoveHistory(option.entryId); }
    } else if (event.key === 'Escape' && query) { event.preventDefault(); setQuery(''); setLinkError(null); }
  }

  const title = mode === 'link' ? 'Add a link' : 'Search Lumina';
  return (
    <Dialog className="g-palette" hideTitle initialFocus={inputRef} onClose={onClose} open={open} size="md" title={title}>
      <div className="g-palette-input">
        <Search aria-hidden="true" className="g-palette-search-icon" />
        <Field error={linkError} hideLabel label={title}>
          {(ids) => (
            <Input
              {...fieldProps(ids)}
              aria-activedescendant={current ? `${listId}-${current}` : undefined}
              aria-autocomplete="list"
              aria-controls={listId}
              aria-describedby={[ids.describedBy, hintId].filter(Boolean).join(' ')}
              aria-expanded={options.length > 0}
              autoComplete="off"
              autoFocus
              className="g-palette-field"
              inputMode={mode === 'link' ? 'url' : 'search'}
              onChange={(event) => { setQuery(event.target.value); setLinkError(null); }}
              onKeyDown={onKeyDown}
              placeholder={mode === 'link' ? 'Paste a YouTube, Twitch or Kick link' : 'Search titles, channels, videos, settings…'}
              ref={inputRef}
              role="combobox"
              spellCheck={false}
              value={query}
            />
          )}
        </Field>
        {loading ? <Spinner /> : null}
        <Kbd>esc</Kbd>
      </div>
      {shown.remoteFailed && settled ? <StatusText tone="attention">YouTube suggestions are taking a break. Vault and channel matches are still available.</StatusText> : null}
      {shown.local?.mode === 'lexical' && settled ? <StatusText tone="attention">Meaning-based matching is unavailable. Showing fast title, creator, and tag matches.</StatusText> : null}
      {failed ? <ErrorState onRetry={() => setRetry((value) => value + 1)} title="Search is unavailable right now." /> : null}
      {nothing ? <EmptyState body="Try a title, a person, a channel, or paste a link." title={`Nothing found for “${typed}”.`} /> : null}
      <div aria-label="Results" className="g-palette-list" id={listId} role="listbox">
        {groups.map((group) => {
          const rows = group.options.map((option) => (
            <Row activeId={current} key={option.id} listId={listId} onActivate={activate} onHover={setActiveId} option={option}>
              {option.kind === 'recent' ? <IconButton icon={<X />} label={`Remove “${option.label}” from recent searches`} onClick={(event) => { event.stopPropagation(); props.onRemoveHistory(option.entryId); }} /> : null}
            </Row>
          ));
          if (!group.label) return rows;
          const head = `${listId}-head-${group.id}`;
          return (
            <div aria-labelledby={head} className="g-palette-group" key={group.id} role="group">
              <div className="g-palette-group-head">
                <span className="g-label" id={head}>{group.label}</span>
                {group.id === 'recent' ? <TextButton onClick={props.onClearHistory}>Clear all</TextButton> : null}
              </div>
              {rows}
            </div>
          );
        })}
      </div>
      <p aria-hidden="true" className="g-palette-footer">{`↑↓ to move · ↵ to open · ${isApplePlatform() ? '⌘' : 'Ctrl'}↵ to open in Explore · esc to close`}</p>
      <p className="sr-only" id={hintId}>Arrow keys move through results. Enter opens. Command or Control Enter searches everything in Explore. Escape clears, then closes.</p>
      <div aria-live="polite" className="sr-only">{announcement}</div>
    </Dialog>
  );
}

function Row({ option, activeId, listId, onActivate, onHover, children }: { option: PaletteOption; activeId: string | null; listId: string; onActivate: (option: PaletteOption) => void; onHover: (id: string) => void; children?: ReactNode }) {
  const selected = option.id === activeId;
  const meta = 'meta' in option ? option.meta : option.kind === 'action' ? option.action.hint : undefined;
  return (
    <div aria-selected={selected} className="g-palette-option" id={`${listId}-${option.id}`} onClick={() => onActivate(option)} onPointerMove={() => onHover(option.id)} role="option">
      <span aria-hidden="true" className={`g-palette-thumb is-${option.kind}`}>{leading(option)}</span>
      <span className="g-palette-copy"><span className="g-palette-title">{option.label}</span>{meta ? <span className="g-palette-meta">{meta}</span> : null}</span>
      {children}
      {selected ? <Kbd>↵</Kbd> : null}
    </div>
  );
}

function leading(option: PaletteOption): ReactNode {
  if (option.kind === 'action') { const Icon = option.action.icon; return <Icon />; }
  if (option.kind === 'channel') return <ChannelAvatar name={option.label} size={32} url={option.channel.artwork_url} />;
  if (option.kind === 'title') {
    const square = option.title.type === 'album' || option.title.type === 'artist';
    const kind = square ? 'square' : 'poster';
    return <span className={`g-palette-art is-${kind}`}><GalleryArt alt="" art={option.title.poster} card={null} colour={cardColour(option.title, kind)} kind={kind} priority={3} sizes="64px" /></span>;
  }
  if (option.kind === 'video') return still(libraryThumbnail(option.item), option.item.id);
  if (option.kind === 'moment') return still(option.match.item ? libraryThumbnail(option.match.item) : null, option.match.id);
  if (option.kind === 'youtube') return still(option.result.artwork_url, option.result.webpage_url || option.result.id || option.label);
  return <Search />;
}

function still(url: string | null | undefined, key: string): ReactNode {
  return <span className="g-palette-art is-still"><GalleryArt alt="" art={remoteArt(url)} card={null} colour={remoteColour({ webpage_url: key })} kind="still" priority={3} sizes="64px" /></span>;
}

export default CommandPalette;
