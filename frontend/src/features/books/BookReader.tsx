import { ArrowLeft, Bookmark, BookOpen, Check, ChevronLeft, ChevronRight, List, Maximize, Minus, Plus, Settings2, X } from 'lucide-react';
import { createElement, type CSSProperties, type ReactNode, useCallback, useEffect, useRef, useState } from 'react';

import { getSessionUserId, trackKeepalive } from '../../api';
import { BrandMark, Button, ErrorState, IconButton, Select, Skeleton } from '../../ui';
import { bookCoverUrl, bookPageUrl, bookResourceUrl, getBookChapter, getBookReader, updateBookState } from './booksApi';
import type { Book, BookNode, BookReaderManifest } from './types';

type Appearance = { size: number; line: number; theme: 'system' | 'dark' | 'light' | 'sepia'; layout: 'single' | 'spread'; fit: 'height' | 'width' };
const DEFAULT_APPEARANCE: Appearance = { size: 19, line: 1.7, theme: 'system', layout: 'single', fit: 'height' };
const ALLOWED_TAGS = new Set(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'ul', 'ol', 'li', 'em', 'strong', 'span', 'br', 'hr']);
const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
const fragmentId = (anchor: string) => `book-fragment-${encodeURIComponent(anchor)}`;

function savedAppearance(userId: string): Appearance {
  try {
    const value = JSON.parse(localStorage.getItem(`lumina.books.appearance.${userId}`) ?? 'null') as Partial<Appearance> | null;
    return value ? {
      size: typeof value.size === 'number' ? clamp(value.size, 14, 30) : 19,
      line: typeof value.line === 'number' ? clamp(value.line, 1.4, 2.2) : 1.7,
      theme: ['system', 'dark', 'light', 'sepia'].includes(value.theme ?? '') ? value.theme! : 'system',
      layout: value.layout === 'spread' ? 'spread' : 'single', fit: value.fit === 'width' ? 'width' : 'height',
    } : DEFAULT_APPEARANCE;
  } catch { return DEFAULT_APPEARANCE; }
}

/** Only structured prose and local archive images can become elements. Archive HTML never reaches the DOM. */
export function BookProse({ nodes, bookId, onLink, depth = 0 }: { nodes: BookNode[]; bookId: string; onLink: (chapter: number | undefined, anchor: string | undefined) => void; depth?: number }) {
  if (depth > 40 || !Array.isArray(nodes)) return null;
  return <>{nodes.map((node, index): ReactNode => {
    if (!node || typeof node !== 'object') return null;
    const content = <>{typeof node.text === 'string' ? node.text : null}{node.children ? <BookProse bookId={bookId} depth={depth + 1} nodes={node.children} onLink={onLink} /> : null}</>;
    const id = node.id ? fragmentId(node.id) : undefined;
    if (node.tag === 'img') return node.member ? <img alt={node.alt ?? ''} decoding="async" id={id} key={index} loading="lazy" src={bookResourceUrl(bookId, node.member)} /> : null;
    if (node.tag === 'a') return typeof node.chapter === 'number' || node.anchor
      ? <a href={node.anchor ? `#${fragmentId(node.anchor)}` : '#book-prose'} id={id} key={index} onClick={(event) => { event.preventDefault(); onLink(node.chapter, node.anchor); }}>{content}</a>
      : <span key={index}>{content}</span>;
    return ALLOWED_TAGS.has(node.tag) ? createElement(node.tag, { key: index, id }, node.tag === 'br' || node.tag === 'hr' ? undefined : content) : <span key={index}>{content}</span>;
  })}</>;
}

function hasChapterContent(nodes: BookNode[], depth = 0): boolean {
  return depth <= 40 && Array.isArray(nodes) && nodes.some((node) => node && typeof node === 'object' && (
    (typeof node.text === 'string' && node.text.trim().length > 0)
    || (node.tag === 'img' && typeof node.member === 'string' && node.member.length > 0)
    || (node.children && hasChapterContent(node.children, depth + 1))
  ));
}

export function BookReader({ bookId, userId, onBack, onBookChange, onFileChange }: { bookId: string; userId: string; onBack: () => void; onBookChange: (book: Book) => void; onFileChange?: (id: string) => void }) {
  const [manifest, setManifest] = useState<BookReaderManifest | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [unit, setUnit] = useState(0);
  const [pageDraft, setPageDraft] = useState('1');
  const [nodes, setNodes] = useState<BookNode[] | null>(null);
  const [chapterError, setChapterError] = useState<string | null>(null);
  const [chapterRetry, setChapterRetry] = useState(0);
  const [contentsOpen, setContentsOpen] = useState(false);
  const [appearanceOpen, setAppearanceOpen] = useState(false);
  const [appearance, setAppearance] = useState(() => savedAppearance(userId));
  const [saveError, setSaveError] = useState<string | null>(null);
  const [imageError, setImageError] = useState(false);
  const [busy, setBusy] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const reading = useRef<HTMLDivElement>(null);
  const ready = useRef(false);
  const pendingTurn = useRef<boolean | null>(null);
  const turnPageRef = useRef<(forward: boolean) => void>(() => undefined);
  const restore = useRef<number | string>(0);
  const checkpoint = useRef({ unit_index: 0, offset: 0 });
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const writeQueue = useRef<Promise<unknown>>(Promise.resolve());
  const alive = useRef(true);
  const book = manifest?.book;
  const count = Math.max(1, book?.format !== 'epub' ? manifest?.page_count ?? 0 : manifest?.chapters.length ?? 0);

  useEffect(() => {
    const controller = new AbortController();
    setLoadError(null); setManifest(null); ready.current = false; pendingTurn.current = null;
    getBookReader(bookId, controller.signal).then((next) => {
      const total = next.book.format !== 'epub' ? next.page_count : next.chapters.length;
      const first = clamp(next.book.progress.unit_index, 0, Math.max(0, total - 1));
      checkpoint.current = { unit_index: first, offset: clamp(next.book.progress.offset, 0, 1) };
      restore.current = checkpoint.current.offset;
      setUnit(first); setManifest(next); setContentsOpen(window.innerWidth >= 1400 && next.book.format === 'epub');
    }).catch((error: unknown) => { if (!controller.signal.aborted) setLoadError(error instanceof Error ? error.message : 'The book could not be opened.'); });
    return () => controller.abort();
  }, [bookId, retry]);

  useEffect(() => setPageDraft(String(unit + 1)), [unit]);

  const persist = useCallback((keepalive = false, completed?: boolean) => {
    if (!ready.current || getSessionUserId() !== userId) return Promise.resolve(false);
    if (saveTimer.current) { clearTimeout(saveTimer.current); saveTimer.current = null; }
    const payload = { ...checkpoint.current, ...(completed === undefined ? {} : { completed }) };
    const send = () => getSessionUserId() === userId ? updateBookState(bookId, payload, keepalive) : Promise.resolve(null);
    const request = writeQueue.current.catch(() => undefined).then(send);
    writeQueue.current = request;
    if (keepalive) trackKeepalive(request);
    return request.then((updated) => {
      if (updated && alive.current) { setSaveError(null); setManifest((value) => value ? { ...value, book: updated } : value); onBookChange(updated); }
      return Boolean(updated);
    }).catch((error: unknown) => { if (alive.current) setSaveError(error instanceof Error ? error.message : 'Reading progress could not be saved.'); return false; });
  }, [bookId, onBookChange, userId]);
  const persistRef = useRef(persist);
  persistRef.current = persist;

  useEffect(() => {
    alive.current = true;
    const leave = () => { void persistRef.current(true); };
    window.addEventListener('pagehide', leave);
    return () => { leave(); alive.current = false; if (saveTimer.current) clearTimeout(saveTimer.current); window.removeEventListener('pagehide', leave); };
  }, [bookId]);

  useEffect(() => {
    if (!manifest) return;
    ready.current = false; setImageError(false); setChapterError(null); setNodes(null);
    if (manifest.book.format !== 'epub') {
      checkpoint.current = { unit_index: unit, offset: 0 };
      ready.current = true; void persistRef.current();
      return;
    }
    const controller = new AbortController();
    getBookChapter(bookId, unit, controller.signal).then((chapter) => setNodes(chapter.nodes)).catch((error: unknown) => {
      if (!controller.signal.aborted) setChapterError(error instanceof Error ? error.message : 'This chapter could not be loaded.');
    });
    return () => controller.abort();
  }, [bookId, manifest?.book.format, unit, chapterRetry]);

  useEffect(() => {
    if (manifest?.book.id) reading.current?.focus({ preventScroll: true });
  }, [manifest?.book.id]);

  useEffect(() => {
    if (!nodes || !reading.current) return;
    const handle = requestAnimationFrame(() => {
      const panel = reading.current;
      if (!panel) return;
      if (typeof restore.current === 'string') document.getElementById(fragmentId(restore.current))?.scrollIntoView({ block: 'start' });
      else panel.scrollTop = Math.max(0, panel.scrollHeight - panel.clientHeight) * restore.current;
      checkpoint.current = { unit_index: unit, offset: typeof restore.current === 'number' ? restore.current : 0 };
      restore.current = 0; ready.current = true; void persistRef.current();
      const pending = pendingTurn.current;
      pendingTurn.current = null;
      if (pending !== null) turnPageRef.current(pending);
    });
    return () => cancelAnimationFrame(handle);
  }, [nodes, unit]);

  useEffect(() => { try { localStorage.setItem(`lumina.books.appearance.${userId}`, JSON.stringify(appearance)); } catch { /* Reading works when browser storage is unavailable. */ } }, [appearance, userId]);

  function recordScroll() {
    if (!ready.current || book?.format !== 'epub' || !reading.current) return;
    const panel = reading.current;
    checkpoint.current = { unit_index: unit, offset: panel.scrollHeight > panel.clientHeight ? clamp(panel.scrollTop / (panel.scrollHeight - panel.clientHeight), 0, 1) : 0 };
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => { void persistRef.current(); }, 1000);
  }

  function turnPage(forward: boolean) {
    const panel = reading.current;
    if (book?.format === 'epub' && panel) {
      if (!ready.current) { pendingTurn.current = forward; return; }
      if (busy) return;
      const end = Math.max(0, panel.scrollHeight - panel.clientHeight);
      if ((forward && panel.scrollTop < end - 2) || (!forward && panel.scrollTop > 2)) {
        panel.scrollTop = clamp(panel.scrollTop + (forward ? 1 : -1) * Math.max(1, panel.clientHeight - 32), 0, end);
        recordScroll();
        return;
      }
      void go(forward ? next : previous, undefined, forward ? 0 : 1);
      return;
    }
    void go(forward ? next : previous);
  }

  turnPageRef.current = turnPage;

  async function go(next: number, anchor?: string, offset = 0, focusPage = true) {
    if (!manifest || next < 0 || next >= count || busy) return;
    setBusy(true);
    try {
      if (ready.current && !await persist()) return;
      restore.current = anchor ?? offset;
      checkpoint.current = { unit_index: next, offset };
      if (next === unit) {
        if (anchor) document.getElementById(fragmentId(anchor))?.scrollIntoView({ block: 'start' });
        else if (reading.current) reading.current.scrollTop = 0;
      }
      else setUnit(next);
      if (focusPage) {
        if (window.innerWidth < 1000) setContentsOpen(false);
        reading.current?.focus({ preventScroll: true });
      }
    } finally { setBusy(false); }
  }
  const spread = book?.format !== 'epub' && appearance.layout === 'spread';
  const spreadStart = spread && unit > 0 && unit % 2 === 0 ? unit - 1 : unit;
  const displayedPages = spread && spreadStart > 0 && spreadStart + 1 < count ? [spreadStart, spreadStart + 1] : [spreadStart];
  const previous = spread ? (spreadStart <= 1 ? 0 : spreadStart - 2) : unit - 1;
  const next = spread ? (spreadStart === 0 ? 1 : spreadStart + 2) : unit + 1;
  const atEnd = next >= count;
  const percent = Math.min(100, Math.round(((unit + checkpoint.current.offset) / count) * 100));

  if (loadError) return <div className="books-reader-recovery"><Button icon={<ArrowLeft />} onClick={onBack} variant="quiet">Back to Books</Button><ErrorState body={loadError} onRetry={() => setRetry((value) => value + 1)} title="This book could not be opened" /></div>;
  if (!manifest || !book) return <div className="books-reader-recovery"><Skeleton count={5} label="Opening your book…" shape="row" /></div>;

  return <div className={`books-reader is-${book.kind}`} data-appearance={appearance.theme} onKeyDown={(event) => {
    if (event.key === 'Escape') { setContentsOpen(false); setAppearanceOpen(false); reading.current?.focus({ preventScroll: true }); }
    if ((event.target as HTMLElement).closest('input, select, textarea, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="slider"], [role="combobox"], [role="listbox"], [role="menu"]') || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const forward = book.format !== 'epub' && manifest.reading_direction === 'rtl' ? 'ArrowLeft' : 'ArrowRight';
    if (event.key === forward) { event.preventDefault(); turnPage(true); }
    else if (event.key === (forward === 'ArrowLeft' ? 'ArrowRight' : 'ArrowLeft')) { event.preventDefault(); turnPage(false); }
  }} ref={root} style={{ '--book-font-size': `${appearance.size}px`, '--book-line-height': appearance.line } as CSSProperties}>
    <header className="books-reader-header">
      <IconButton icon={<ArrowLeft />} label="Back to Books" onClick={() => { void persist().then((saved) => { if (saved || !ready.current) onBack(); }); }} />
      <BrandMark compact={false} size="bar" />
      <h1 className="books-reader-title" tabIndex={-1}><span>Books / </span>{book.title}</h1>
      <div className="books-reader-tools">
        {onFileChange && book.files && book.files.length > 1 ? <div className="books-reader-format"><span className="sr-only">Read format</span><Select aria-label="Read format" disabled={busy} onChange={(event) => { const id = event.target.value; if (id === book.id) return; void persist().then((saved) => { if (saved || !ready.current) { ready.current = false; onFileChange(id); } }); }} value={book.id}>{book.files.map((file) => <option disabled={!file.available} key={file.id} value={file.id}>{file.format.toUpperCase()} · {file.filename}</option>)}</Select></div> : null}
        <IconButton aria-controls="book-contents" aria-expanded={contentsOpen} icon={<List />} label={book.format === 'epub' ? 'Chapters' : 'Pages'} onClick={() => { setContentsOpen((value) => !value); setAppearanceOpen(false); }} />
        <IconButton aria-controls="book-appearance" aria-expanded={appearanceOpen} icon={<Settings2 />} label="Reading appearance" onClick={() => { setAppearanceOpen((value) => !value); if (window.innerWidth < 1000) setContentsOpen(false); }} />
        <IconButton aria-pressed={book.reading_list} icon={<Bookmark />} label={book.reading_list ? 'Remove from reading list' : 'Add to reading list'} onClick={() => { void updateBookState(bookId, { reading_list: !book.reading_list }).then((updated) => { setManifest((value) => value ? { ...value, book: { ...updated, progress: value.book.progress, reading_file_id: value.book.id } } : value); onBookChange(updated); }).catch((error: unknown) => setSaveError(error instanceof Error ? error.message : 'Your reading list could not be updated.')); }} />
        <IconButton icon={<Maximize />} label="Toggle fullscreen" onClick={() => { void (document.fullscreenElement ? document.exitFullscreen() : root.current?.requestFullscreen())?.catch(() => setSaveError('Fullscreen is unavailable in this browser.')); }} />
      </div>
    </header>
    <div className={`books-reader-workspace${contentsOpen ? ' has-contents' : ''}${appearanceOpen ? ' has-appearance' : ''}`}>
      {contentsOpen ? <aside aria-label={book.format === 'epub' ? 'Table of contents' : 'Page navigation'} className="books-reader-aside" id="book-contents">
        <header><div><strong>{book.title}</strong><span>{book.author}</span></div><IconButton icon={<X />} label="Close contents" onClick={() => { setContentsOpen(false); reading.current?.focus({ preventScroll: true }); }} /></header>
        <h2>{book.format === 'epub' ? 'Contents' : 'Pages'}</h2>
        {book.format === 'epub' ? <ol>{manifest.chapters.map((chapter, index) => <li key={index}><button aria-current={unit === index ? 'location' : undefined} onClick={() => { void go(index); }} type="button"><span>{index + 1}</span>{chapter.label}</button></li>)}</ol> : <div className="books-reader-page-jump"><label htmlFor="book-page-input">Go to page</label><input id="book-page-input" max={count} min={1} onChange={(event) => setPageDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); const value = Number(pageDraft); if (Number.isInteger(value)) void go(value - 1); } }} type="number" value={pageDraft} /><Button disabled={!Number.isInteger(Number(pageDraft)) || Number(pageDraft) < 1 || Number(pageDraft) > count || busy} onClick={() => { void go(Number(pageDraft) - 1); }} variant="quiet">Go to page</Button><p>{count} pages · {manifest.reading_direction === 'rtl' ? 'Right to left' : 'Left to right'}</p></div>}
      </aside> : null}
      <div aria-label={book.format === 'epub' ? 'Book chapter' : book.kind === 'manga' ? 'Manga pages' : 'Book pages'} className={`books-reader-page is-${appearance.fit}`} onScroll={recordScroll} ref={reading} tabIndex={0}>
        {book.format === 'epub' ? chapterError ? <ErrorState body={chapterError} onRetry={() => setChapterRetry((value) => value + 1)} title="This chapter could not be loaded" /> : !nodes ? <Skeleton count={6} label="Loading chapter…" shape="row" /> : <article className={`books-prose${appearance.layout === 'spread' ? ' is-spread' : ''}`} id="book-prose"><p className="books-chapter-label">Chapter {unit + 1} of {count}</p>{unit === 0 && !hasChapterContent(nodes) && bookCoverUrl(book.cover_url) ? <img alt={`Cover of ${book.title}`} className="books-prose-cover" src={bookCoverUrl(book.cover_url)!} /> : null}<BookProse bookId={bookId} nodes={nodes} onLink={(chapter, anchor) => { void go(chapter ?? unit, anchor); }} /></article>
          : imageError ? <ErrorState body="The page image could not be loaded." onRetry={() => { setImageError(false); setChapterRetry((value) => value + 1); }} title="Page unavailable" /> : <div className={`books-manga-pages${displayedPages.length === 2 ? ' is-spread' : ''}`} dir={manifest.reading_direction}>{displayedPages.map((page) => <img alt={`Page ${page + 1} of ${count}`} decoding="async" key={`${page}:${chapterRetry}`} onError={() => setImageError(true)} src={bookPageUrl(bookId, page)} />)}</div>}
      </div>
      {appearanceOpen ? <aside aria-label="Reading appearance" className="books-reader-aside books-reader-appearance" id="book-appearance">
        <header><h2>Reading appearance</h2><IconButton icon={<X />} label="Close reading appearance" onClick={() => { setAppearanceOpen(false); reading.current?.focus({ preventScroll: true }); }} /></header>
        <fieldset><legend>Page color</legend><div className="books-appearance-choices">{(['system', 'dark', 'sepia', 'light'] as const).map((theme) => <button aria-pressed={appearance.theme === theme} key={theme} onClick={() => setAppearance((value) => ({ ...value, theme }))} type="button"><span className={`books-color-swatch is-${theme}`} />{theme[0].toUpperCase() + theme.slice(1)}</button>)}</div></fieldset>
        {book.format === 'epub' ? <><fieldset><legend>Text size</legend><div className="books-size-controls"><IconButton disabled={appearance.size <= 14} icon={<Minus />} label="Smaller text" onClick={() => setAppearance((value) => ({ ...value, size: value.size - 1 }))} /><output>{appearance.size}px</output><IconButton disabled={appearance.size >= 30} icon={<Plus />} label="Larger text" onClick={() => setAppearance((value) => ({ ...value, size: value.size + 1 }))} /></div></fieldset><label className="books-reader-range">Line spacing<input max="2.2" min="1.4" onChange={(event) => setAppearance((value) => ({ ...value, line: Number(event.target.value) }))} step="0.1" type="range" value={appearance.line} /></label></> : <fieldset><legend>Page fit</legend><div className="books-reader-options">{(['height', 'width'] as const).map((fit) => <button aria-pressed={appearance.fit === fit} key={fit} onClick={() => setAppearance((value) => ({ ...value, fit }))} type="button">Fit {fit}</button>)}</div></fieldset>}
        <fieldset><legend>Page layout</legend><div className="books-reader-options">{(['single', 'spread'] as const).map((layout) => <button aria-pressed={appearance.layout === layout} key={layout} onClick={() => setAppearance((value) => ({ ...value, layout }))} type="button">{layout === 'single' ? 'Single' : book.format === 'epub' ? 'Two columns' : 'Spread'}</button>)}</div></fieldset>
        <Button onClick={() => setAppearance(DEFAULT_APPEARANCE)} variant="quiet">Reset appearance</Button>
      </aside> : null}
    </div>
    <footer className={`books-reader-footer${contentsOpen ? ' has-contents' : ''}${appearanceOpen ? ' has-appearance' : ''}`}><div className="books-reader-footer-controls">
      <Button disabled={unit <= 0 || busy} icon={<ChevronLeft />} onClick={() => { void go(previous); }} variant="quiet">Previous</Button>
      <div className="books-reader-position"><label className="sr-only" htmlFor="book-position">Reading position</label><input id="book-position" max={count - 1} min={0} onChange={(event) => { void go(Number(event.target.value), undefined, 0, false); }} type="range" value={unit} /><span>{book.format !== 'epub' ? `Page ${displayedPages.map((page) => page + 1).join('–')} of ${count}` : `Chapter ${unit + 1} of ${count}`} · {book.progress.completed ? 'Read' : `${percent}% read`}</span></div>
      {atEnd ? <Button busy={busy} icon={book.progress.completed ? <Check /> : <BookOpen />} onClick={() => {
        setBusy(true);
        void persist(false, !book.progress.completed).then((saved) => {
          if (saved && book.progress.completed) {
            checkpoint.current = { unit_index: 0, offset: 0 }; restore.current = 0; setUnit(0);
            if (reading.current) reading.current.scrollTop = 0;
            void persist(false, false);
          }
        }).finally(() => setBusy(false));
      }} variant="primary">{book.progress.completed ? 'Read again' : 'Finish book'}</Button> : <Button disabled={busy} iconEnd={<ChevronRight />} onClick={() => { void go(next); }} variant="quiet">Next</Button>}
      </div>{saveError ? <div className="books-reader-save-error" role="alert"><span>{saveError}</span><Button onClick={() => { void persist(); }} variant="quiet">Retry save</Button></div> : null}
    </footer>
  </div>;
}
