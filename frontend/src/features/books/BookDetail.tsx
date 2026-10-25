import { Bookmark, BookOpen, Check, FileText, Heart, Layers, Plus, Trash2 } from '../../luminaIcons';

import { Button, IconButton } from '../../ui';
import { BookCover, readingPosition } from './BookCards';
import type { Book, BookStateUpdate } from './types';

const languageName = (language: string) => {
  try { return new Intl.DisplayNames(['en'], { type: 'language' }).of(language); }
  catch { return language; }
};

export function BookDetail({ book, onRead, onReadFile, onState, onShelf, onEdit, onCombine, onDelete, onSeries, onSeparate }: {
  book: Book; onRead: (book: Book) => void; onReadFile: (id: string) => void;
  onState: (book: Book, patch: BookStateUpdate) => void; onShelf: () => void; onEdit: () => void;
  onCombine: () => void; onDelete: () => void; onSeries: () => void; onSeparate?: (id: string) => void;
}) {
  const files = book.files ?? [{ id: book.id, filename: book.filename || `${book.title}.${book.format}`, format: book.format, available: book.available, chapter_count: book.chapter_count, page_count: book.page_count }];
  const paragraphs = book.description.split(/\n\s*\n/).map((paragraph) => paragraph.trim()).filter(Boolean);
  return <article className="books-title-detail">
    <div className="books-title-cover"><BookCover author={book.author} title={book.title} url={book.cover_url} />
      <p className="books-title-status">{readingPosition(book)}</p>
      {book.progress.updated_at ? <progress aria-label="Reading progress" max={100} value={book.progress.percent} /> : null}
    </div>
    <div className="books-title-story">
      <header className="books-title-heading"><p className="g-kicker">{book.series_name ? `${book.series_name}${book.volume_number !== null ? ` · Volume ${book.volume_number}` : ''}` : book.kind === 'manga' ? 'Comics & manga' : 'Your reading library'}</p>
      <h1 tabIndex={-1}>{book.title}</h1>
      <p className="books-title-author">{book.author || 'Unknown author'}</p></header>
      <div className="books-title-actions"><Button disabled={!book.available} icon={<BookOpen />} onClick={() => onRead(book)} variant="primary">{book.progress.completed ? 'Read again' : book.progress.updated_at ? 'Continue reading' : 'Start reading'}</Button><IconButton aria-pressed={book.favorite} icon={<Heart />} label={book.favorite ? 'Remove from favorites' : 'Add to favorites'} onClick={() => onState(book, { favorite: !book.favorite })} /><IconButton aria-pressed={book.reading_list} icon={<Bookmark />} label={book.reading_list ? 'Remove from reading list' : 'Add to reading list'} onClick={() => onState(book, { reading_list: !book.reading_list })} /><IconButton icon={<Plus />} label="Add to shelf" onClick={onShelf} /></div>
      {!book.available ? <p className="books-form-error">The book file is not available right now.</p> : null}
      {paragraphs.length ? <section aria-label="Synopsis" className="books-title-synopsis"><h2>About this book</h2>{paragraphs.map((paragraph, index) => <p key={index}>{paragraph}</p>)}</section> : null}
      {book.genres.length ? <ul aria-label="Genres" className="books-title-genres">{book.genres.map((genre) => <li key={genre}>{genre}</li>)}</ul> : null}
    </div>
    <aside aria-label="Book details and files" className="books-title-information">
      <section><h2>In your library</h2><dl><div><dt>Formats</dt><dd>{[...new Set(files.map((file) => file.format.toUpperCase()))].join(' · ')}</dd></div><div><dt>Length</dt><dd>{book.format === 'epub' ? `${book.chapter_count} chapters` : `${book.page_count} pages`}</dd></div>{book.language ? <div><dt>Language</dt><dd>{languageName(book.language)}</dd></div> : null}<div><dt>Visibility</dt><dd>{book.visibility === 'shared' ? 'Household' : 'Private'}</dd></div></dl></section>
      <section className="books-title-files"><h2>{files.length === 1 ? 'Your file' : `Your files · ${files.length}`}</h2><ul>{files.map((file) => <li key={file.id}><FileText aria-hidden="true" /><div><strong>{file.format.toUpperCase()} · {file.format === 'epub' ? `${file.chapter_count} chapters` : `${file.page_count} pages`}</strong><span>{file.filename}</span><button disabled={!file.available} onClick={() => onReadFile(file.id)} type="button">Read {file.format.toUpperCase()}</button>{book.can_edit && onSeparate && file.id !== book.id ? <button aria-label={`Separate ${file.filename}`} onClick={() => onSeparate(file.id)} type="button">Separate entry</button> : null}</div></li>)}</ul></section>
      <div className="books-title-management"><Button icon={<Check />} onClick={() => onState(book, { completed: !book.progress.completed, ...(book.progress.completed ? { unit_index: 0, offset: 0 } : {}) })} variant="quiet">{book.progress.completed ? 'Mark as unread' : 'Mark as read'}</Button>{book.series_id && book.kind === 'manga' ? <Button onClick={onSeries} variant="quiet">View series</Button> : null}{book.can_edit ? <><Button onClick={onEdit} variant="quiet">Edit details</Button><Button icon={<Layers />} onClick={onCombine} variant="quiet">Combine entries</Button><Button icon={<Trash2 />} onClick={onDelete} variant="quiet">Move to trash</Button></> : null}</div>
    </aside>
  </article>;
}
