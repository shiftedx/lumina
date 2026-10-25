import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Bookmark, BookOpen, Check, Ellipsis, Heart, Library, Plus, Search, SlidersHorizontal } from '../../luminaIcons';
import { type ReactNode, lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import type { UserProfile } from '../../types';
import { BlockedSurface, useAccess, useCanDownload } from '../access/access';
import { Button, ConfirmDialog, EmptyState, ErrorState, IconButton, Masthead, Menu, Select, Skeleton, TabScroller, type MenuEntry } from '../../ui';
import { useFetched } from '../titles/titleModel';
import { AddToShelfDialog, BookMetadataEditor, ImportBooksDialog, ShelfEditor } from './BooksDialogs';
import { BookCard, BookCover, readingPosition, SeriesCard, ShelfTile } from './BookCards';
import { BookDetail } from './BookDetail';
import { BookReader } from './BookReader';
import { booksRoutePath, type BooksRoute } from './routes';
import { deleteBook, deleteBookShelf, getBook, getBookSeries, getBooksOverview, listBooks, listBookSeries, setBookShelfEntries, separateBookFile, updateBookShelf, updateBookState, type BooksQuery } from './booksApi';
import type { Book, BookPage, BookSeries, BookShelf, BookSort, BookStateUpdate } from './types';
import './books.css';

const CombineBooksDialog = lazy(() => import('./BooksManagementDialogs').then((module) => ({ default: module.CombineBooksDialog })));
const BooksTrashDialog = lazy(() => import('./BooksManagementDialogs').then((module) => ({ default: module.BooksTrashDialog })));

const TABS = [['overview', 'Overview'], ['ebooks', 'Ebooks'], ['manga', 'Manga'], ['shelves', 'Shelves'], ['discover', 'Discover']] as const;
const ERROR = (error: unknown) => error instanceof Error ? error.message : 'Books could not be loaded. Please try again.';
const titleRoute = (book: Book): BooksRoute => book.kind === 'manga' && book.series_id ? { surface: 'books', view: 'series', seriesId: book.series_id } : { surface: 'books', view: 'title', bookId: book.id };
const count = (value: number, singular: string, plural = `${singular}s`) => `${value.toLocaleString()} ${value === 1 ? singular : plural}`;

/** One recently added cover per manga series keeps volumes from crowding out ebooks. */
function readingCovers(books: Book[]): Book[] {
  const series = new Set<string>();
  return books.filter((book) => {
    if (book.kind !== 'manga' || !book.series_id) return true;
    if (series.has(book.series_id)) return false;
    series.add(book.series_id); return true;
  });
}

type BooksSurfaceProps = { user: UserProfile; route: BooksRoute; onRoute: (route: BooksRoute, replace?: boolean) => void };
export function BooksSurface(props: BooksSurfaceProps) {
  const { access } = useAccess();
  if (props.user.role !== 'admin' && access?.sections && !access.sections.includes('books')) return <div className="surface books-surface"><Masthead title="Books" /><BlockedSurface what="Books" />{props.route.view === 'read' ? <Button icon={<ArrowLeft />} onClick={() => props.onRoute({ surface: 'books', view: 'overview' })} variant="quiet">Back to Books</Button> : null}</div>;
  return <BooksContent {...props} />;
}

function BooksContent({ user, route, onRoute }: BooksSurfaceProps) {
  const canImport = useCanDownload();
  const [revision, setRevision] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [shelfEditor, setShelfEditor] = useState<BookShelf | 'new' | null>(null);
  const [shelfToDelete, setShelfToDelete] = useState<BookShelf | null>(null);
  const [shelfIds, setShelfIds] = useState<string[] | null>(null);
  const [trashOpen, setTrashOpen] = useState(false);
  const [combinePreset, setCombinePreset] = useState<Book[]>([]);
  const [combineBook, setCombineBook] = useState<Book | null>(null);
  const [fileToSeparate, setFileToSeparate] = useState<string | null>(null);
  const [bookToDelete, setBookToDelete] = useState<Book | null>(null);
  const [metadataBook, setMetadataBook] = useState<Book | null>(null);
  const [importFiles, setImportFiles] = useState<File[] | null>(null);
  const [moreBooks, setMoreBooks] = useState<Book[]>([]);
  const [moreSeries, setMoreSeries] = useState<BookSeries[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [search, setSearch] = useState('q' in route ? route.q ?? '' : '');
  const [shelfFormat, setShelfFormat] = useState<'all' | 'ebook' | 'manga'>('all');
  const input = useRef<HTMLInputElement>(null);
  const generation = useRef(0);
  const lastListRoute = useRef<BooksRoute>({ surface: 'books', view: 'overview' });
  const lastReaderRoute = useRef<BooksRoute>({ surface: 'books', view: 'overview' });
  const routeKey = booksRoutePath(route);
  const listView = route.view !== 'read' && route.view !== 'title' && route.view !== 'series';
  const filters = listView ? route : null;
  const isShelfList = route.view === 'shelves' && (Boolean(route.shelfId) || Boolean(route.status));
  const listActive = route.view === 'ebooks' || route.view === 'discover' || isShelfList;
  const query: BooksQuery = filters ? { q: filters.q, sort: filters.sort ?? (route.view === 'shelves' && route.shelfId ? 'custom' : undefined), status: route.view === 'discover' ? filters.status ?? 'unread' : filters.status, ...(route.view === 'ebooks' ? { kind: 'ebook' } : {}), ...(route.view === 'shelves' ? { shelf_id: route.shelfId, ...(shelfFormat === 'all' ? {} : { kind: shelfFormat }) } : {}), limit: 48 } : {};
  const overviewActive = route.view === 'overview' || route.view === 'shelves' || shelfIds !== null;
  const overview = useFetched(overviewActive ? `${user.id}:books-overview` : null, getBooksOverview, revision);
  const books = useFetched(listActive ? `${user.id}:${routeKey}:${shelfFormat}` : null, () => listBooks(query), revision);
  const series = useFetched(route.view === 'manga' ? `${user.id}:${routeKey}:series` : null, () => listBookSeries({ q: filters?.q, sort: filters?.sort, status: filters?.status, limit: 48 }), revision);
  const detail = useFetched(route.view === 'title' ? `${user.id}:book:${route.bookId}` : null, () => getBook(route.view === 'title' ? route.bookId : ''), revision);
  const seriesDetail = useFetched(route.view === 'series' ? `${user.id}:series:${route.seriesId}` : null, () => getBookSeries(route.view === 'series' ? route.seriesId : ''), revision);
  const related = useFetched(route.view === 'title' && detail.data?.author ? `${user.id}:related:${detail.data.author}` : null, () => listBooks({ q: detail.data?.author, kind: detail.data?.kind, sort: 'title', limit: 12 }), revision);
  const shelves = overview.data?.shelves ?? [];
  const activeShelf = route.view === 'shelves' && route.shelfId ? shelves.find((shelf) => shelf.id === route.shelfId) : undefined;
  const allBooks = [...(books.data?.items ?? []), ...moreBooks];
  const allSeries = [...(series.data?.items ?? []), ...moreSeries];
  const refreshed = useCallback(() => setRevision((value) => value + 1), []);
  const bookChanged = useCallback(() => { setRevision((value) => value + 1); }, []);

  useEffect(() => {
    generation.current += 1; setMoreBooks([]); setMoreSeries([]); setNextCursor(null); setSelected(new Set()); setSelecting(false); setLoadingMore(false); setMutationError(null); setBusy(false);
    if (listView) lastListRoute.current = route;
    setSearch('q' in route ? route.q ?? '' : '');
  }, [routeKey, user.id]); // The canonical route owns the view/filter reset.
  useEffect(() => { setNextCursor((route.view === 'manga' ? series.data : books.data)?.next_cursor ?? null); setMoreBooks([]); setMoreSeries([]); }, [books.data, series.data, route.view]);

  useEffect(() => {
    if (route.view === 'title' && detail.data?.id === route.bookId && detail.data.canonical_book_id && detail.data.canonical_book_id !== route.bookId) {
      onRoute({ surface: 'books', view: 'title', bookId: detail.data.canonical_book_id }, true);
    }
  }, [routeKey, detail.data, onRoute]);

  function open(next: BooksRoute, replace = false) { setNotice(null); setMutationError(null); onRoute(next, replace); }
  function openBook(book: Book) { open(titleRoute(book)); }
  function read(book: Book) {
    const fileId = book.reading_file_id || book.files?.find((file) => file.format === 'epub')?.id || book.id;
    const begin = () => { lastReaderRoute.current = route; open({ surface: 'books', view: 'read', bookId: fileId }); };
    if (book.progress.completed) void mutate(() => updateBookState(fileId, { completed: false, unit_index: 0, offset: 0 }), '').then((saved) => { if (saved) begin(); });
    else begin();
  }
  function readId(id: string) { lastReaderRoute.current = route; open({ surface: 'books', view: 'read', bookId: id }); }
  function shelfOpen(shelf: BookShelf) { open({ surface: 'books', view: 'shelves', shelfId: shelf.id }); }
  function beginCombine(book: Book, preset: Book[] = []) { setCombinePreset(preset); setCombineBook(book); }
  function select(id: string) { setSelected((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; }); }
  async function mutate(action: () => Promise<unknown>, success: string) {
    if (busy) return false;
    const current = generation.current;
    setBusy(true); setMutationError(null);
    try { await action(); if (current === generation.current) { refreshed(); setNotice(success); } return true; }
    catch (error) { if (current === generation.current) setMutationError(ERROR(error)); return false; }
    finally { if (current === generation.current) setBusy(false); }
  }
  function state(book: Book, update: BookStateUpdate) { void mutate(() => updateBookState(book.id, update), update.favorite !== undefined ? (update.favorite ? 'Added to favorites.' : 'Removed from favorites.') : update.reading_list !== undefined ? (update.reading_list ? 'Added to your reading list.' : 'Removed from your reading list.') : update.completed ? 'Marked as read.' : 'Marked as unread.'); }

  const card = (book: Book, extra: MenuEntry[] = [], onOpen = openBook) => <BookCard book={book} extra={[...extra, ...(book.can_edit ? [
    { kind: 'item' as const, label: 'Combine entries', onSelect: () => beginCombine(book) },
    { kind: 'item' as const, label: 'Move to trash', onSelect: () => setBookToDelete(book) },
  ] : [])]} key={book.id} onOpen={onOpen} onRead={read} onSelect={select} onShelf={setShelfIds} onState={state} selected={selected.has(book.id)} selecting={selecting} />;
  function section(title: string, items: Book[], seeAll?: BooksRoute, className = '') {
    if (!items.length) return null;
    return <section className={`books-section ${className}`}><header><h2>{title}</h2>{seeAll ? <Button iconEnd={<ArrowRight />} onClick={() => open(seeAll)} variant="quiet">View all</Button> : null}</header><div className="books-cover-grid">{items.map((book) => card(book))}</div></section>;
  }
  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    const current = generation.current;
    setLoadingMore(true); setMutationError(null);
    try {
      if (route.view === 'manga') {
        const page = await listBookSeries({ q: route.q, sort: route.sort, status: route.status, cursor: nextCursor, limit: 48 });
        if (current === generation.current) { setMoreSeries((items) => [...items, ...page.items]); setNextCursor(page.next_cursor); }
      } else {
        const page = await listBooks({ ...query, cursor: nextCursor });
        if (current === generation.current) { setMoreBooks((items) => [...items, ...page.items]); setNextCursor(page.next_cursor); }
      }
    } catch (error) { if (current === generation.current) setMutationError(ERROR(error)); }
    finally { if (current === generation.current) setLoadingMore(false); }
  }
  function updateFilter(change: { q?: string; sort?: BookSort; status?: BooksQuery['status'] }) {
    if (filters) open({ ...filters, ...change }, true);
  }
  const toolbar = <div className="books-toolbar">
    <form className="books-search" onSubmit={(event) => { event.preventDefault(); updateFilter({ q: search.trim() || undefined }); }}><Search aria-hidden="true" /><label className="sr-only" htmlFor="books-search">Search books, authors, and series</label><input id="books-search" maxLength={200} onChange={(event) => setSearch(event.target.value)} placeholder="Find a title, author, or series…" type="search" value={search} /><Button type="submit" variant="quiet">Search</Button></form>
    <div className="books-sort"><span>Sort</span><Select aria-label="Sort" onChange={(event) => updateFilter({ sort: event.target.value as BookSort })} value={filters?.sort ?? (activeShelf ? 'custom' : 'added')}>{activeShelf ? <option value="custom">Custom order</option> : null}<option value="added">Recently added</option><option value="title">Title</option><option value="author">Author</option></Select></div>
    <div className="books-status"><SlidersHorizontal aria-hidden="true" /><span className="sr-only">Reading status</span><Select aria-label="Reading status" onChange={(event) => updateFilter({ status: event.target.value as BooksQuery['status'] })} value={filters?.status ?? (route.view === 'discover' ? 'unread' : 'all')}><option value="all">All stories</option><option value="unread">Unread</option><option value="reading">Reading</option><option value="read">Read</option><option value="favorites">Favorites</option><option value="reading_list">Reading list</option></Select></div>
    {route.view !== 'manga' ? <Button onClick={() => { setSelecting((value) => !value); setSelected(new Set()); }} pressed={selecting} variant="quiet">{selecting ? 'Done selecting' : 'Select'}</Button> : null}
  </div>;
  const dataState = <T,>(resource: { data: BookPage<T> | null; loading: boolean; error: unknown }, children: ReactNode) => resource.error ? <ErrorState body={ERROR(resource.error)} onRetry={refreshed} title="Your collection could not be loaded" /> : !resource.data ? <Skeleton count={6} label="Loading your books…" shape="poster" /> : resource.data.total === 0 ? <EmptyState action={canImport ? <Button icon={<Plus />} onClick={() => input.current?.click()} variant="quiet">Import books</Button> : undefined} body={filters?.q || (filters?.status && filters.status !== 'all') ? 'Try a different search or reading status.' : canImport ? 'Import an EPUB or CBZ to begin your collection.' : 'Book imports are not available on this account.'} size="page" title={filters?.q ? 'No matching stories' : 'This shelf is waiting for its first story'} /> : children;

  if (route.view === 'read') return <BookReader bookId={route.bookId} key={`${user.id}:${route.bookId}`} onBack={() => open(lastReaderRoute.current)} onBookChange={bookChanged} onFileChange={(id) => open({ surface: 'books', view: 'read', bookId: id })} userId={user.id} />;

  let body: ReactNode;
  let heading = 'Books';
  let description: string | undefined;
  let meta: string | undefined;
  let actions: ReactNode = <><Button onClick={() => setTrashOpen(true)} variant="quiet">Trash</Button>{canImport ? <Button icon={<Plus />} onClick={() => input.current?.click()} variant="quiet">Import</Button> : null}</>;

  if (route.view === 'overview') {
    const data = overview.data;
    const continuing = data?.continue_reading.slice(0, 3) ?? [];
    const readingIds = new Set(continuing.map((book) => book.id));
    const recent = readingCovers(data?.recently_added ?? []).filter((book) => !readingIds.has(book.id)).slice(0, 8);
    const storyKey = (book: Book) => book.kind === 'manga' && book.series_id ? book.series_id : book.id;
    const recentStories = new Set(recent.map(storyKey));
    const discoveries = readingCovers(data?.discover ?? []).filter((book) => !recentStories.has(storyKey(book))).slice(0, 8);
    const sparse = recent.length > 0 && recent.length <= 3 && discoveries.length <= 3 && (data?.shelves.length ?? 0) <= 3;
    meta = data ? `${count(data.totals.ebooks, 'ebook')} · ${count(data.totals.manga_series, 'manga series', 'manga series')}` : undefined;
    body = overview.error ? <ErrorState body={ERROR(overview.error)} onRetry={refreshed} title="Books could not be loaded" /> : !data ? <Skeleton count={6} label="Loading Books…" shape="poster" /> : !data.totals.ebooks && !data.totals.volumes ? <EmptyState action={canImport ? <Button icon={<Plus />} onClick={() => input.current?.click()} variant="primary">Import your first books</Button> : undefined} body={canImport ? "Bring your EPUB and PDF ebooks and CBZ and CBR manga into a library made for reading. Keep favorites, arrange personal shelves, and continue your stories on any device." : "Shared household books will appear here. Book imports are not available on this account."} size="page" title="A home for every story" /> : <div className={`books-overview${sparse ? ' is-sparse' : ''}${continuing.length ? ' has-reading' : ''}`}>
      {continuing.length ? <section className="books-section books-overview-reading"><header><h2>Continue reading</h2><Button iconEnd={<ArrowRight />} onClick={() => open({ surface: 'books', view: 'shelves', status: 'reading' })} variant="quiet">View all</Button></header><div className="books-continue-grid">{continuing.map((book) => <article className="books-continue" key={book.id}><button aria-label={`Open ${book.title}`} className="books-cover-button" onClick={() => openBook(book)} type="button"><BookCover title={book.title} url={book.cover_url} /></button><div><h3>{book.kind === 'manga' ? book.series_name || book.title : book.title}</h3><p>{book.author}</p><span>{book.kind === 'manga' && book.volume_number !== null ? `Volume ${book.volume_number} · ` : ''}{readingPosition(book)}</span><progress aria-label={`${book.title} reading progress`} max={100} value={book.progress.percent} /><Button disabled={!book.available} icon={<BookOpen />} onClick={() => read(book)} variant="primary">Continue reading</Button></div></article>)}</div></section> : null}
      {section('Recently added', recent, { surface: 'books', view: 'shelves', status: 'all', sort: 'added' }, 'books-overview-additions')}
      {data.shelves.length ? <section className="books-section books-overview-shelves"><header><h2>Your shelves</h2><Button iconEnd={<ArrowRight />} onClick={() => open({ surface: 'books', view: 'shelves' })} variant="quiet">Manage shelves</Button></header><div className="books-shelves-grid">{data.shelves.slice(0, 4).map((shelf) => <ShelfTile key={shelf.id} onOpen={shelfOpen} shelf={shelf} />)}</div></section> : <section className="books-first-shelf"><div><h2>Make room for your favorites</h2><p>A shelf can hold both ebooks and manga.</p></div><Button icon={<Plus />} onClick={() => setShelfEditor('new')} variant="quiet">Create a shelf</Button></section>}
      {section('Rediscover your collection', discoveries, { surface: 'books', view: 'discover' }, 'books-overview-discover')}
    </div>;
  } else if (route.view === 'title') {
    const book = detail.data;
    heading = book?.title ?? 'Book'; actions = undefined;
    body = detail.error ? <ErrorState body={ERROR(detail.error)} onRetry={refreshed} title="This book is unavailable" /> : !book ? <Skeleton count={4} label="Loading book details…" shape="row" /> : <><BookDetail book={book} onRead={read} onReadFile={readId} onState={state} onShelf={() => setShelfIds([book.id])} onEdit={() => setMetadataBook(book)} onCombine={() => beginCombine(book)} onSeparate={setFileToSeparate} onDelete={() => setBookToDelete(book)} onSeries={() => { if (book.series_id) open({ surface: 'books', view: 'series', seriesId: book.series_id }); }} />{section(`More by ${book.author}`, (related.data?.items ?? []).filter((item) => item.id !== book.id && item.author === book.author).slice(0, 8), undefined, 'books-related')}</>;
  } else if (route.view === 'series') {
    const data = seriesDetail.data;
    heading = data?.series.title ?? 'Manga series'; actions = undefined;
    const next = data?.books.find((book) => book.id === data.series.next_book_id);
    body = seriesDetail.error ? <ErrorState body={ERROR(seriesDetail.error)} onRetry={refreshed} title="This series is unavailable" /> : !data ? <Skeleton count={5} label="Loading manga series…" shape="poster" /> : <><div className="books-detail"><BookCover author={data.series.author} title={data.series.title} url={data.series.cover_url} /><div className="books-detail-copy"><p className="books-detail-author">{data.series.author || 'Unknown author'}</p><p className="books-detail-facts">{count(data.series.volume_count, 'volume')} in your collection · {data.series.read_volume_count} read</p>{data.series.description ? <p className="books-description">{data.series.description}</p> : null}<div className="books-detail-actions">{next ? <Button disabled={!next.available} icon={<BookOpen />} onClick={() => read(next)} variant="primary">{next.progress.updated_at ? 'Continue' : 'Read'}{next.volume_number !== null ? ` volume ${next.volume_number}` : ' next volume'}</Button> : data.books[0] ? <Button icon={<BookOpen />} onClick={() => read(data.books[0])} variant="primary">Read again</Button> : null}<Button icon={<Plus />} onClick={() => setShelfIds(data.books.map((book) => book.id))} variant="quiet">Add series to shelf</Button></div>{next ? <p className="books-detail-position">{readingPosition(next)} · {next.reading_direction === 'rtl' ? 'Right to left' : 'Left to right'}</p> : null}</div></div><section className="books-section"><header><h2>Volumes</h2><span>In your collection · Volume order</span></header><div className="books-cover-grid">{data.books.map((book) => <div className="books-volume" key={book.id}>{card(book, book.can_edit ? [{ kind: 'item', label: 'Edit volume details', onSelect: () => setMetadataBook(book) }] : [], (volume) => open({ surface: 'books', view: 'title', bookId: volume.id }))}<span className="books-volume-status">{book.progress.completed ? <Check aria-hidden="true" /> : null}{readingPosition(book)}</span></div>)}</div></section></>;
  } else if (route.view === 'manga') {
    heading = 'Manga'; meta = series.data ? count(series.data.total, 'series', 'series') : undefined;
    body = <>{toolbar}{dataState(series, <div className="books-cover-grid">{allSeries.map((item) => <SeriesCard key={item.id} onOpen={(series) => open({ surface: 'books', view: 'series', seriesId: series.id })} onRead={readId} series={item} />)}</div>)}</>;
  } else if (route.view === 'shelves') {
    heading = activeShelf?.name ?? (route.status === 'favorites' ? 'Favorites' : route.status === 'reading_list' ? 'Reading list' : route.status === 'reading' ? 'Currently reading' : route.status === 'all' ? 'All books' : 'Your shelves');
    description = activeShelf?.description || undefined;
    meta = activeShelf ? `${activeShelf.visibility === 'shared' ? 'Household' : 'Private'} · ${count(activeShelf.count, 'book')}` : undefined;
    actions = activeShelf?.can_edit ? <><Button busy={busy} onClick={() => { void mutate(() => updateBookShelf(activeShelf.id, { visibility: activeShelf.visibility === 'shared' ? 'private' : 'shared' }), activeShelf.visibility === 'shared' ? 'Shelf is private.' : 'Shelf shared with household.'); }} variant="quiet">{activeShelf.visibility === 'shared' ? 'Make private' : 'Share with household'}</Button><Menu items={[{ kind: 'item', label: 'Edit shelf', onSelect: () => setShelfEditor(activeShelf) }, { kind: 'item', label: 'Delete shelf', danger: true, onSelect: () => setShelfToDelete(activeShelf) }]} trigger={(props) => <button {...props} aria-label="Shelf actions" className="g-icon-button" type="button"><Ellipsis aria-hidden="true" /></button>} /></> : <Button icon={<Plus />} onClick={() => setShelfEditor('new')} variant="quiet">New shelf</Button>;
    const navigator = <aside aria-label="Personal shelves" className="books-shelf-navigation"><h2>Your shelves</h2><button aria-current={route.status === 'all' && !route.shelfId ? 'page' : undefined} onClick={() => open({ surface: 'books', view: 'shelves', status: 'all' })} type="button"><BookOpen aria-hidden="true" />All books</button><button aria-current={route.status === 'reading_list' && !route.shelfId ? 'page' : undefined} onClick={() => open({ surface: 'books', view: 'shelves', status: 'reading_list' })} type="button"><Bookmark aria-hidden="true" />Reading list</button><button aria-current={route.status === 'favorites' && !route.shelfId ? 'page' : undefined} onClick={() => open({ surface: 'books', view: 'shelves', status: 'favorites' })} type="button"><Heart aria-hidden="true" />Favorites</button>{shelves.map((shelf) => <button aria-current={activeShelf?.id === shelf.id ? 'page' : undefined} key={shelf.id} onClick={() => shelfOpen(shelf)} type="button"><Library aria-hidden="true" /><span>{shelf.name}</span><small>{shelf.count}</small></button>)}<Button icon={<Plus />} onClick={() => setShelfEditor('new')} variant="quiet">New shelf</Button></aside>;
    body = <div className="books-shelf-workspace">{navigator}<div className="books-shelf-content">{isShelfList || route.status === 'reading' ? <><div aria-label="Shelf formats" className="books-format-switch">{([['all', 'All'], ['ebook', 'Ebooks'], ['manga', 'Manga']] as const).map(([format, label]) => <button aria-pressed={shelfFormat === format} key={format} onClick={() => { setShelfFormat(format); setMoreBooks([]); setSelected(new Set()); }} type="button">{label}</button>)}</div>{toolbar}{dataState(books, <div className="books-cover-grid">{allBooks.map((book) => {
      const index = activeShelf?.book_ids.indexOf(book.id) ?? -1;
      const shift = (step: number) => { if (!activeShelf) return; const ids = [...activeShelf.book_ids]; [ids[index], ids[index + step]] = [ids[index + step], ids[index]]; void mutate(() => setBookShelfEntries(activeShelf.id, ids), 'Shelf order updated.'); };
      return card(book, activeShelf?.can_edit ? [
        { kind: 'separator' }, { kind: 'item', label: 'Move earlier', icon: <ArrowUp />, disabled: index <= 0, onSelect: () => shift(-1) }, { kind: 'item', label: 'Move later', icon: <ArrowDown />, disabled: index < 0 || index >= activeShelf.book_ids.length - 1, onSelect: () => shift(1) },
        { kind: 'item', label: 'Remove from this shelf', onSelect: () => { void mutate(() => setBookShelfEntries(activeShelf.id, activeShelf.book_ids.filter((id) => id !== book.id)), 'Removed from shelf.'); } },
      ] : []);
    })}</div>)}</> : overview.error ? <ErrorState body={ERROR(overview.error)} onRetry={refreshed} title="Shelves could not be loaded" /> : !overview.data ? <Skeleton count={3} label="Loading shelves…" shape="row" /> : !shelves.length ? <EmptyState action={<Button icon={<Plus />} onClick={() => setShelfEditor('new')} variant="primary">Create a shelf</Button>} body="Build a reading list for quiet evenings, collect favorite series, or share a shelf with your household." size="page" title="Your collection, your way" /> : <div className="books-shelves-grid">{shelves.map((shelf) => <ShelfTile key={shelf.id} onOpen={shelfOpen} shelf={shelf} />)}</div>}</div></div>;
  } else {
    heading = route.view === 'discover' ? 'Discover' : 'Ebooks';
    description = route.view === 'discover' ? 'Unread books from your collection.' : undefined;
    meta = books.data ? count(books.data.total, 'book') : undefined;
    body = <>{toolbar}{dataState(books, <div className="books-cover-grid">{allBooks.map((book) => card(book))}</div>)}</>;
  }

  async function bulkRead() {
    const ids = [...selected];
    const succeeded = await mutate(async () => {
      const results = await Promise.allSettled(ids.map((id) => updateBookState(id, { completed: true })));
      const failures = results.flatMap((result, index) => result.status === 'rejected' ? [ids[index]] : []);
      if (failures.length) { refreshed(); setSelected(new Set(failures)); throw new Error(`${failures.length} ${failures.length === 1 ? 'book could' : 'books could'} not be marked as read. Retry the selected books.`); }
    }, `${count(ids.length, 'book')} marked as read.`);
    if (succeeded) { setSelected(new Set()); setSelecting(false); }
  }

  return <div className="surface books-surface">
    <input accept=".epub,.pdf,.cbz,.cbr" aria-label="Choose EPUB, PDF, CBZ, and CBR files" className="sr-only" disabled={!canImport} multiple onChange={(event) => { const files = Array.from(event.target.files ?? []).slice(0, 100); if (canImport && files.length) setImportFiles(files); event.target.value = ''; }} ref={input} tabIndex={-1} type="file" />
    {route.view === 'title' || route.view === 'series' ? <Button className="books-back" icon={<ArrowLeft />} onClick={() => open(lastListRoute.current)} variant="quiet">Back to Books</Button> : null}
    {route.view !== 'title' ? <Masthead actions={actions} lede={description} meta={meta} title={heading} /> : null}
    {listView ? <TabScroller activeKey={route.view} ariaLabel="Books" as="nav" rowClassName="g-tabs books-tabs">{TABS.map(([view, label]) => <a aria-current={route.view === view ? 'page' : undefined} href={booksRoutePath({ surface: 'books', view })} key={view} onClick={(event) => { if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) { event.preventDefault(); open({ surface: 'books', view }); } }}>{label}</a>)}</TabScroller> : null}
    {notice ? <p className="books-notice" role="status">{notice}</p> : null}
    {mutationError ? <p className="books-form-error" role="alert">{mutationError}</p> : null}
    {body}
    {nextCursor && (listActive || route.view === 'manga') ? <div className="books-load-more"><Button busy={loadingMore} onClick={() => { void loadMore(); }}>Load more</Button></div> : null}
    {selecting ? <div aria-label="Selected books" className="books-bulk-bar"><strong>{selected.size} selected</strong><Button disabled={selected.size < 2 || busy || [...selected].some((id) => !allBooks.find((book) => book.id === id)?.can_edit)} onClick={() => { const chosen = [...selected].map((id) => allBooks.find((book) => book.id === id)).filter((book): book is Book => Boolean(book)); if (chosen[0]) beginCombine(chosen[0], chosen); }} variant="quiet">Combine entries</Button><Button disabled={!selected.size || busy} icon={<Plus />} onClick={() => setShelfIds([...selected])} variant="quiet">Add to shelf</Button><Button busy={busy} disabled={!selected.size} icon={<Check />} onClick={() => { void bulkRead(); }} variant="quiet">Mark as read</Button>{activeShelf?.can_edit ? <Button disabled={!selected.size || busy} onClick={() => { void mutate(() => setBookShelfEntries(activeShelf.id, activeShelf.book_ids.filter((id) => !selected.has(id))), 'Removed selected books from shelf.').then((done) => { if (done) setSelected(new Set()); }); }} variant="quiet">Remove from shelf</Button> : null}<Button onClick={() => { setSelecting(false); setSelected(new Set()); }} variant="primary">Done</Button></div> : null}
    {importFiles ? <ImportBooksDialog files={importFiles} onClose={() => setImportFiles(null)} onImported={() => { refreshed(); setNotice('Books imported into your private collection.'); }} /> : null}
    {shelfEditor ? <ShelfEditor onClose={() => setShelfEditor(null)} onSaved={(shelf) => { setShelfEditor(null); refreshed(); if (!shelfIds) shelfOpen(shelf); setNotice('Shelf saved.'); }} shelf={shelfEditor === 'new' ? undefined : shelfEditor} /> : null}
    {shelfIds && !shelfEditor ? <AddToShelfDialog ids={shelfIds} onClose={() => setShelfIds(null)} onCreate={() => setShelfEditor('new')} onSaved={() => { setShelfIds(null); refreshed(); setNotice('Added to your shelves.'); }} shelves={shelves} /> : null}
    <Suspense fallback={null}>{combineBook ? <CombineBooksDialog book={combineBook} initialBooks={combinePreset} onClose={() => setCombineBook(null)} onCombined={(combined) => { setCombineBook(null); refreshed(); open({ surface: 'books', view: 'title', bookId: combined.id }); setNotice('Entries combined. All files are retained.'); }} /> : null}
    {trashOpen ? <BooksTrashDialog onClose={() => setTrashOpen(false)} onRestored={refreshed} /> : null}</Suspense>
    <ConfirmDialog body="This entry will leave your library. Its files and reading progress stay in Trash, where you can restore it." busy={busy} confirmLabel="Move to trash" onCancel={() => setBookToDelete(null)} onConfirm={() => { if (bookToDelete) void mutate(() => deleteBook(bookToDelete.id), 'Book moved to trash.').then((saved) => { if (saved) { setBookToDelete(null); open(lastListRoute.current); setNotice('Book moved to trash.'); } }); }} open={Boolean(bookToDelete)} title="Move this book to trash?" />
    <ConfirmDialog body="This file will become its own catalog entry. Its original metadata, reading position, and shelf membership are retained." busy={busy} confirmLabel="Separate entry" onCancel={() => setFileToSeparate(null)} onConfirm={() => { if (fileToSeparate) { const id = fileToSeparate; void mutate(() => separateBookFile(id), 'File separated into its own entry.').then((saved) => { if (saved) { setFileToSeparate(null); open({ surface: 'books', view: 'title', bookId: id }); setNotice('File separated into its own entry.'); } }); } }} open={Boolean(fileToSeparate)} title="Separate this file?" />
    {metadataBook ? <BookMetadataEditor book={metadataBook} onClose={() => setMetadataBook(null)} onSaved={(updated) => { setMetadataBook(null); refreshed(); if (route.view === 'series') open(titleRoute(updated), true); setNotice('Book details saved.'); }} /> : null}
    <ConfirmDialog body="The shelf will be removed. Its books stay in your collection." busy={busy} confirmLabel="Delete shelf" danger onCancel={() => setShelfToDelete(null)} onConfirm={() => { if (shelfToDelete) void mutate(() => deleteBookShelf(shelfToDelete.id), 'Shelf deleted.').then((done) => { if (done) { setShelfToDelete(null); open({ surface: 'books', view: 'shelves' }); } }); }} open={Boolean(shelfToDelete)} title="Delete this shelf?" />
  </div>;
}
