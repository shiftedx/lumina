import { BookOpen, Check, Ellipsis, Heart } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { IconButton, Menu, type MenuEntry } from '../../ui';
import { bookCoverUrl } from './booksApi';
import type { Book, BookSeries, BookShelf, BookStateUpdate } from './types';

const COVER_RETRY_DELAYS_MS = [1_000, 1_500, 2_500, 4_000, 6_000, 8_000, 10_000];

export function BookCover({ title, author = '', url, compact = false }: { title: string; author?: string; url: string | null; compact?: boolean }) {
  const source = bookCoverUrl(url);
  const root = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(() => typeof IntersectionObserver === 'undefined');
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  useEffect(() => { setAttempt(0); setFailed(false); }, [source]);
  useEffect(() => {
    const element = root.current;
    if (!source || !element || typeof IntersectionObserver === 'undefined') { setVisible(true); return undefined; }
    const observer = new IntersectionObserver((entries) => setVisible(entries.some((entry) => entry.isIntersecting)), { rootMargin: '100px' });
    observer.observe(element);
    return () => observer.disconnect();
  }, [source]);
  useEffect(() => {
    if (!failed || !visible || attempt >= COVER_RETRY_DELAYS_MS.length) return undefined;
    const timer = window.setTimeout(() => { setAttempt((value) => value + 1); setFailed(false); }, COVER_RETRY_DELAYS_MS[attempt]);
    return () => window.clearTimeout(timer);
  }, [attempt, failed, visible]);
  const retrySource = source && attempt ? `${source}${source.includes('?') ? '&' : '?'}attempt=${attempt}` : source;
  return <span className={`books-cover${compact ? ' is-compact' : ''}`} ref={root}>
    {retrySource && visible && !failed ? <img alt="" decoding="async" fetchPriority="low" loading="lazy" onError={() => setFailed(true)} src={retrySource} /> : <span className="books-cover-fallback"><BookOpen aria-hidden="true" /><strong>{title}</strong>{author ? <span>{author}</span> : null}</span>}
  </span>;
}

export function readingPosition(book: Book): string {
  if (book.progress.completed) return 'Read';
  if (!book.progress.updated_at) return 'Unread';
  const file = book.files?.find((file) => file.id === book.reading_file_id);
  return (file?.format ?? book.format) !== 'epub' ? `Page ${book.progress.unit_index + 1} of ${file?.page_count ?? book.page_count}` : `Chapter ${book.progress.unit_index + 1} · ${Math.round(book.progress.percent)}% read`;
}

export function BookCard({ book, onOpen, onRead, onState, onShelf, selecting = false, selected = false, onSelect, extra = [] }: {
  book: Book; onOpen: (book: Book) => void; onRead: (book: Book) => void; onState: (book: Book, update: BookStateUpdate) => void;
  onShelf: (ids: string[]) => void; selecting?: boolean; selected?: boolean; onSelect?: (id: string) => void; extra?: MenuEntry[];
}) {
  const manga = book.kind === 'manga';
  return <article className={`books-card${selected ? ' is-selected' : ''}`}>
    <div className="books-card-art">
      <button aria-label={selecting ? `${selected ? 'Deselect' : 'Select'} ${book.title}` : `Open ${book.title}`} aria-pressed={selecting ? selected : undefined} className="books-cover-button" onClick={() => selecting ? onSelect?.(book.id) : onOpen(book)} type="button"><BookCover author={book.author} title={book.title} url={book.cover_url} /></button>
      {selecting ? <span aria-hidden="true" className={`books-selection-check${selected ? ' is-checked' : ''}`}>{selected ? <Check /> : null}</span> : null}
      {!selecting && book.favorite ? <span aria-label="Favorite" className="books-favorite-marker"><Heart aria-hidden="true" /></span> : null}
      {!selecting && book.progress.updated_at && !book.progress.completed ? <span aria-hidden="true" className="books-cover-progress"><span style={{ width: `${Math.max(1, Math.min(100, book.progress.percent))}%` }} /></span> : null}
    </div>
    <div className="books-card-caption">
      <button className="books-card-title" onClick={() => onOpen(book)} type="button">{manga && book.series_name ? book.series_name : book.title}</button>
      <span className="books-card-author">{book.author || 'Unknown author'}</span>
      <span className="books-card-format">{manga ? `Manga${book.volume_number !== null ? ` · Volume ${book.volume_number}` : ''}` : `Ebook · ${(book.files?.map((file) => file.format).filter((format, index, all) => all.indexOf(format) === index) ?? [book.format]).join(' + ').toUpperCase()}`}{book.progress.completed ? ' · Read' : !book.available ? ' · File unavailable' : ''}</span>
      {book.filename ? <span className="books-card-filename" title={book.files?.map((file) => file.filename).join(' · ') || book.filename}>{book.filename}{book.files && book.files.length > 1 ? ` · ${book.files.length} files` : ''}</span> : null}
      {!selecting ? <Menu align="end" items={[
        { kind: 'item', label: book.progress.completed ? 'Read again' : book.progress.updated_at ? 'Continue reading' : 'Start reading', icon: <BookOpen />, disabled: !book.available, onSelect: () => onRead(book) },
        { kind: 'item', label: book.favorite ? 'Remove from favorites' : 'Add to favorites', icon: <Heart />, onSelect: () => onState(book, { favorite: !book.favorite }) },
        { kind: 'item', label: book.reading_list ? 'Remove from reading list' : 'Add to reading list', onSelect: () => onState(book, { reading_list: !book.reading_list }) },
        { kind: 'item', label: 'Add to shelf', onSelect: () => onShelf([book.id]) },
        { kind: 'item', label: book.progress.completed ? 'Mark as unread' : 'Mark as read', onSelect: () => onState(book, { completed: !book.progress.completed, ...(book.progress.completed ? { unit_index: 0, offset: 0 } : {}) }) },
        ...extra,
      ]} label={`Actions for ${book.title}`} trigger={(props) => <button {...props} aria-label={`Actions for ${book.title}`} className="books-card-menu" type="button"><Ellipsis aria-hidden="true" /></button>} /> : null}
    </div>
  </article>;
}

export function SeriesCard({ series, onOpen, onRead }: { series: BookSeries; onOpen: (series: BookSeries) => void; onRead: (id: string) => void }) {
  return <article className="books-card books-series-card">
    <button aria-label={`Open ${series.title}, ${series.volume_count} ${series.volume_count === 1 ? 'volume' : 'volumes'}`} className="books-cover-button" onClick={() => onOpen(series)} type="button"><BookCover author={series.author} title={series.title} url={series.cover_url} /></button>
    <div className="books-card-caption"><button className="books-card-title" onClick={() => onOpen(series)} type="button">{series.title}</button><span className="books-card-author">{series.author || 'Unknown author'}</span><span className="books-card-format">{series.volume_count} {series.volume_count === 1 ? 'volume' : 'volumes'} · {series.read_volume_count} read</span>{series.next_book_id ? <IconButton icon={<BookOpen />} label={`Continue ${series.title}`} onClick={() => onRead(series.next_book_id!)} /> : null}</div>
  </article>;
}

export function ShelfTile({ shelf, onOpen }: { shelf: BookShelf; onOpen: (shelf: BookShelf) => void }) {
  return <button className="books-shelf-tile" onClick={() => onOpen(shelf)} type="button">
    <span aria-hidden="true" className="books-shelf-covers">{shelf.covers.length ? shelf.covers.slice(0, 4).map((url, index) => <BookCover compact key={`${url}:${index}`} title={shelf.name} url={url} />) : <span className="books-empty-shelf-art"><BookOpen /></span>}</span>
    <strong>{shelf.name}</strong><span>{shelf.count} {shelf.count === 1 ? 'book' : 'books'} · {shelf.visibility === 'shared' ? 'Household' : 'Private'}</span>
  </button>;
}
