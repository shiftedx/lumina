import { Check, FileText, Plus } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { Button, Checkbox, Dialog, Field, Input, Select, Textarea, fieldProps } from '../../ui';
import { createBookShelf, importBook, setBookShelfEntries, updateBookMetadata, updateBookShelf } from './booksApi';
import type { Book, BookShelf } from './types';

const errorText = (error: unknown) => error instanceof Error ? error.message : 'The change could not be saved.';

export function ImportBooksDialog({ files, onClose, onImported }: { files: File[]; onClose: () => void; onImported: () => void }) {
  const [entries, setEntries] = useState(() => files.map((file) => ({ file, state: 'pending' as 'pending' | 'importing' | 'done' | 'failed', error: '' })));
  const [busy, setBusy] = useState(false);
  const waiting = entries.filter((entry) => entry.state === 'pending' || entry.state === 'failed');
  async function start() {
    if (busy) return;
    setBusy(true);
    let imported = false;
    for (const entry of waiting) {
      const update = (state: typeof entry.state, error = '') => setEntries((current) => current.map((value) => value.file === entry.file ? { ...value, state, error } : value));
      if (!/\.(epub|pdf|cbz|cbr)$/i.test(entry.file.name)) { update('failed', 'Choose an EPUB or PDF ebook, or a CBZ or CBR manga archive.'); continue; }
      if (entry.file.size > 256 * 1024 * 1024) { update('failed', 'This file is larger than the 256 MiB import limit.'); continue; }
      update('importing');
      try { await importBook(entry.file); update('done'); imported = true; }
      catch (error) { update('failed', errorText(error)); }
    }
    if (imported) onImported();
    setBusy(false);
  }
  return <Dialog busy={busy} description="Import EPUB and PDF ebooks and CBZ and CBR manga. Your imports are private until you share them." footer={<><Button disabled={busy} onClick={onClose} variant="quiet">{waiting.length ? 'Close' : 'Done'}</Button>{waiting.length ? <Button busy={busy} onClick={() => { void start(); }} variant="primary">{entries.some((entry) => entry.state === 'failed') ? 'Retry remaining files' : `Import ${waiting.length} ${waiting.length === 1 ? 'file' : 'files'}`}</Button> : null}</>} onClose={onClose} open size="md" title="Import into Books">
    <ul className="books-import-files">{entries.map((entry, index) => <li key={index}><span aria-hidden="true">{entry.state === 'done' ? <Check /> : <FileText />}</span><div><strong>{entry.file.name}</strong><span aria-live="polite">{entry.state === 'pending' ? `${(entry.file.size / 1024 / 1024).toFixed(1)} MiB · Ready to import` : entry.state === 'importing' ? 'Importing…' : entry.state === 'done' ? 'Imported' : entry.error}</span></div></li>)}</ul>
  </Dialog>;
}

export function ShelfEditor({ shelf, onClose, onSaved }: { shelf?: BookShelf; onClose: () => void; onSaved: (shelf: BookShelf) => void }) {
  const [name, setName] = useState(shelf?.name ?? '');
  const [description, setDescription] = useState(shelf?.description ?? '');
  const [shared, setShared] = useState(shelf?.visibility === 'shared');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save(event: FormEvent) {
    event.preventDefault(); if (busy || !name.trim()) return;
    setBusy(true); setError(null);
    try { const visibility = shared ? 'shared' : 'private'; onSaved(shelf ? await updateBookShelf(shelf.id, { name: name.trim(), description: description.trim(), visibility }) : await createBookShelf(name.trim(), description.trim(), visibility)); }
    catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }
  return <Dialog busy={busy} footer={<><Button onClick={onClose} variant="quiet">Cancel</Button><Button busy={busy} disabled={!name.trim()} form="book-shelf-form" type="submit" variant="primary">{shelf ? 'Save shelf' : 'Create shelf'}</Button></>} onClose={onClose} open size="sm" title={shelf ? 'Edit shelf' : 'Create a shelf'}><form className="books-form" id="book-shelf-form" onSubmit={(event) => { void save(event); }}><Field label="Shelf name" required>{(ids) => <Input {...fieldProps(ids)} maxLength={120} onChange={(event) => setName(event.target.value)} required value={name} />}</Field><Field label="Description">{(ids) => <Textarea {...fieldProps(ids)} maxLength={1000} onChange={(event) => setDescription(event.target.value)} value={description} />}</Field><Checkbox checked={shared} label="Share this shelf with the household" onChange={(event) => setShared(event.target.checked)} /><p className="books-form-note">Members see only books they already have access to.</p>{error ? <p className="books-form-error" role="alert">{error}</p> : null}</form></Dialog>;
}

export function AddToShelfDialog({ ids, shelves, onClose, onSaved, onCreate }: { ids: string[]; shelves: BookShelf[]; onClose: () => void; onSaved: () => void; onCreate: () => void }) {
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editable = shelves.filter((shelf) => shelf.can_edit);
  async function save() {
    if (busy) return;
    setBusy(true); setError(null);
    const results = await Promise.allSettled(editable.filter((shelf) => chosen.has(shelf.id)).map((shelf) => setBookShelfEntries(shelf.id, [...new Set([...shelf.book_ids, ...ids])])));
    const failure = results.find((result) => result.status === 'rejected');
    if (failure?.status === 'rejected') { setError(errorText(failure.reason)); setBusy(false); }
    else onSaved();
  }
  return <Dialog busy={busy} description={`Add ${ids.length === 1 ? 'this book' : `${ids.length} books`} to one or more personal shelves.`} footer={<><Button onClick={onClose} variant="quiet">Cancel</Button><Button busy={busy} disabled={!chosen.size} onClick={() => { void save(); }} variant="primary">Add to shelves</Button></>} onClose={onClose} open size="sm" title="Add to shelf"><div className="books-shelf-choices">{editable.map((shelf) => <Checkbox checked={chosen.has(shelf.id)} key={shelf.id} label={shelf.name} onChange={() => setChosen((current) => { const next = new Set(current); if (next.has(shelf.id)) next.delete(shelf.id); else next.add(shelf.id); return next; })} />)}{!editable.length ? <p>Create a shelf for the stories you want to keep together.</p> : null}<Button icon={<Plus />} onClick={onCreate} variant="quiet">Create a shelf</Button>{error ? <p className="books-form-error" role="alert">{error}</p> : null}</div></Dialog>;
}

export function BookMetadataEditor({ book, onClose, onSaved }: { book: Book; onClose: () => void; onSaved: (book: Book) => void }) {
  const [title, setTitle] = useState(book.title);
  const [author, setAuthor] = useState(book.author ?? '');
  const [description, setDescription] = useState(book.description);
  const [series, setSeries] = useState(book.series_name ?? '');
  const [volume, setVolume] = useState(book.volume_number?.toString() ?? '');
  const [genres, setGenres] = useState(book.genres.join(', '));
  const [direction, setDirection] = useState(book.reading_direction);
  const [shared, setShared] = useState(book.visibility === 'shared');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save(event: FormEvent) {
    event.preventDefault(); if (!title.trim() || busy) return;
    setBusy(true); setError(null);
    try { onSaved(await updateBookMetadata(book.id, { title: title.trim(), author: author.trim(), description: description.trim(), series_name: series.trim() || null, volume_number: volume.trim() ? Number(volume) : null, genres: genres.split(',').map((value) => value.trim()).filter(Boolean), reading_direction: direction, visibility: shared ? 'shared' : 'private' })); }
    catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }
  return <Dialog busy={busy} footer={<><Button onClick={onClose} variant="quiet">Cancel</Button><Button busy={busy} disabled={!title.trim()} form="book-metadata-form" type="submit" variant="primary">Save details</Button></>} onClose={onClose} open size="md" title="Edit book details"><form className="books-form" id="book-metadata-form" onSubmit={(event) => { void save(event); }}><Field label="Title" required>{(ids) => <Input {...fieldProps(ids)} maxLength={500} onChange={(event) => setTitle(event.target.value)} required value={title} />}</Field><Field label="Author">{(ids) => <Input {...fieldProps(ids)} maxLength={300} onChange={(event) => setAuthor(event.target.value)} value={author} />}</Field><Field label="Synopsis">{(ids) => <Textarea {...fieldProps(ids)} maxLength={10000} onChange={(event) => setDescription(event.target.value)} rows={6} value={description} />}</Field><div className="books-form-pair"><Field label="Series">{(ids) => <Input {...fieldProps(ids)} maxLength={300} onChange={(event) => setSeries(event.target.value)} value={series} />}</Field><Field label="Volume">{(ids) => <Input {...fieldProps(ids)} min={0} onChange={(event) => setVolume(event.target.value)} step="0.1" type="number" value={volume} />}</Field></div><Field label="Genres" hint="Separate genres with commas.">{(ids) => <Input {...fieldProps(ids)} maxLength={1000} onChange={(event) => setGenres(event.target.value)} value={genres} />}</Field>{book.kind === 'manga' ? <Field label="Reading direction">{(ids) => <Select {...fieldProps(ids)} onChange={(event) => setDirection(event.target.value as 'ltr' | 'rtl')} value={direction}><option value="rtl">Right to left</option><option value="ltr">Left to right</option></Select>}</Field> : null}<Checkbox checked={shared} label="Share this book with the household" onChange={(event) => setShared(event.target.checked)} />{error ? <p className="books-form-error" role="alert">{error}</p> : null}</form></Dialog>;
}
