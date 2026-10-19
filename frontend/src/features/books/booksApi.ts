import { apiBaseUrl, del, post, put, requestJson, trackKeepalive } from '../../api';
import type { Book, BookMetadataUpdate, BookNode, BookPage, BookReaderManifest, BookSeries, BookShelf, BookSort, BookStateUpdate, BookStatus, BooksOverview } from './types';

const enc = encodeURIComponent;
const query = (params: Record<string, string | number | undefined>) => {
  const values = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== '') values.set(key, String(value));
  return values.size ? `?${values}` : '';
};
export type BooksQuery = { kind?: 'ebook' | 'manga'; q?: string; sort?: BookSort; status?: BookStatus; shelf_id?: string; cursor?: string; limit?: number };

export const getBooksOverview = (signal?: AbortSignal) => requestJson<BooksOverview>('/api/books/overview', undefined, { signal });
export const listBooks = (params: BooksQuery = {}, signal?: AbortSignal) => requestJson<BookPage<Book>>(`/api/books${query(params)}`, undefined, { signal });
export const listBookSeries = (params: Omit<BooksQuery, 'kind' | 'shelf_id'> = {}, signal?: AbortSignal) => requestJson<BookPage<BookSeries>>(`/api/books/series${query(params)}`, undefined, { signal });
export const getBook = (id: string, signal?: AbortSignal) => requestJson<Book>(`/api/books/${enc(id)}`, undefined, { signal });
export const getBookSeries = (id: string, signal?: AbortSignal) => requestJson<{ series: BookSeries; books: Book[] }>(`/api/books/series/${enc(id)}`, undefined, { signal });
export const getBookReader = (id: string, signal?: AbortSignal) => requestJson<BookReaderManifest>(`/api/books/${enc(id)}/reader`, undefined, { signal });
export const getBookChapter = (id: string, index: number, signal?: AbortSignal) => requestJson<{ nodes: BookNode[] }>(`/api/books/${enc(id)}/chapters/${index}`, undefined, { signal });
export const bookPageUrl = (id: string, index: number) => `${apiBaseUrl}/api/books/${enc(id)}/pages/${index}`;
/** Private, bounded encoded page data for the mounted reader's current/next-page window. */
export async function getBookPageBytes(id: string, index: number, signal: AbortSignal, speculative = false): Promise<Blob> {
  const response = await fetch(bookPageUrl(id, index), { credentials: 'include', cache: 'no-store', signal, priority: speculative ? 'low' : 'high' });
  if (!response.ok) throw new Error('The page image could not be loaded.');
  if (!['image/jpeg', 'image/png', 'image/gif', 'image/webp'].includes(response.headers.get('Content-Type')?.split(';')[0] ?? '')) throw new Error('The page image type is unsupported.');
  const blob = await response.blob();
  if (!blob.size || blob.size > 24 * 1024 * 1024) throw new Error('The page image exceeds the allowed size.');
  return blob;
}
export const bookResourceUrl = (id: string, member: string) => `${apiBaseUrl}/api/books/${enc(id)}/resources?member=${enc(member)}`;
export const bookCoverUrl = (url: string | null) => url?.startsWith('/api/books/') ? `${apiBaseUrl}${url}` : null;
export const importBook = (file: File) => requestJson<Book>(`/api/books/import?filename=${enc(file.name)}`, { method: 'PUT', body: file, headers: { 'Content-Type': 'application/octet-stream' } }, { timeoutMs: 300_000 });
export const updateBookState = (id: string, state: BookStateUpdate, keepalive = false) => {
  const request = requestJson<Book>(`/api/books/${enc(id)}/state`, { method: 'PATCH', body: JSON.stringify(state), keepalive });
  return keepalive ? trackKeepalive(request) : request;
};
export const updateBookMetadata = (id: string, metadata: BookMetadataUpdate) => requestJson<Book>(`/api/books/${enc(id)}`, { method: 'PATCH', body: JSON.stringify(metadata) });
export const listBookShelves = (signal?: AbortSignal) => requestJson<BookShelf[]>('/api/books/shelves', undefined, { signal });
export const createBookShelf = (name: string, description = '', visibility: 'private' | 'shared' = 'private') => post<BookShelf>('/api/books/shelves', { name, description, visibility });
export const updateBookShelf = (id: string, metadata: Partial<Pick<BookShelf, 'name' | 'description' | 'visibility'>>) => requestJson<BookShelf>(`/api/books/shelves/${enc(id)}`, { method: 'PATCH', body: JSON.stringify(metadata) });
export const setBookShelfEntries = (id: string, book_ids: string[]) => put<BookShelf>(`/api/books/shelves/${enc(id)}/entries`, { book_ids });
export const deleteBookShelf = (id: string) => del(`/api/books/shelves/${enc(id)}`);
export const combineBookEntries = (primary_book_id: string, book_ids: string[]) => post<Book>('/api/books/combine', { primary_book_id, book_ids });
export const deleteBook = (id: string) => del(`/api/books/${enc(id)}`);
export const listBookTrash = (signal?: AbortSignal) => requestJson<Book[]>('/api/books/trash', undefined, { signal });
export const restoreBook = (id: string) => post<Book>(`/api/books/${enc(id)}/restore`, {});

export const separateBookFile = (id: string) => post<Book>(`/api/books/${enc(id)}/separate`, {});
