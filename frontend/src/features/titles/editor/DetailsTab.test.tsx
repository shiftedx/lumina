import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { UserProfile } from '../../../types';
import { ToastProvider } from '../../../ui';
import { cachedTitle, loadTitle } from '../../gallery/titleCache';
import { docFixture, fieldState } from './editorFixtures';

const api = vi.hoisted(() => ({ getTitleMetadata: vi.fn(), saveMetadataEdits: vi.fn(), undoMetadataBatch: vi.fn(), getMetadataVocabulary: vi.fn(), getTitle: vi.fn() }));
vi.mock('../../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../../api')>()), ...api }));
const { ApiRequestError } = await import('../../../api');
const { EditorPage } = await import('./EditorPage');

const owner: UserProfile = { id: 'u1', username: 'o', display_name: 'Owner', role: 'admin', is_active: true, can_edit_details: true };
const viewer: UserProfile = { ...owner, role: 'viewer' };
afterEach(() => vi.resetAllMocks());
async function open(user = owner, doc = docFixture('movie')) {
  api.getTitleMetadata.mockResolvedValue(doc);
  api.getMetadataVocabulary.mockResolvedValue([]);
  render(<ToastProvider><EditorPage id="t1" onBack={() => undefined} onTab={() => undefined} tab="details" user={user} /></ToastProvider>);
  await screen.findByRole('textbox', { name: 'Overview' });
}
const saveButton = () => screen.getByRole('button', { name: /^Save/ }) as HTMLButtonElement;
const type = (text: string) => userEvent.type(screen.getByRole('textbox', { name: 'Overview' }), text);

describe('DetailsTab', () => {
  it('shows only the fields the document has, in catalogue order', async () => {
    await open();
    const keys = [...document.querySelectorAll('.ed-row')].map((el) => el.getAttribute('data-field'));
    expect(keys).toEqual(['name', 'year', 'overview', 'genres']);
  });
  it('Lock this item is a Switch with the spec description and sits in the draft', async () => {
    await open();
    expect(screen.getByText(/Scans and refreshes won't change anything on this title\. Your edits still save\./)).toBeTruthy();
    const toggle = screen.getByRole('switch', { name: 'Lock this item' });
    await userEvent.click(toggle);
    expect(saveButton().textContent).toBe('Save 1 change');
    await userEvent.click(toggle);
    expect(saveButton().textContent).toBe('Save changes');
    expect(saveButton().disabled).toBe(true);
  });
  it('hides Preview refresh from members and from titles that cannot be matched', async () => {
    await open(viewer);
    expect(screen.queryByRole('button', { name: 'Preview refresh' })).toBeNull();
  });
  it('shows Preview refresh to an owner of a matchable title', async () => {
    await open();
    expect(screen.getByRole('button', { name: 'Preview refresh' })).toBeTruthy();
  });
  it('saves an edit with its base, then offers Undo', async () => {
    api.getTitle.mockResolvedValue({ id: 't1' });
    await loadTitle('t1');
    expect(cachedTitle('t1')).toBeTruthy();
    await open();
    api.saveMetadataEdits.mockResolvedValue({ batch_id: 'b1', titles: [docFixture('movie')], conflicts: [] });
    api.undoMetadataBatch.mockResolvedValue({ batch_id: 'b2', restored: 1, skipped: [] });
    await type('!');
    expect(saveButton().textContent).toBe('Save 1 change');
    await userEvent.click(saveButton());
    expect(api.saveMetadataEdits).toHaveBeenCalledWith([{ title_id: 't1', changes: { overview: { value: 'Work and life are split.!', base: 'Work and life are split.' } } }]);
    expect(await screen.findByText('Saved.')).toBeTruthy();
    expect(cachedTitle('t1')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(api.undoMetadataBatch).toHaveBeenCalledWith('b1');
  });
  it('shows the conflict banner and keeps the draft', async () => {
    await open();
    const conflict = { batch_id: '', titles: [], conflicts: [{ title_id: 't1', fields: ['overview'], current: { overview: fieldState('Theirs', 'user') } }] };
    api.saveMetadataEdits.mockResolvedValueOnce(conflict);
    await type('!');
    await userEvent.click(saveButton());
    expect(await screen.findByText('Someone changed Overview while you were editing.')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Load their version' }));
    expect((screen.getByRole('textbox', { name: 'Overview' }) as HTMLTextAreaElement).value).toBe('Theirs');
    expect(saveButton().disabled).toBe(true);
    api.saveMetadataEdits.mockResolvedValueOnce(conflict).mockResolvedValueOnce({ batch_id: 'b3', titles: [docFixture('movie')], conflicts: [] });
    await type('?');
    await userEvent.click(saveButton());
    await userEvent.click(await screen.findByRole('button', { name: 'Keep mine and save' }));
    expect(api.saveMetadataEdits.mock.calls[2][0][0].changes.overview.base).toBe('Theirs');
  });
  it('warns before the page unloads while a change is unsaved', async () => {
    await open();
    await type('!');
    const event = new Event('beforeunload', { cancelable: true });
    act(() => { window.dispatchEvent(event); });
    expect(event.defaultPrevented).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: 'Discard' }));
    const after = new Event('beforeunload', { cancelable: true });
    act(() => { window.dispatchEvent(after); });
    expect(after.defaultPrevented).toBe(false);
  });
  it('shows a server field error', async () => {
    await open();
    api.saveMetadataEdits.mockRejectedValue(new ApiRequestError('invalid', 422, null, null, { detail: 'invalid', field: 'year', reason: 'out_of_range' }));
    await type('!');
    await userEvent.click(saveButton());
    expect(await screen.findByText('Check Year: out_of_range')).toBeTruthy();
  });
});
