import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ImportBooksDialog } from './BooksDialogs';
import { BooksSurface } from './BooksSurface';
import { AccessProvider, type AccessValue } from '../access/access';
import { booksUser, book, ebookManifest, mangaVolume, overview, series, shelf } from './testFixtures';
import type { BooksRoute } from './routes';

const api = vi.hoisted(() => ({
  deleteBookShelf: vi.fn(), getBook: vi.fn(), getBookReader: vi.fn(), getBookSeries: vi.fn(), getBooksOverview: vi.fn(), importBook: vi.fn(),
  listBooks: vi.fn(), listBookSeries: vi.fn(), setBookShelfEntries: vi.fn(), updateBookShelf: vi.fn(), updateBookState: vi.fn(),
}));
vi.mock('./booksApi', async (importOriginal) => ({ ...(await importOriginal<typeof import('./booksApi')>()), ...api }));

const permitted: AccessValue = { access: { sections: null, blocked_streaming: [], followed_only: false, allowed_now: true, until: null, remaining_minutes: null, can_download: true }, stop: null, refresh: vi.fn() };

function Surface({ initial, access = permitted }: { initial: BooksRoute; access?: AccessValue }) {
  const [route, setRoute] = useState(initial);
  return <AccessProvider value={access}><BooksSurface onRoute={setRoute} route={route} user={booksUser} /></AccessProvider>;
}

const page = <T,>(items: T[]) => ({ items, total: items.length, next_cursor: null });

beforeEach(() => {
  vi.clearAllMocks();
  api.getBooksOverview.mockResolvedValue(overview());
  api.listBooks.mockResolvedValue(page([book(), book({ id: 'ebook-2', title: 'After the Storm' })]));
  api.listBookSeries.mockResolvedValue(page([series()]));
  api.getBook.mockResolvedValue(book());
  api.getBookSeries.mockResolvedValue({ series: series(), books: [mangaVolume()] });
  api.getBookReader.mockResolvedValue(ebookManifest());
  api.updateBookState.mockImplementation(async (_id: string, patch: object) => book({ ...patch }));
  api.setBookShelfEntries.mockResolvedValue(shelf());
  api.importBook.mockResolvedValue(book());
});

describe('BooksSurface', () => {
  it('shows the standalone Books overview and canonical Books tabs without Library lenses or seeded catalog copy', async () => {
    render(<Surface initial={{ surface: 'books', view: 'overview' }} />);
    await screen.findByRole('heading', { level: 1, name: 'Books' });
    const tabs = screen.getByRole('navigation', { name: 'Books' });
    expect(within(tabs).getByRole('link', { name: 'Overview' }).getAttribute('href')).toBe('/books');
    expect(within(tabs).getByRole('link', { name: 'Ebooks' }).getAttribute('href')).toBe('/books/ebooks');
    expect(within(tabs).getByRole('link', { name: 'Manga' }).getAttribute('href')).toBe('/books/manga');
    expect(within(tabs).getByRole('link', { name: 'Shelves' }).getAttribute('href')).toBe('/books/shelves');
    expect(screen.queryByRole('navigation', { name: 'Library' })).toBeNull();
    expect(screen.getAllByText('Northbound').length).toBeGreaterThan(0);
    expect(screen.queryByText(/sample catalog/i)).toBeNull();
  });

  it('keeps continued stories out of Recent and sends Recent View all to the complete book list', async () => {
    const browser = userEvent.setup();
    const continuing = book({ progress: { unit_index: 0, offset: 0.25, percent: 25, completed: false, updated_at: '2026-10-02T00:00:00Z' } });
    const added = book({ id: 'ebook-2', title: 'After the Storm' });
    api.getBooksOverview.mockResolvedValue(overview({ continue_reading: [continuing], recently_added: [continuing, added], discover: [] }));
    render(<Surface initial={{ surface: 'books', view: 'overview' }} />);
    await screen.findByRole('heading', { level: 2, name: 'Recently added' });
    expect(screen.getAllByRole('button', { name: 'Open Northbound' })).toHaveLength(1);
    const recent = screen.getByRole('heading', { level: 2, name: 'Recently added' }).closest('section')!;
    await browser.click(within(recent).getByRole('button', { name: 'View all' }));
    await screen.findByRole('heading', { level: 1, name: 'All books' });
    expect(api.listBooks).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'added', status: 'all' }));
  });

  it('routes Ebooks, Manga and personal shelves through their canonical paths', async () => {
    const browser = userEvent.setup();
    render(<Surface initial={{ surface: 'books', view: 'overview' }} />);
    await screen.findByRole('link', { name: 'Manga' });
    await browser.click(screen.getByRole('link', { name: 'Ebooks' }));
    await screen.findByRole('heading', { level: 1, name: 'Ebooks' });
    expect(screen.getByRole('link', { name: 'Ebooks' }).getAttribute('aria-current')).toBe('page');
    await browser.click(screen.getByRole('link', { name: 'Manga' }));
    await screen.findByRole('heading', { level: 1, name: 'Manga' });
    await browser.click(screen.getByRole('link', { name: 'Shelves' }));
    await screen.findByRole('heading', { level: 1, name: 'Your shelves' });
    await browser.click(within(screen.getByRole('complementary', { name: 'Personal shelves' })).getByRole('button', { name: /Weekend reads/ }));
    await screen.findByRole('heading', { level: 1, name: 'Weekend reads' });
    expect(api.listBooks).toHaveBeenLastCalledWith(expect.objectContaining({ shelf_id: 'shelf-1', sort: 'custom' }));
  });

  it('opens a manga series and starts its next volume in the reader', async () => {
    const browser = userEvent.setup();
    render(<Surface initial={{ surface: 'books', view: 'manga' }} />);
    await browser.click(await screen.findByRole('button', { name: /Open Skybound, 2 volumes/ }));
    await screen.findByRole('heading', { level: 1, name: 'Skybound' });
    await browser.click(screen.getByRole('button', { name: 'Read volume 1' }));
    await waitFor(() => expect(api.getBookReader).toHaveBeenCalledWith('manga-1', expect.any(AbortSignal)));
  });

  it('opens individual volumes as title detail and exposes volume-specific editing', async () => {
    const browser = userEvent.setup();
    api.getBook.mockResolvedValue(mangaVolume());
    const detail = render(<Surface initial={{ surface: 'books', view: 'series', seriesId: 'series-1' }} />);
    await screen.findByRole('heading', { level: 1, name: 'Skybound' });
    await browser.click(screen.getByRole('button', { name: 'Open Skybound, Volume 1' }));
    await screen.findByRole('heading', { level: 1, name: 'Skybound, Volume 1' });
    expect(api.getBook).toHaveBeenCalledWith('manga-1');

    // The series list preserves a volume's own metadata editor, rather than an ambiguous series editor.
    detail.unmount();
    render(<Surface initial={{ surface: 'books', view: 'series', seriesId: 'series-1' }} />);
    await browser.click(await screen.findByRole('button', { name: 'Actions for Skybound, Volume 1' }));
    await browser.click(screen.getByRole('menuitem', { name: 'Edit volume details' }));
    expect(screen.getByRole('dialog', { name: 'Edit book details' })).not.toBeNull();
    expect(screen.queryByRole('menuitem', { name: /edit series/i })).toBeNull();
  });

  it('resets a completed title checkpoint before reopening its reader', async () => {
    const browser = userEvent.setup();
    const completed = book({ progress: { unit_index: 1, offset: 0.75, percent: 100, completed: true, updated_at: '2026-10-01T00:00:00Z' } });
    api.getBook.mockResolvedValue(completed);
    render(<Surface initial={{ surface: 'books', view: 'title', bookId: 'ebook-1' }} />);
    await browser.click(await screen.findByRole('button', { name: 'Read again' }));
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledWith('ebook-1', { completed: false, unit_index: 0, offset: 0 }));
    await waitFor(() => expect(api.getBookReader).toHaveBeenCalledWith('ebook-1', expect.any(AbortSignal)));
  });

  it('keeps imported ebook series metadata out of manga series navigation', async () => {
    api.getBook.mockResolvedValue(book({ series_id: 'novel-series', series_name: 'The Northern Roads', volume_number: 1 }));
    render(<Surface initial={{ surface: 'books', view: 'title', bookId: 'ebook-1' }} />);
    await screen.findByRole('heading', { level: 1, name: 'Northbound' });
    expect(screen.queryByRole('button', { name: 'View series' })).toBeNull();
  });

  it('does not fetch Books for a member without the Books section and hides imports when downloads are denied', async () => {
    const restricted: AccessValue = { ...permitted, access: { ...permitted.access!, sections: ['movies'], can_download: false } };
    const blocked = render(<Surface access={restricted} initial={{ surface: 'books', view: 'overview' }} />);
    expect(await screen.findByRole('heading', { level: 2, name: "Books isn't available right now" })).not.toBeNull();
    expect(api.getBooksOverview).not.toHaveBeenCalled();

    blocked.unmount();
    api.getBooksOverview.mockClear();
    api.getBooksOverview.mockResolvedValue(overview({ totals: { ebooks: 0, manga_series: 0, volumes: 0 }, recently_added: [], discover: [], shelves: [] }));
    render(<Surface access={{ ...permitted, access: { ...permitted.access!, can_download: false } }} initial={{ surface: 'books', view: 'overview' }} />);
    await screen.findByRole('heading', { level: 2, name: 'A home for every story' });
    expect(screen.queryByRole('button', { name: /import/i })).toBeNull();
    expect(screen.getByLabelText('Choose EPUB, PDF, CBZ, and CBR files')).toHaveProperty('disabled', true);
  });

  it('adds to shelves, updates shelf order/removal, and changes reading state through the API', async () => {
    const browser = userEvent.setup();
    render(<Surface initial={{ surface: 'books', view: 'title', bookId: 'ebook-1' }} />);
    await screen.findByRole('button', { name: 'Add to shelf' });
    await browser.click(screen.getByRole('button', { name: 'Add to shelf' }));
    const dialog = screen.getByRole('dialog', { name: 'Add to shelf' });
    await browser.click(within(dialog).getByRole('checkbox', { name: 'Weekend reads' }));
    await browser.click(within(dialog).getByRole('button', { name: 'Add to shelves' }));
    await waitFor(() => expect(api.setBookShelfEntries).toHaveBeenCalledWith('shelf-1', ['ebook-1', 'ebook-2']));
    await browser.click(screen.getByRole('button', { name: 'Mark as read' }));
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledWith('ebook-1', { completed: true }));
  });

  it('reorders and removes a book from an editable personal shelf', async () => {
    const browser = userEvent.setup();
    render(<Surface initial={{ surface: 'books', view: 'shelves', shelfId: 'shelf-1' }} />);
    await screen.findByRole('heading', { level: 1, name: 'Weekend reads' });
    await browser.click(screen.getByRole('button', { name: 'Actions for Northbound' }));
    await browser.click(screen.getByRole('menuitem', { name: 'Move later' }));
    await waitFor(() => expect(api.setBookShelfEntries).toHaveBeenCalledWith('shelf-1', ['ebook-2', 'ebook-1']));
    await browser.click(screen.getByRole('button', { name: 'Actions for Northbound' }));
    await browser.click(screen.getByRole('menuitem', { name: 'Remove from this shelf' }));
    await waitFor(() => expect(api.setBookShelfEntries).toHaveBeenCalledWith('shelf-1', ['ebook-2']));
  });
});

describe('book import', () => {
  it('imports supported books and retries a failed archive', async () => {
    const browser = userEvent.setup();
    const epub = new File(['book'], 'northbound.epub', { type: 'application/epub+zip' });
    const cbz = new File(['manga'], 'skybound.cbz', { type: 'application/vnd.comicbook+zip' });
    const pdf = new File(['pdf'], 'scan.pdf');
    const cbr = new File(['rar'], 'comic.cbr');
    const text = new File(['no'], 'notes.txt', { type: 'text/plain' });
    api.importBook.mockRejectedValueOnce(new Error('Archive needs repair.')).mockResolvedValue(book());
    render(<ImportBooksDialog files={[epub, cbz, pdf, cbr, text]} onClose={vi.fn()} onImported={vi.fn()} />);
    await browser.click(screen.getByRole('button', { name: 'Import 5 files' }));
    await screen.findByText('Choose an EPUB or PDF ebook, or a CBZ or CBR manga archive.');
    expect(api.importBook).toHaveBeenCalledTimes(4);
    expect(api.importBook).toHaveBeenCalledWith(epub);
    expect(api.importBook).toHaveBeenCalledWith(cbz);
    expect(api.importBook).toHaveBeenCalledWith(pdf);
    expect(api.importBook).toHaveBeenCalledWith(cbr);
    await browser.click(screen.getByRole('button', { name: 'Retry remaining files' }));
    await waitFor(() => expect(api.importBook).toHaveBeenCalledTimes(5));
    expect(api.importBook).toHaveBeenLastCalledWith(epub);
  });
});
