import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { BookProse, BookReader } from './BookReader';
import { chooseSelect } from '../../test/selectChoice';
import { book, ebookManifest } from './testFixtures';

const api = vi.hoisted(() => ({
  getSessionUserId: vi.fn(), getBookPageBytes: vi.fn(), getBookChapter: vi.fn(), getBookReader: vi.fn(), updateBookState: vi.fn(),
}));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), getSessionUserId: api.getSessionUserId }));
vi.mock('./booksApi', async (importOriginal) => ({ ...(await importOriginal<typeof import('./booksApi')>()), getBookPageBytes: api.getBookPageBytes, getBookChapter: api.getBookChapter, getBookReader: api.getBookReader, updateBookState: api.updateBookState }));

const nextFrame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

beforeEach(() => {
  vi.clearAllMocks();
  Reflect.deleteProperty(navigator, 'connection');
  api.getSessionUserId.mockReturnValue('books-member');
  api.getBookPageBytes.mockResolvedValue(new Blob(['page'], { type: 'image/png' }));
  let blobNumber = 0;
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => `blob:page-${++blobNumber}`) });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
  api.getBookReader.mockResolvedValue(ebookManifest());
  api.getBookChapter.mockImplementation(async (_id: string, index: number) => ({ nodes: [{ tag: 'h2', text: index ? 'The pass' : 'Departure' }, { tag: 'p', text: 'Safe structured prose.' }] }));
  api.updateBookState.mockImplementation(async (_id: string, patch: object) => book({ progress: { ...book().progress, ...(patch as object) } }));
});

describe('BookProse', () => {
  it('renders only structured prose and local book resources', () => {
    const { container } = render(<BookProse bookId="ebook-1" nodes={[
      { tag: 'p', text: 'A safe paragraph.' },
      { tag: 'script', text: 'window.evil()' },
      { tag: 'p', id: 'chapter-start', text: 'A destination in the book.' },
      { tag: 'a', anchor: 'chapter-start', text: 'Jump to the destination.' },
      { tag: 'img', member: 'art/cover.png', alt: 'Cover' },
      { tag: 'img', member: 'https://elsewhere.invalid/track.png', alt: 'Still local member' },
      { tag: 'span', text: '<img onerror=alert(1)>' } as never,
    ]} onLink={vi.fn()} />);
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('[onerror]')).toBeNull();
    expect(container.textContent).toContain('<img onerror=alert(1)>');
    const images = container.querySelectorAll('img');
    expect(images).toHaveLength(2);
    for (const image of images) expect(image.getAttribute('src')).toMatch(/^\/api\/books\/ebook-1\/resources\?member=/);
    expect(container.querySelector('#book-fragment-chapter-start')?.textContent).toBe('A destination in the book.');
    expect(container.querySelector('a')?.getAttribute('href')).toBe('#book-fragment-chapter-start');
  });
});

describe('BookReader', () => {
  it('fetches a native page once and reuses one encoded lookahead without decoding hidden pages', async () => {
    const comic = book({ format: 'cbr', kind: 'manga', page_count: 120, chapter_count: 0 });
    api.getBookReader.mockResolvedValue({ book: comic, chapters: [], page_count: 120, reading_direction: 'ltr' });
    const { unmount } = render(<BookReader bookId="comic-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);
    const first = await screen.findByRole('img', { name: 'Page 1 of 120' });
    await waitFor(() => expect(api.getBookPageBytes).toHaveBeenCalledTimes(1));
    fireEvent.load(first);
    await waitFor(() => expect(api.getBookPageBytes).toHaveBeenCalledTimes(2));
    expect(document.querySelectorAll('.books-manga-pages img')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    const second = await screen.findByRole('img', { name: 'Page 2 of 120' });
    expect(second.getAttribute('src')).toBe('blob:page-2');
    expect(api.getBookPageBytes).toHaveBeenCalledTimes(2);
    fireEvent.load(second);
    await waitFor(() => expect(api.getBookPageBytes).toHaveBeenCalledTimes(3));
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect((await screen.findByRole('img', { name: 'Page 1 of 120' })).getAttribute('src')).toBe('blob:page-1');
    expect(api.getBookPageBytes).toHaveBeenCalledTimes(3);
    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:page-1');
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:page-2');
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:page-3');
  });
  it('does not transfer speculative pages when data saving is enabled', async () => {
    Object.defineProperty(navigator, 'connection', { configurable: true, value: { saveData: true } });
    const comic = book({ format: 'cbr', kind: 'manga', page_count: 120, chapter_count: 0 });
    api.getBookReader.mockResolvedValue({ book: comic, chapters: [], page_count: 120, reading_direction: 'ltr' });
    render(<BookReader bookId="comic-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);
    fireEvent.load(await screen.findByRole('img', { name: 'Page 1 of 120' }));
    await nextFrame();
    expect(api.getBookPageBytes).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByRole('img', { name: 'Page 2 of 120' });
    expect(api.getBookPageBytes).toHaveBeenCalledTimes(2);
  });

  it('cancels an abandoned page lookahead on a jump and releases large off-screen blobs', async () => {
    const comic = book({ format: 'cbr', kind: 'manga', page_count: 120, chapter_count: 0 });
    api.getBookReader.mockResolvedValue({ book: comic, chapters: [], page_count: 120, reading_direction: 'ltr' });
    const largePage = new Blob([new Uint8Array(24 * 1024 * 1024)], { type: 'image/png' });
    let finishLookahead!: (value: Blob) => void;
    api.getBookPageBytes.mockImplementation(async (_id: string, index: number) => index === 2
      ? new Promise<Blob>((resolve) => { finishLookahead = resolve; }) : largePage);
    const { unmount } = render(<BookReader bookId="comic-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);
    fireEvent.load(await screen.findByRole('img', { name: 'Page 1 of 120' }));
    await waitFor(() => expect(api.getBookPageBytes).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    fireEvent.load(await screen.findByRole('img', { name: 'Page 2 of 120' }));
    await waitFor(() => expect(api.getBookPageBytes).toHaveBeenCalledTimes(3));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:page-1');
    const abandoned = api.getBookPageBytes.mock.calls[2][2] as AbortSignal;
    fireEvent.change(screen.getByLabelText('Reading position'), { target: { value: '80' } });
    await screen.findByRole('img', { name: 'Page 81 of 120' });
    expect(abandoned.aborted).toBe(true);
    finishLookahead(largePage);
    await nextFrame();
    expect(URL.createObjectURL).toHaveBeenCalledTimes(3);
    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:page-2');
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:page-3');
  });

  it('opens a resumed large comic without an immediate write and does not hold the next page behind the recency save', async () => {
    const resumed = book({ format: 'cbz', kind: 'manga', page_count: 1000, chapter_count: 0, progress: { ...book().progress, unit_index: 499, percent: 50, updated_at: '2026-10-01T00:00:00Z' } });
    api.getBookReader.mockResolvedValue({ book: resumed, chapters: [], page_count: 1000, reading_direction: 'ltr' });
    const finishes: Array<(value: ReturnType<typeof book>) => void> = [];
    api.updateBookState.mockImplementation(() => new Promise((resolve) => { finishes.push(resolve); }));
    render(<BookReader bookId="comic-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);

    await screen.findByRole('img', { name: 'Page 500 of 1000' });
    expect(document.querySelectorAll('.books-manga-pages img')).toHaveLength(1);
    expect(api.updateBookState).not.toHaveBeenCalled();
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledWith('comic-1', { unit_index: 499, offset: 0 }, true));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByRole('img', { name: 'Page 501 of 1000' });
    expect(api.updateBookState).toHaveBeenCalledOnce();
    await new Promise((resolve) => setTimeout(resolve, 600));
    expect(api.updateBookState).toHaveBeenCalledOnce();
    finishes[0](resumed);
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledWith('comic-1', { unit_index: 500, offset: 0 }, true));
    finishes[1](resumed);
  });

  it('coalesces rapid native page turns into the latest checkpoint', async () => {
    const scanned = book({ format: 'pdf', kind: 'ebook', page_count: 1000, chapter_count: 0, progress: { ...book().progress, updated_at: '2026-10-01T00:00:00Z' } });
    api.getBookReader.mockResolvedValue({ book: scanned, chapters: [], page_count: 1000, reading_direction: 'ltr' });
    api.updateBookState.mockImplementation(async (_id: string, patch: object) => ({ ...scanned, progress: { ...scanned.progress, ...patch } }));
    render(<BookReader bookId="pdf-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);

    await screen.findByRole('img', { name: 'Page 1 of 1000' });
    for (let page = 2; page <= 4; page += 1) {
      fireEvent.click(screen.getByRole('button', { name: 'Next' }));
      await screen.findByRole('img', { name: `Page ${page} of 1000` });
    }
    expect(api.updateBookState).not.toHaveBeenCalled();
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledWith('pdf-1', { unit_index: 3, offset: 0 }, true));
    expect(api.updateBookState).toHaveBeenCalledOnce();
  });

  it('keeps at most one active and one latest checkpoint while saves are slow', async () => {
    const scanned = book({ format: 'pdf', kind: 'ebook', page_count: 1000, chapter_count: 0, progress: { ...book().progress, updated_at: '2026-10-01T00:00:00Z' } });
    api.getBookReader.mockResolvedValue({ book: scanned, chapters: [], page_count: 1000, reading_direction: 'ltr' });
    const finishes: Array<(value: ReturnType<typeof book>) => void> = [];
    api.updateBookState.mockImplementation(() => new Promise((resolve) => { finishes.push(resolve); }));
    render(<BookReader bookId="pdf-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);

    await screen.findByRole('img', { name: 'Page 1 of 1000' });
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledTimes(1));
    expect(api.updateBookState).toHaveBeenNthCalledWith(1, 'pdf-1', { unit_index: 1, offset: 0 }, true);
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await new Promise((resolve) => setTimeout(resolve, 600));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await new Promise((resolve) => setTimeout(resolve, 600));
    expect(api.updateBookState).toHaveBeenCalledTimes(1);

    finishes[0](scanned);
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledTimes(2));
    expect(api.updateBookState).toHaveBeenLastCalledWith('pdf-1', { unit_index: 3, offset: 0 }, true);
    finishes[1](scanned);
  });

  it('preserves a queued Finish book command when pagehide flushes a slow checkpoint', async () => {
    const finalPage = book({ format: 'pdf', kind: 'ebook', page_count: 2, chapter_count: 0, progress: { ...book().progress, percent: 50, updated_at: '2026-10-01T00:00:00Z' } });
    api.getBookReader.mockResolvedValue({ book: finalPage, chapters: [], page_count: 2, reading_direction: 'ltr' });
    const finishes: Array<(value: ReturnType<typeof book>) => void> = [];
    api.updateBookState.mockImplementation(() => new Promise((resolve) => { finishes.push(resolve); }));
    render(<BookReader bookId="pdf-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);

    await screen.findByRole('img', { name: 'Page 1 of 2' });
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByRole('img', { name: 'Page 2 of 2' });
    fireEvent.click(screen.getByRole('button', { name: 'Finish book' }));
    fireEvent(window, new Event('pagehide'));
    finishes[0](finalPage);

    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledTimes(2));
    expect(api.updateBookState).toHaveBeenLastCalledWith('pdf-1', { unit_index: 1, offset: 0, completed: true }, true);
    finishes[1]({ ...finalPage, progress: { ...finalPage.progress, completed: true } });
  });

  it('sends the final page-zero reset when reading a completed native book again', async () => {
    const completed = book({ format: 'pdf', kind: 'ebook', page_count: 2, chapter_count: 0, progress: { ...book().progress, unit_index: 1, percent: 100, completed: true, updated_at: '2026-10-01T00:00:00Z' } });
    api.getBookReader.mockResolvedValue({ book: completed, chapters: [], page_count: 2, reading_direction: 'ltr' });
    let finishReset!: (value: ReturnType<typeof book>) => void;
    api.updateBookState.mockImplementation(async (_id: string, patch: { unit_index?: number; offset?: number; completed?: boolean }) => {
      if (patch.unit_index === 0 && patch.completed === false) return new Promise((resolve) => { finishReset = resolve; });
      return { ...completed, progress: { ...completed.progress, ...patch } };
    });
    render(<BookReader bookId="pdf-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);

    await screen.findByRole('img', { name: 'Page 2 of 2' });
    fireEvent.click(screen.getByRole('button', { name: 'Read again' }));
    await screen.findByRole('img', { name: 'Page 1 of 2' });
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledTimes(2));
    expect(api.updateBookState).toHaveBeenLastCalledWith('pdf-1', { unit_index: 0, offset: 0, completed: false }, true);
    expect(screen.getByRole('button', { name: 'Next' })).toHaveProperty('disabled', true);
    finishReset({ ...completed, progress: { ...completed.progress, unit_index: 0, offset: 0, completed: false } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next' })).toHaveProperty('disabled', false));
  });

  it('ignores a completed checkpoint response from the previously open book', async () => {
    const first = book({ id: 'comic-1', title: 'First volume', format: 'cbz', kind: 'manga', page_count: 20, chapter_count: 0, progress: { ...book().progress, updated_at: '2026-10-01T00:00:00Z' } });
    const second = book({ id: 'comic-2', title: 'Second volume', format: 'cbz', kind: 'manga', page_count: 20, chapter_count: 0, progress: { ...book().progress, updated_at: '2026-10-02T00:00:00Z' } });
    api.getBookReader.mockResolvedValueOnce({ book: first, chapters: [], page_count: 20, reading_direction: 'ltr' }).mockResolvedValueOnce({ book: second, chapters: [], page_count: 20, reading_direction: 'ltr' });
    let finishSave!: (value: ReturnType<typeof book>) => void;
    api.updateBookState.mockImplementation(() => new Promise((resolve) => { finishSave = resolve; }));
    const changed = vi.fn();
    const view = render(<BookReader bookId="comic-1" onBack={vi.fn()} onBookChange={changed} userId="books-member" />);
    await screen.findByText('First volume');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledOnce());

    view.rerender(<BookReader bookId="comic-2" onBack={vi.fn()} onBookChange={changed} userId="books-member" />);
    await screen.findByText('Second volume');
    finishSave(first);
    await waitFor(() => expect(screen.getByText('Second volume')).not.toBeNull());
    expect(screen.queryByText('First volume')).toBeNull();
    expect(changed).not.toHaveBeenCalled();
  });

  it('creates Continue reading state after opening a new native book', async () => {
    const unread = book({ format: 'cbr', kind: 'manga', page_count: 20, chapter_count: 0 });
    api.getBookReader.mockResolvedValue({ book: unread, chapters: [], page_count: 20, reading_direction: 'ltr' });
    api.updateBookState.mockImplementation(async (_id: string, patch: object) => ({ ...unread, progress: { ...unread.progress, ...patch, updated_at: '2026-10-18T00:00:00Z' } }));
    render(<BookReader bookId="comic-new" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);

    await screen.findByRole('img', { name: 'Page 1 of 20' });
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledWith('comic-new', { unit_index: 0, offset: 0 }, true));
    expect(api.updateBookState).toHaveBeenCalledOnce();
  });

  it('prefetches at most the next EPUB chapter and cancels it when the reader closes', async () => {
    const chapters = Array.from({ length: 1000 }, (_, index) => ({ label: `Chapter ${index + 1}` }));
    api.getBookReader.mockResolvedValue(ebookManifest({ chapters }));
    let finishPrefetch!: (value: { nodes: { tag: string; text: string }[] }) => void;
    let prefetchSignal: AbortSignal | undefined;
    api.getBookChapter.mockImplementation(async (_id: string, index: number, signal?: AbortSignal) => {
      if (index === 0) return { nodes: [{ tag: 'p', text: 'Opening chapter' }] };
      prefetchSignal = signal;
      return new Promise((resolve) => { finishPrefetch = resolve; });
    });
    const view = render(<BookReader bookId="ebook-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);

    await screen.findByText('Opening chapter');
    await waitFor(() => expect(api.getBookChapter).toHaveBeenCalledTimes(2));
    expect(api.getBookChapter.mock.calls.map((call) => call[1])).toEqual([0, 1]);
    view.unmount();
    expect(prefetchSignal?.aborted).toBe(true);
    finishPrefetch({ nodes: [{ tag: 'p', text: 'Never mounted' }] });
  });

  it('turns EPUB chapters with arrow keys immediately after opening', async () => {
    const browser = userEvent.setup();
    render(<BookReader bookId="ebook-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);
    await screen.findByText('Departure');
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Book chapter');
    await browser.keyboard('{ArrowRight}');
    await screen.findByText('The pass');
    await browser.keyboard('{ArrowLeft}');
    await screen.findByText('Departure');
  });

  it.each([
    ['pdf', 'ltr', 'ArrowRight', 'ArrowLeft'],
    ['cbr', 'ltr', 'ArrowRight', 'ArrowLeft'],
    ['cbz', 'rtl', 'ArrowLeft', 'ArrowRight'],
  ] as const)('turns %s pages from toolbar focus in %s order', async (format, direction, forward, backward) => {
    const browser = userEvent.setup();
    const scanned = book({ format, kind: format === 'pdf' ? 'ebook' : 'manga', page_count: 3, chapter_count: 0 });
    api.getBookReader.mockResolvedValue({ book: scanned, chapters: [], page_count: 3, reading_direction: direction });
    api.updateBookState.mockImplementation(async (_id: string, patch: object) => ({ ...scanned, progress: { ...scanned.progress, ...patch } }));
    render(<BookReader bookId="ebook-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);
    await screen.findByRole('img', { name: 'Page 1 of 3' });
    screen.getByRole('button', { name: 'Pages' }).focus();
    await browser.keyboard(`{${forward}}`);
    await screen.findByRole('img', { name: 'Page 2 of 3' });
    await browser.keyboard(`{${backward}}`);
    await screen.findByRole('img', { name: 'Page 1 of 3' });
    await browser.click(screen.getByRole('button', { name: 'Pages' }));
    const input = screen.getByRole('spinbutton', { name: 'Go to page' });
    input.focus();
    expect(fireEvent.keyDown(input, { key: forward })).toBe(true);
    expect(screen.queryByRole('img', { name: 'Page 2 of 3' })).toBeNull();
    const position = screen.getByRole('slider', { name: 'Reading position' });
    expect(fireEvent.keyDown(position, { key: forward })).toBe(true);
    expect(screen.queryByRole('img', { name: 'Page 2 of 3' })).toBeNull();
    await browser.clear(input);
    await browser.type(input, '2');
    expect(screen.queryByRole('img', { name: 'Page 2 of 3' })).toBeNull();
    await browser.keyboard('{Enter}');
    await screen.findByRole('img', { name: 'Page 2 of 3' });
    position.focus();
    fireEvent.change(position, { target: { value: '0' } });
    await screen.findByRole('img', { name: 'Page 1 of 3' });
    expect(document.activeElement).toBe(position);
  });

  it('saves the current file checkpoint before switching bundled formats', async () => {
    const bundled = book({ files: [
      { id: 'ebook-1', filename: 'Northbound.epub', format: 'epub', chapter_count: 2, page_count: 0, available: true },
      { id: 'pdf-1', filename: 'Northbound.pdf', format: 'pdf', chapter_count: 0, page_count: 30, available: true },
    ] });
    api.getBookReader.mockResolvedValue(ebookManifest({ book: bundled }));
    api.updateBookState.mockImplementation(async (_id: string, patch: object) => ({ ...bundled, progress: { ...bundled.progress, ...patch } }));
    const onFileChange = vi.fn(() => view.unmount());
    const view = render(<BookReader bookId="ebook-1" onBack={vi.fn()} onBookChange={vi.fn()} onFileChange={onFileChange} userId="books-member" />);
    await screen.findByText('Departure');
    await nextFrame();
    const chapter = screen.getByLabelText('Book chapter');
    Object.defineProperties(chapter, { scrollHeight: { configurable: true, value: 1000 }, clientHeight: { configurable: true, value: 500 }, scrollTop: { configurable: true, writable: true, value: 250 } });
    fireEvent.scroll(chapter);
    const formats = screen.getByRole('combobox', { name: 'Read format' });
    expect(fireEvent.keyDown(formats, { key: 'ArrowRight' })).toBe(true);
    expect(screen.queryByText('The pass')).toBeNull();
    chooseSelect(formats, 'PDF · Northbound.pdf');
    await waitFor(() => expect(onFileChange).toHaveBeenCalledWith('pdf-1'));
    expect(api.updateBookState).toHaveBeenCalledWith('ebook-1', { unit_index: 0, offset: 0.5 }, true);
    expect(api.updateBookState).toHaveBeenCalledOnce();
  });

  it('keeps reader progress when a reading-list response describes another format', async () => {
    const browser = userEvent.setup();
    render(<BookReader bookId="ebook-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);
    await screen.findByText('Departure');
    await nextFrame();
    api.updateBookState.mockResolvedValueOnce(book({ reading_list: true, reading_file_id: 'pdf-1', progress: { ...book().progress, unit_index: 1, percent: 100, completed: true } }));
    await browser.click(screen.getByRole('button', { name: 'Add to reading list' }));
    await screen.findByRole('button', { name: 'Remove from reading list' });
    expect(screen.getByText(/Chapter 1 of 2.*% read/).textContent).toContain('% read');
  });

  it('pages through EPUB text before changing chapters and returns to the previous chapter end', async () => {
    const browser = userEvent.setup();
    render(<BookReader bookId="ebook-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);
    await screen.findByText('Departure');
    await nextFrame();
    const chapter = screen.getByLabelText('Book chapter');
    Object.defineProperties(chapter, { scrollHeight: { configurable: true, value: 1400 }, clientHeight: { configurable: true, value: 400 }, scrollTop: { configurable: true, writable: true, value: 0 } });
    chapter.focus();
    await browser.keyboard('{ArrowRight}');
    expect(chapter.scrollTop).toBe(368);
    expect(screen.queryByText('The pass')).toBeNull();
    await browser.keyboard('{ArrowLeft}');
    expect(chapter.scrollTop).toBe(0);
    chapter.scrollTop = 1000;
    await browser.keyboard('{ArrowRight}');
    await screen.findByText('The pass');
    chapter.scrollTop = 0;
    await browser.keyboard('{ArrowLeft}');
    await screen.findByText('Departure');
    await waitFor(() => expect(chapter.scrollTop).toBe(1000));
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledWith('ebook-1', { unit_index: 0, offset: 1 }, true));
  });

  it.each([
    { case: 'empty', nodes: [] },
    { case: 'whitespace wrappers', nodes: [{ tag: 'span', text: '\n' }, { tag: 'span', children: [{ tag: 'span', text: ' ' }] }] },
  ])('shows the approved book cover for a $case first EPUB chapter', async ({ nodes }) => {
    api.getBookReader.mockResolvedValue(ebookManifest({ book: book({ cover_url: '/api/books/ebook-1/cover' }) }));
    api.getBookChapter.mockResolvedValue({ nodes });
    render(<BookReader bookId="ebook-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);
    const cover = await screen.findByRole('img', { name: 'Cover of Northbound' });
    expect(cover.getAttribute('src')).toBe('/api/books/ebook-1/cover');
  });
  it.each(['pdf', 'cbr'] as const)('reads %s with page navigation and saves page checkpoints', async (format) => {
    const browser = userEvent.setup();
    const scanned = book({ format, kind: format === 'pdf' ? 'ebook' : 'manga', page_count: 2, chapter_count: 0 });
    api.getBookReader.mockResolvedValue({ book: scanned, chapters: [], page_count: 2, reading_direction: 'ltr' });
    api.updateBookState.mockImplementation(async (_id: string, patch: object) => ({ ...scanned, progress: { ...scanned.progress, ...patch } }));
    render(<BookReader bookId="ebook-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);
    await screen.findByRole('img', { name: 'Page 1 of 2' });
    expect(api.getBookPageBytes.mock.calls.map(([id, index]) => [id, index])).toContainEqual(['ebook-1', 0]);
    await browser.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByRole('img', { name: 'Page 2 of 2' });
    expect(api.getBookPageBytes.mock.calls.map(([id, index]) => [id, index])).toContainEqual(['ebook-1', 1]);
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledWith('ebook-1', { unit_index: 1, offset: 0 }, true));
    expect(api.getBookChapter).not.toHaveBeenCalled();
  });
  it('changes reader appearance, chapters and checkpoints before returning to Books', async () => {
    const browser = userEvent.setup();
    const onBack = vi.fn();
    render(<BookReader bookId="ebook-1" onBack={onBack} onBookChange={vi.fn()} userId="books-member" />);
    await screen.findByText('Departure');
    await nextFrame();
    await browser.click(screen.getByRole('button', { name: 'Reading appearance' }));
    await browser.click(screen.getByRole('button', { name: 'Sepia' }));
    await browser.click(screen.getByRole('button', { name: 'Larger text' }));
    await browser.click(screen.getByRole('button', { name: 'Two columns' }));
    const reader = document.querySelector('.books-reader')!;
    expect(reader.getAttribute('data-appearance')).toBe('sepia');
    expect(reader.getAttribute('style')).toContain('--book-font-size: 20px');
    await browser.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('The pass');
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledWith('ebook-1', expect.objectContaining({ unit_index: 1, offset: 0 }), true));
    await browser.click(screen.getByRole('button', { name: 'Back to Books' }));
    await waitFor(() => expect(onBack).toHaveBeenCalledOnce());
  });

  it('does not save a checkpoint after the session changes accounts', async () => {
    const browser = userEvent.setup();
    render(<BookReader bookId="ebook-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);
    await screen.findByText('Departure');
    await nextFrame();
    api.getSessionUserId.mockReturnValue('next-member');
    await browser.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText('Departure')).not.toBeNull();
    expect(screen.queryByText('The pass')).toBeNull();
    expect(api.updateBookState).not.toHaveBeenCalled();
  });

  it('keeps the reader in place when a checkpoint fails, then retries before leaving Books', async () => {
    const browser = userEvent.setup();
    const onBack = vi.fn();
    render(<BookReader bookId="ebook-1" onBack={onBack} onBookChange={vi.fn()} userId="books-member" />);
    await screen.findByText('Departure');
    await nextFrame();
    const chapter = screen.getByLabelText('Book chapter');
    Object.defineProperties(chapter, { scrollHeight: { configurable: true, value: 1000 }, clientHeight: { configurable: true, value: 500 }, scrollTop: { configurable: true, writable: true, value: 250 } });
    fireEvent.scroll(chapter);
    api.updateBookState.mockRejectedValueOnce(new Error('Network unavailable.'));

    await browser.click(screen.getByRole('button', { name: 'Back to Books' }));
    expect(screen.getByText('Departure')).not.toBeNull();
    expect((await screen.findByRole('alert')).textContent).toContain('Network unavailable.');
    expect(onBack).not.toHaveBeenCalled();

    await browser.click(screen.getByRole('button', { name: 'Retry save' }));
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledTimes(2));
    await browser.click(screen.getByRole('button', { name: 'Back to Books' }));
    await waitFor(() => expect(onBack).toHaveBeenCalledOnce());
  });

  it('saves the last readable EPUB checkpoint when the next chapter fails to load', async () => {
    const browser = userEvent.setup();
    const onBack = vi.fn();
    api.getBookChapter.mockImplementation(async (_id: string, index: number) => {
      if (index === 1) throw new Error('Chapter unavailable.');
      return { nodes: [{ tag: 'p', text: 'Readable chapter' }] };
    });
    render(<BookReader bookId="ebook-1" onBack={onBack} onBookChange={vi.fn()} userId="books-member" />);
    await screen.findByText('Readable chapter');
    await nextFrame();
    const chapter = screen.getByLabelText('Book chapter');
    Object.defineProperties(chapter, { scrollHeight: { configurable: true, value: 1000 }, clientHeight: { configurable: true, value: 500 }, scrollTop: { configurable: true, writable: true, value: 250 } });
    fireEvent.scroll(chapter);
    chapter.scrollTop = 500;
    await browser.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Chapter unavailable.');
    await browser.click(screen.getByRole('button', { name: 'Back to Books' }));

    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledWith('ebook-1', { unit_index: 0, offset: 0.5 }, true));
    await waitFor(() => expect(onBack).toHaveBeenCalledOnce());
  });

  it('saves an ebook scroll checkpoint after a reading pause', async () => {
    render(<BookReader bookId="ebook-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);
    await screen.findByText('Departure');
    await nextFrame();
    const chapter = document.querySelector<HTMLElement>('[aria-label="Book chapter"]')!;
    Object.defineProperties(chapter, { scrollHeight: { configurable: true, value: 1000 }, clientHeight: { configurable: true, value: 500 }, scrollTop: { configurable: true, writable: true, value: 250 } });
    fireEvent.scroll(chapter);
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledWith('ebook-1', { unit_index: 0, offset: 0.5 }, true), { timeout: 2000 });
  });
});
