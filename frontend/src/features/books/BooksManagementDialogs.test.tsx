import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';

import { BooksTrashDialog, CombineBooksDialog } from './BooksManagementDialogs';
import { book } from './testFixtures';

const api = vi.hoisted(() => ({ listBooks: vi.fn(), combineBookEntries: vi.fn(), listBookTrash: vi.fn(), restoreBook: vi.fn() }));
vi.mock('./booksApi', async (original) => ({ ...(await original<typeof import('./booksApi')>()), ...api }));
beforeEach(() => vi.clearAllMocks());

it('combines chosen entries and retains the current book as the canonical entry', async () => {
  const browser = userEvent.setup();
  const onCombined = vi.fn();
  api.listBooks.mockResolvedValue({ items: [book(), book({ id: 'pdf-1', title: 'Northbound PDF', filename: 'Northbound.pdf', format: 'pdf' })] });
  api.combineBookEntries.mockResolvedValue(book());
  render(<CombineBooksDialog book={book()} onClose={vi.fn()} onCombined={onCombined} />);
  await browser.click(await screen.findByRole('checkbox', { name: 'Northbound PDF' }));
  await browser.click(screen.getByRole('button', { name: 'Combine 2 entries' }));
  expect(api.combineBookEntries).toHaveBeenCalledWith('ebook-1', ['ebook-1', 'pdf-1']);
  await waitFor(() => expect(onCombined).toHaveBeenCalledWith(expect.objectContaining({ id: 'ebook-1' })));
});

it('keeps the combine dialog and selected entries when the request fails', async () => {
  const browser = userEvent.setup();
  api.listBooks.mockResolvedValue({ items: [book({ id: 'pdf-1', title: 'Northbound PDF' })] });
  api.combineBookEntries.mockRejectedValue(new Error('The entry changed.'));
  const onCombined = vi.fn();
  render(<CombineBooksDialog book={book()} onClose={vi.fn()} onCombined={onCombined} />);
  await browser.click(await screen.findByRole('checkbox', { name: 'Northbound PDF' }));
  await browser.click(screen.getByRole('button', { name: 'Combine 2 entries' }));
  expect((await screen.findByRole('alert')).textContent).toContain('The entry changed.');
  expect(onCombined).not.toHaveBeenCalled();
  expect(screen.getByRole<HTMLInputElement>('checkbox', { name: 'Northbound PDF' }).checked).toBe(true);
});

it('restores a removed book without removing its retained files', async () => {
  const browser = userEvent.setup();
  api.listBookTrash.mockResolvedValue([book({ filename: 'Northbound.epub' })]);
  api.restoreBook.mockResolvedValue(book());
  const onRestored = vi.fn();
  render(<BooksTrashDialog onClose={vi.fn()} onRestored={onRestored} />);
  await screen.findByText('Northbound.epub');
  await browser.click(screen.getByRole('button', { name: 'Restore' }));
  await screen.findByText('Your trash is empty.');
  expect(api.restoreBook).toHaveBeenCalledWith('ebook-1');
  expect(onRestored).toHaveBeenCalledOnce();
});
