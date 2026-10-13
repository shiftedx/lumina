import { useEffect, useState } from 'react';

import { Button, Checkbox, Dialog, Field, Input, Skeleton, fieldProps } from '../../ui';
import { combineBookEntries, listBooks, listBookTrash, restoreBook } from './booksApi';
import type { Book } from './types';

const message = (error: unknown) => error instanceof Error ? error.message : 'The change could not be saved.';

export function CombineBooksDialog({ book, initialBooks = [], onClose, onCombined }: { book: Book; initialBooks?: Book[]; onClose: () => void; onCombined: (book: Book) => void }) {
  const [query, setQuery] = useState(book.title);
  const [options, setOptions] = useState<Book[] | null>(null);
  const [chosen, setChosen] = useState<Map<string, Book>>(() => new Map(initialBooks.filter((candidate) => candidate.id !== book.id).map((candidate) => [candidate.id, candidate])));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setOptions(null); setError(null);
    const timer = setTimeout(() => {
      listBooks({ q: query, kind: book.kind, limit: 100 }, controller.signal).then((page) => setOptions(page.items.filter((candidate) => candidate.id !== book.id && candidate.can_edit && candidate.visibility === book.visibility && (candidate.volume_number === null || book.volume_number === null || candidate.volume_number === book.volume_number) && (!candidate.language || !book.language || candidate.language.toLowerCase().replace('_', '-').split('-')[0] === book.language.toLowerCase().replace('_', '-').split('-')[0])))).catch((failure: unknown) => { if (!controller.signal.aborted) setError(message(failure)); });
    }, 200);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, book.id, book.kind, book.visibility]);
  async function combine() {
    setBusy(true); setError(null);
    try { onCombined(await combineBookEntries(book.id, [book.id, ...chosen.keys()])); }
    catch (failure) { setError(message(failure)); setBusy(false); }
  }
  return <Dialog busy={busy} description="Keep this book’s details and bring the selected entries’ files together. Reading progress and shelves are retained." footer={<><Button disabled={busy} onClick={onClose} variant="quiet">Cancel</Button><Button busy={busy} disabled={!chosen.size} onClick={() => { void combine(); }} variant="primary">Combine {chosen.size + 1} entries</Button></>} onClose={onClose} open size="md" title="Combine book entries">
    <p><strong>{book.title}</strong><span className="books-combine-file">{(book.files ?? []).map((file) => file.filename).join(' · ') || book.filename}</span></p>
    <Field label="Find entries to combine">{(ids) => <Input {...fieldProps(ids)} onChange={(event) => setQuery(event.target.value)} value={query} />}</Field>
    <div className="books-combine-options">{options === null ? <Skeleton count={3} label="Finding entries…" shape="row" /> : null}{[...chosen.values(), ...(options ?? []).filter((candidate) => !chosen.has(candidate.id))].map((candidate) => <div key={candidate.id}><Checkbox checked={chosen.has(candidate.id)} disabled={!chosen.has(candidate.id) && chosen.size >= 99} label={candidate.title} onChange={() => setChosen((value) => { const next = new Map(value); if (next.has(candidate.id)) next.delete(candidate.id); else next.set(candidate.id, candidate); return next; })} /><span className="books-combine-file">{candidate.author} · {(candidate.files ?? []).map((file) => file.filename).join(' · ') || candidate.filename || candidate.format.toUpperCase()}</span></div>)}{options !== null && !options.length && !chosen.size ? <p>No matching entries. Search another title or author.</p> : null}</div>
    {error ? <p className="books-form-error" role="alert">{error}</p> : null}
  </Dialog>;
}

export function BooksTrashDialog({ onClose, onRestored }: { onClose: () => void; onRestored: () => void }) {
  const [books, setBooks] = useState<Book[] | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    listBookTrash(controller.signal).then(setBooks).catch((failure: unknown) => { if (!controller.signal.aborted) setError(message(failure)); });
    return () => controller.abort();
  }, []);
  async function restore(book: Book) {
    setBusyId(book.id); setError(null);
    try { await restoreBook(book.id); setBooks((value) => value?.filter((item) => item.id !== book.id) ?? []); onRestored(); }
    catch (failure) { setError(message(failure)); }
    finally { setBusyId(null); }
  }
  return <Dialog description="Removed entries keep their files, reading progress, and shelves. Restore a book to return it to your library." footer={<Button onClick={onClose} variant="quiet">Done</Button>} onClose={onClose} open size="md" title="Books trash">
    {books === null ? <Skeleton count={3} label="Loading removed books…" shape="row" /> : books.length ? <ul className="books-trash-list">{books.map((book) => <li key={book.id}><div><strong>{book.title}</strong><span className="books-combine-file">{(book.files ?? []).map((file) => file.filename).join(' · ') || book.filename}</span></div><Button busy={busyId === book.id} disabled={busyId !== null} onClick={() => { void restore(book); }} variant="quiet">Restore</Button></li>)}</ul> : <p>Your trash is empty.</p>}
    {error ? <p className="books-form-error" role="alert">{error}</p> : null}
  </Dialog>;
}
