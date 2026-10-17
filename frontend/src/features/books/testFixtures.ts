import type { UserProfile } from '../../types';
import type { Book, BookReaderManifest, BookSeries, BookShelf, BooksOverview } from './types';

export const booksUser: UserProfile = { id: 'books-member', username: 'reader', display_name: 'Reader', role: 'viewer', is_active: true };

export function book(patch: Partial<Book> = {}): Book {
  return {
    id: 'ebook-1', kind: 'ebook', format: 'epub', title: 'Northbound', author: 'Mira Vale', description: 'A quiet voyage north.', language: 'en', genres: ['Adventure'], series_id: null, series_name: null, volume_number: null,
    reading_direction: 'ltr', cover_url: null, chapter_count: 2, page_count: 0, created_at: '2026-10-01T00:00:00Z', visibility: 'private', can_edit: true, available: true,
    progress: { unit_index: 0, offset: 0, percent: 0, completed: false, updated_at: null }, favorite: false, reading_list: false,
    shelf_ids: [], ...patch,
  };
}

export const mangaVolume = (patch: Partial<Book> = {}) => book({ id: 'manga-1', kind: 'manga', format: 'cbz', title: 'Skybound, Volume 1', author: 'Rin Kato', series_id: 'series-1', series_name: 'Skybound', volume_number: 1, page_count: 3, chapter_count: 0, reading_direction: 'rtl', ...patch });

export const shelf = (patch: Partial<BookShelf> = {}): BookShelf => ({ id: 'shelf-1', name: 'Weekend reads', description: 'For slower mornings.', visibility: 'private', can_edit: true, book_ids: ['ebook-1', 'ebook-2'], count: 2, covers: [], ...patch });

export const series = (patch: Partial<BookSeries> = {}): BookSeries => ({ id: 'series-1', title: 'Skybound', author: 'Rin Kato', description: 'A skyfaring manga.', cover_url: null, volume_count: 2, read_volume_count: 0, next_book_id: 'manga-1', ...patch });

export const overview = (patch: Partial<BooksOverview> = {}): BooksOverview => ({ totals: { ebooks: 1, manga_series: 1, volumes: 2 }, continue_reading: [], recently_added: [book()], shelves: [shelf()], discover: [mangaVolume()], ...patch });

export const ebookManifest = (patch: Partial<BookReaderManifest> = {}): BookReaderManifest => ({ book: book({ progress: { unit_index: 0, offset: 0, percent: 0, completed: false, updated_at: '2026-10-01T00:00:00Z' } }), chapters: [{ label: 'Departure' }, { label: 'The pass' }], page_count: 0, reading_direction: 'ltr', ...patch });
