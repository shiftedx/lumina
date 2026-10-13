export type BookKind = 'ebook' | 'manga';
export type BookFormat = 'epub' | 'cbz' | 'cbr' | 'pdf';
export type BookStatus = 'all' | 'unread' | 'reading' | 'read' | 'favorites' | 'reading_list';
export type BookSort = 'added' | 'title' | 'author' | 'custom';

export type Book = {
  id: string;
  kind: BookKind;
  format: BookFormat;
  title: string;
  author: string;
  description: string;
  language: string;
  genres: string[];
  series_id: string | null;
  series_name: string | null;
  volume_number: number | null;
  reading_direction: 'ltr' | 'rtl';
  cover_url: string | null;
  chapter_count: number;
  page_count: number;
  created_at: string;
  visibility: 'private' | 'shared';
  can_edit: boolean;
  available: boolean;
  progress: { unit_index: number; offset: number; percent: number; completed: boolean; updated_at: string | null };
  favorite: boolean;
  reading_list: boolean;
  shelf_ids: string[];
};

export type BookSeries = {
  id: string;
  title: string;
  author: string;
  description: string;
  cover_url: string | null;
  volume_count: number;
  read_volume_count: number;
  next_book_id: string | null;
};

export type BookShelf = {
  id: string;
  name: string;
  description: string;
  visibility: 'private' | 'shared';
  can_edit: boolean;
  book_ids: string[];
  count: number;
  covers: string[];
};

export type BooksOverview = {
  totals: { ebooks: number; manga_series: number; volumes: number };
  continue_reading: Book[];
  recently_added: Book[];
  shelves: BookShelf[];
  discover: Book[];
};
export type BookPage<T> = { items: T[]; total: number; next_cursor: string | null };
export type BookReaderManifest = { book: Book; chapters: { label: string }[]; page_count: number; reading_direction: 'ltr' | 'rtl' };
export type BookNode = {
  tag: string;
  id?: string;
  text?: string;
  children?: BookNode[];
  member?: string;
  alt?: string;
  chapter?: number;
  anchor?: string;
};
export type BookStateUpdate = Partial<Pick<Book, 'favorite' | 'reading_list'>> & { completed?: boolean; unit_index?: number; offset?: number };
export type BookMetadataUpdate = Partial<Pick<Book, 'title' | 'author' | 'series_name' | 'volume_number' | 'reading_direction' | 'visibility' | 'genres'>>;
