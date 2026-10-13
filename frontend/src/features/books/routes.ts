export const BOOKS_LIST_VIEWS = ['overview', 'ebooks', 'manga', 'shelves', 'discover'] as const;
export type BooksListView = (typeof BOOKS_LIST_VIEWS)[number];
export const BOOKS_SORTS = ['added', 'title', 'author', 'custom'] as const;
export type BooksSort = (typeof BOOKS_SORTS)[number];
export const BOOKS_STATUSES = ['all', 'unread', 'reading', 'read', 'favorites', 'reading_list'] as const;
export type BooksStatus = (typeof BOOKS_STATUSES)[number];

type BooksFilters = { q?: string; sort?: BooksSort; status?: BooksStatus };

/** One canonical, bookmarkable location inside the independent Books surface. */
export type BooksRoute =
  | ({ surface: 'books'; view: Exclude<BooksListView, 'shelves'> } & BooksFilters)
  | ({ surface: 'books'; view: 'shelves'; shelfId?: string } & BooksFilters)
  | { surface: 'books'; view: 'title'; bookId: string }
  | { surface: 'books'; view: 'series'; seriesId: string }
  | { surface: 'books'; view: 'read'; bookId: string };

const BOOK_ID = /^[A-Za-z0-9_-]{1,64}$/;
const MAX_QUERY = 200;
const defaultRoute = (): BooksRoute => ({ surface: 'books', view: 'overview' });

function filters(params: URLSearchParams): BooksFilters {
  const q = (params.get('q') ?? '').trim().slice(0, MAX_QUERY);
  const sort = BOOKS_SORTS.find((value) => value === params.get('sort'));
  const status = BOOKS_STATUSES.find((value) => value === params.get('status'));
  return { ...(q ? { q } : {}), ...(sort ? { sort } : {}), ...(status ? { status } : {}) };
}

function safeId(value: string): string | null {
  try {
    const id = decodeURIComponent(value);
    return BOOK_ID.test(id) ? id : null;
  } catch {
    return null;
  }
}

/** Parses /books paths. Unknown or malformed Books paths stay inside Books at its overview. */
export function parseBooksRoute(path: string, params: URLSearchParams): BooksRoute | null {
  if (path !== '/books' && !path.startsWith('/books/')) return null;
  if (path === '/books') return { ...defaultRoute(), ...filters(params) };
  const list = /^\/books\/(ebooks|manga|shelves|discover)$/.exec(path);
  if (list) {
    const view = list[1] as BooksListView;
    return { surface: 'books', view, ...filters(params) } as BooksRoute;
  }
  const shelf = /^\/books\/shelves\/([^/]+)$/.exec(path);
  if (shelf) {
    const shelfId = safeId(shelf[1]);
    return shelfId ? { surface: 'books', view: 'shelves', shelfId, ...filters(params) } : defaultRoute();
  }
  const title = /^\/books\/title\/([^/]+)$/.exec(path);
  if (title) {
    const bookId = safeId(title[1]);
    return bookId ? { surface: 'books', view: 'title', bookId } : defaultRoute();
  }
  const series = /^\/books\/series\/([^/]+)$/.exec(path);
  if (series) {
    const seriesId = safeId(series[1]);
    return seriesId ? { surface: 'books', view: 'series', seriesId } : defaultRoute();
  }
  const reader = /^\/books\/read\/([^/]+)$/.exec(path);
  if (reader) {
    const bookId = safeId(reader[1]);
    return bookId ? { surface: 'books', view: 'read', bookId } : defaultRoute();
  }
  return defaultRoute();
}

export function booksRoutePath(route: BooksRoute): string {
  if (route.view === 'title') return `/books/title/${encodeURIComponent(route.bookId)}`;
  if (route.view === 'series') return `/books/series/${encodeURIComponent(route.seriesId)}`;
  if (route.view === 'read') return `/books/read/${encodeURIComponent(route.bookId)}`;
  const path = route.view === 'overview' ? '/books' : `/books/${route.view}${route.view === 'shelves' && route.shelfId ? `/${encodeURIComponent(route.shelfId)}` : ''}`;
  const query = [route.q ? `q=${encodeURIComponent(route.q)}` : '', route.sort ? `sort=${route.sort}` : '', route.status ? `status=${route.status}` : ''].filter(Boolean).join('&');
  return query ? `${path}?${query}` : path;
}

/** List pages share a focus target; each title, series and reader gets its own page focus target. */
export function booksRouteFocusKey(route: BooksRoute): string {
  if (route.view === 'title') return `books:title:${route.bookId}`;
  if (route.view === 'series') return `books:series:${route.seriesId}`;
  if (route.view === 'read') return `books:read:${route.bookId}`;
  return 'books';
}
