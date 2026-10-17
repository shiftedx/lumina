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
