import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { UserProfile } from '../../../types';
import { ToastProvider } from '../../../ui';
import { docFixture } from './editorFixtures';
import { saveLabel } from './EditorFooter';

const api = vi.hoisted(() => ({ getTitleMetadata: vi.fn(), saveMetadataEdits: vi.fn(), undoMetadataBatch: vi.fn() }));
vi.mock('../../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../../api')>()), ...api }));
const { ApiRequestError } = await import('../../../api');
const { EditorPage } = await import('./EditorPage');

const user: UserProfile = { id: 'u1', username: 'o', display_name: 'Owner', role: 'admin', is_active: true, can_edit_details: true };
afterEach(() => vi.resetAllMocks());
function show(tab: 'details' | null = null, handlers: { onTab?: (tab: string) => void } = {}) {
  return render(<ToastProvider><EditorPage id="t1" onBack={() => undefined} onTab={(handlers.onTab ?? (() => undefined)) as never} tab={tab} user={user} /></ToastProvider>);
}

describe('EditorPage', () => {
  it('shows skeleton rows while the document loads', () => {
    api.getTitleMetadata.mockReturnValue(new Promise(() => undefined));
    show();
    expect(screen.getByRole('status').textContent).toBe('Loading details');
  });

  it('reads a 404 and a 403 in the spec copy, and retries any other failure', async () => {
    api.getTitleMetadata.mockRejectedValueOnce(new ApiRequestError('not_found', 404));
    const first = show();
    expect((await screen.findByRole('heading', { name: "This title isn't in your library." })).textContent).toBeTruthy();
    first.unmount();
    api.getTitleMetadata.mockRejectedValueOnce(new ApiRequestError('forbidden', 403));
    const second = show();
    expect(await screen.findByText('Ask a vault owner to let household members edit details.')).toBeTruthy();
    second.unmount();
    api.getTitleMetadata.mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce(docFixture('series'));
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    expect((await screen.findByRole('heading', { name: 'Severance' })).textContent).toBe('Severance');
  });

  it('renders the name, kind and year, the tabs for the type, and reports a tab change', async () => {
    api.getTitleMetadata.mockResolvedValue(docFixture('series'));
    const onTab = vi.fn();
    show(null, { onTab });
    expect((await screen.findByRole('heading', { name: 'Severance' })).textContent).toBe('Severance');
    expect(screen.getByText('Series · 2022')).toBeTruthy();
    const group = screen.getByRole('group', { name: 'Edit sections' });
    expect(group.querySelectorAll('input[type=radio]').length).toBe(6);
    expect((screen.getByRole('radio', { name: 'Details' }) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole('radio', { name: 'People' }));
    expect(onTab).toHaveBeenCalledWith('people');
  });

  it('keeps Save and Discard disabled with an empty draft and does not block unload', async () => {
    api.getTitleMetadata.mockResolvedValue(docFixture('movie'));
    show();
    await screen.findByRole('heading', { name: 'Severance' });
    expect((screen.getByRole('button', { name: 'Save changes' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Discard' }) as HTMLButtonElement).disabled).toBe(true);
    const event = new Event('beforeunload', { cancelable: true });
    act(() => { window.dispatchEvent(event); });
    expect(event.defaultPrevented).toBe(false);
  });

  it.each([['people', 'Add person', 'Name 1'], ['ids', null, 'Other ID name']] as const)('Discard resets the %s tab form with the draft', async (tab, add, _field) => {
    api.getTitleMetadata.mockResolvedValue(docFixture('movie'));
    show(tab as never);
    await screen.findByRole('heading', { name: 'Severance' });
    let imdbBefore = '';
    if (add) {
      fireEvent.click(screen.getByRole('button', { name: add }));
      fireEvent.change(screen.getByLabelText('Name 1'), { target: { value: 'Ghost' } });
      expect(screen.getByLabelText('Name 1')).toBeTruthy();
    } else {
      imdbBefore = (screen.getByLabelText('IMDb') as HTMLInputElement).value;
      fireEvent.change(screen.getByLabelText('IMDb'), { target: { value: 'tt1234567' } });
    }
    expect(screen.getByRole('button', { name: 'Save 1 change' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    if (add) expect(screen.queryByLabelText('Name 1')).toBeNull();
    else expect((screen.getByLabelText('IMDb') as HTMLInputElement).value).toBe(imdbBefore);
  });

  it('labels the save button by the number of changes', () => {
    expect(saveLabel(0)).toBe('Save changes');
    expect(saveLabel(1)).toBe('Save 1 change');
    expect(saveLabel(3)).toBe('Save 3 changes');
  });
});
