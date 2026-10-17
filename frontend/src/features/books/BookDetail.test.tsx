import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';

import { BookDetail } from './BookDetail';
import { book } from './testFixtures';

it('shows one book title, paragraph synopsis, filenames and separate format reading controls', async () => {
  const browser = userEvent.setup();
  const onReadFile = vi.fn();
  const onDelete = vi.fn();
  const onCombine = vi.fn();
  const onSeparate = vi.fn();
  render(<BookDetail book={book({ description: 'A voyage north.\n\nA second paragraph.', files: [
    { id: 'ebook-1', filename: 'Northbound - Mira Vale.epub', format: 'epub', available: true, chapter_count: 2, page_count: 0 },
    { id: 'pdf-1', filename: 'Northbound - Mira Vale.pdf', format: 'pdf', available: true, chapter_count: 0, page_count: 30 },
  ] })} onCombine={onCombine} onSeparate={onSeparate} onDelete={onDelete} onEdit={vi.fn()} onRead={vi.fn()} onReadFile={onReadFile} onSeries={vi.fn()} onShelf={vi.fn()} onState={vi.fn()} />);
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  const synopsis = screen.getByRole('region', { name: 'Synopsis' });
  expect(within(synopsis).getByText('A voyage north.').tagName).toBe('P');
  expect(within(synopsis).getByText('A second paragraph.').tagName).toBe('P');
  expect(screen.getByText('Northbound - Mira Vale.epub')).toBeTruthy();
  expect(screen.getByText('Northbound - Mira Vale.pdf')).toBeTruthy();
  await browser.click(screen.getByRole('button', { name: /^Read PDF$/ }));
  expect(onReadFile).toHaveBeenCalledWith('pdf-1');
  await browser.click(screen.getByRole('button', { name: 'Separate Northbound - Mira Vale.pdf' }));
  expect(onSeparate).toHaveBeenCalledWith('pdf-1');
  await browser.click(screen.getByRole('button', { name: 'Combine entries' }));
  expect(onCombine).toHaveBeenCalledOnce();
  await browser.click(screen.getByRole('button', { name: 'Move to trash' }));
  expect(onDelete).toHaveBeenCalledOnce();
});

it('uses page counts for PDF ebooks and hides management controls for shared readers', () => {
  render(<BookDetail book={book({ format: 'pdf', page_count: 30, chapter_count: 0, can_edit: false, description: '', language: 'en' })} onCombine={vi.fn()} onDelete={vi.fn()} onEdit={vi.fn()} onRead={vi.fn()} onReadFile={vi.fn()} onSeries={vi.fn()} onShelf={vi.fn()} onState={vi.fn()} />);
  expect(screen.getByText('30 pages')).toBeTruthy();
  expect(screen.getByText('English')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Move to trash' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Combine entries' })).toBeNull();
});
