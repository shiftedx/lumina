import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ listLibraryNotes: vi.fn(), createLibraryNote: vi.fn(), updateLibraryNote: vi.fn(), deleteLibraryNote: vi.fn() }));
vi.mock('./api', () => api);

import { HouseholdNotesPanel } from './features/watch/HouseholdNotesPanel';
import type { LibraryNote } from './types';

afterEach(() => { vi.clearAllMocks(); });

const note = (patch: Partial<LibraryNote>): LibraryNote => ({ id: 'n', item_id: 'film', visibility: 'private', body: '', is_owner: true, can_delete: true, created_at: '2026-01-01T00:00:00', ...patch });

describe('HouseholdNotesPanel', () => {
  it('test_notes_timestamp_and_seek: captures player time, sorts by timestamp and seeks', async () => {
    const browser = userEvent.setup();
    const onSeek = vi.fn();
    api.listLibraryNotes.mockResolvedValue([note({ id: 'late', body: 'Late', timestamp_ms: 90_000 })]);
    api.createLibraryNote.mockImplementation(async (_id: string, payload: { body: string; visibility: 'private' | 'household'; timestamp_ms: number | null }) => note({ id: 'early', ...payload }));
    render(<HouseholdNotesPanel getTime={() => 12.34} itemId="film" onSeek={onSeek} />);
    await screen.findByText('Late');

    await browser.click(screen.getByRole('button', { name: 'Add current time' }));
    expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'New note' }));
    await browser.keyboard('Great line');
    await browser.click(screen.getByRole('switch', { name: 'Share with household' }));
    await browser.click(screen.getByRole('button', { name: 'Save note' }));
    expect(api.createLibraryNote).toHaveBeenCalledWith('film', { body: 'Great line', visibility: 'household', timestamp_ms: 12340 });

    const items = within(screen.getByRole('list', { name: 'Notes on this item' })).getAllByRole('article');
    expect(items.map((entry) => entry.getAttribute('aria-label'))).toEqual(['Household note from You', 'Private note from You']);
    await browser.click(screen.getByRole('button', { name: 'Seek to 1:30' }));
    expect(onSeek).toHaveBeenCalledWith(90);
  });

  it('test_notes_draft_preserved: failed save keeps the draft and edit form', async () => {
    const browser = userEvent.setup();
    api.listLibraryNotes.mockResolvedValue([note({ id: 'mine', body: 'Original' })]);
    api.createLibraryNote.mockRejectedValue(new Error('Server unavailable'));
    api.updateLibraryNote.mockRejectedValue(new Error('Server unavailable'));
    render(<HouseholdNotesPanel getTime={() => 0} itemId="film" onSeek={vi.fn()} />);
    const article = await screen.findByRole('article', { name: 'Private note from You' });

    await browser.type(screen.getByRole('textbox', { name: 'New note' }), 'Keep me');
    await browser.click(screen.getByRole('button', { name: 'Save note' }));
    expect((await screen.findByRole('alert')).textContent).toBe('Server unavailable');
    expect((screen.getByRole('textbox', { name: 'New note' }) as HTMLTextAreaElement).value).toBe('Keep me');

    await browser.click(within(article).getByRole('button', { name: 'Edit' }));
    await browser.type(within(article).getByRole('textbox', { name: 'Note text' }), ' revised');
    await browser.click(within(article).getByRole('button', { name: 'Save' }));
    expect((within(article).getByRole('textbox', { name: 'Note text' }) as HTMLTextAreaElement).value).toBe('Original revised');
  });

  it('test_notes_untrusted_content_inert: renders bodies as text and hides edit for others', async () => {
    api.listLibraryNotes.mockResolvedValue([note({ id: 'other', body: '<img src=x onerror=alert(1)>', visibility: 'household', is_owner: false, can_delete: false, author_display_name: 'Sam' })]);
    render(<HouseholdNotesPanel getTime={() => 0} itemId="film" onSeek={vi.fn()} />);
    const article = await screen.findByRole('article', { name: 'Household note from Sam' });
    expect(article.querySelector('img')).toBeNull();
    expect(within(article).getByText('<img src=x onerror=alert(1)>')).not.toBeNull();
    expect(within(article).queryByRole('button')).toBeNull();
  });
});
