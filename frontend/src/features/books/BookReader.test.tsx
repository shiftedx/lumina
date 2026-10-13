import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { BookProse, BookReader } from './BookReader';
import { book, ebookManifest } from './testFixtures';

const api = vi.hoisted(() => ({
  getSessionUserId: vi.fn(), getBookChapter: vi.fn(), getBookReader: vi.fn(), updateBookState: vi.fn(),
}));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), getSessionUserId: api.getSessionUserId }));
vi.mock('./booksApi', async (importOriginal) => ({ ...(await importOriginal<typeof import('./booksApi')>()), getBookChapter: api.getBookChapter, getBookReader: api.getBookReader, updateBookState: api.updateBookState }));

beforeEach(() => {
  vi.clearAllMocks();
  api.getSessionUserId.mockReturnValue('books-member');
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
    const browser = userEvent.setup();
    const bundled = book({ files: [
      { id: 'ebook-1', filename: 'Northbound.epub', format: 'epub', chapter_count: 2, page_count: 0, available: true },
      { id: 'pdf-1', filename: 'Northbound.pdf', format: 'pdf', chapter_count: 0, page_count: 30, available: true },
    ] });
    api.getBookReader.mockResolvedValue(ebookManifest({ book: bundled }));
    api.updateBookState.mockImplementation(async (_id: string, patch: object) => ({ ...bundled, progress: { ...bundled.progress, ...patch } }));
    const onFileChange = vi.fn(() => view.unmount());
    const view = render(<BookReader bookId="ebook-1" onBack={vi.fn()} onBookChange={vi.fn()} onFileChange={onFileChange} userId="books-member" />);
    await screen.findByText('Departure');
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalled());
    api.updateBookState.mockClear();
    const formats = screen.getByRole('combobox', { name: 'Read format' });
    expect(fireEvent.keyDown(formats, { key: 'ArrowRight' })).toBe(true);
    expect(screen.queryByText('The pass')).toBeNull();
    await browser.selectOptions(formats, 'pdf-1');
    await waitFor(() => expect(onFileChange).toHaveBeenCalledWith('pdf-1'));
    expect(api.updateBookState).toHaveBeenCalledWith('ebook-1', { unit_index: 0, offset: 0 }, false);
    expect(api.updateBookState).toHaveBeenCalledOnce();
  });

  it('keeps reader progress when a reading-list response describes another format', async () => {
    const browser = userEvent.setup();
    render(<BookReader bookId="ebook-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);
    await screen.findByText('Departure');
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalled());
    api.updateBookState.mockResolvedValueOnce(book({ reading_list: true, reading_file_id: 'pdf-1', progress: { ...book().progress, unit_index: 1, percent: 100, completed: true } }));
    await browser.click(screen.getByRole('button', { name: 'Add to reading list' }));
    await screen.findByRole('button', { name: 'Remove from reading list' });
    expect(screen.getByText(/Chapter 1 of 2.*% read/).textContent).toContain('% read');
  });

  it('pages through EPUB text before changing chapters and returns to the previous chapter end', async () => {
    const browser = userEvent.setup();
    render(<BookReader bookId="ebook-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);
    await screen.findByText('Departure');
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalled());
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
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledWith('ebook-1', { unit_index: 0, offset: 1 }, false));
  });

  it('shows the approved book cover when the first EPUB chapter has no inert content', async () => {
    api.getBookReader.mockResolvedValue(ebookManifest({ book: book({ cover_url: '/api/books/ebook-1/cover' }) }));
    api.getBookChapter.mockResolvedValue({ nodes: [] });
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
    expect((await screen.findByRole('img', { name: 'Page 1 of 2' })).getAttribute('src')).toBe('/api/books/ebook-1/pages/0');
    await browser.click(screen.getByRole('button', { name: 'Next' }));
    expect((await screen.findByRole('img', { name: 'Page 2 of 2' })).getAttribute('src')).toBe('/api/books/ebook-1/pages/1');
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledWith('ebook-1', { unit_index: 1, offset: 0 }, false));
    expect(api.getBookChapter).not.toHaveBeenCalled();
  });
  it('changes reader appearance, chapters and checkpoints before returning to Books', async () => {
    const browser = userEvent.setup();
    const onBack = vi.fn();
    render(<BookReader bookId="ebook-1" onBack={onBack} onBookChange={vi.fn()} userId="books-member" />);
    await screen.findByText('Departure');
    await browser.click(screen.getByRole('button', { name: 'Reading appearance' }));
    await browser.click(screen.getByRole('button', { name: 'Sepia' }));
    await browser.click(screen.getByRole('button', { name: 'Larger text' }));
    await browser.click(screen.getByRole('button', { name: 'Two columns' }));
    const reader = document.querySelector('.books-reader')!;
    expect(reader.getAttribute('data-appearance')).toBe('sepia');
    expect(reader.getAttribute('style')).toContain('--book-font-size: 20px');
    await browser.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('The pass');
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledWith('ebook-1', expect.objectContaining({ unit_index: 1, offset: 0 }), false));
    await browser.click(screen.getByRole('button', { name: 'Back to Books' }));
    await waitFor(() => expect(onBack).toHaveBeenCalledOnce());
  });

  it('does not save a checkpoint after the session changes accounts', async () => {
    const browser = userEvent.setup();
    render(<BookReader bookId="ebook-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);
    await screen.findByText('Departure');
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalled());
    api.updateBookState.mockClear();
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
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalled());
    api.updateBookState.mockClear();
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

  it('saves an ebook scroll checkpoint after a reading pause', async () => {
    render(<BookReader bookId="ebook-1" onBack={vi.fn()} onBookChange={vi.fn()} userId="books-member" />);
    await screen.findByText('Departure');
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalled());
    api.updateBookState.mockClear();
    const chapter = document.querySelector<HTMLElement>('[aria-label="Book chapter"]')!;
    Object.defineProperties(chapter, { scrollHeight: { configurable: true, value: 1000 }, clientHeight: { configurable: true, value: 500 }, scrollTop: { configurable: true, writable: true, value: 250 } });
    fireEvent.scroll(chapter);
    await waitFor(() => expect(api.updateBookState).toHaveBeenCalledWith('ebook-1', { unit_index: 0, offset: 0.5 }, false), { timeout: 2000 });
  });
});
