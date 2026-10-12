import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { FieldState } from '../../../types';
import { ToastProvider } from '../../../ui';
import { fieldState } from './editorFixtures';
import type { TitleDraft } from './editorModel';

const api = vi.hoisted(() => ({ revertMetadata: vi.fn(), getMetadataVocabulary: vi.fn() }));
vi.mock('../../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../../api')>()), ...api }));
const { FieldRow } = await import('./FieldRow');

afterEach(() => vi.resetAllMocks());
const fns = () => ({ setField: vi.fn(), togglePin: vi.fn(), discardField: vi.fn(), onReverted: vi.fn() });
function row(fieldKey: string, state: FieldState, extra: { itemLocked?: boolean; draft?: TitleDraft } = {}) {
  const f = fns();
  api.revertMetadata.mockResolvedValue({ batch_id: 'b', title: {} });
  api.getMetadataVocabulary.mockResolvedValue([]);
  render(<ToastProvider><FieldRow draft={extra.draft} fieldKey={fieldKey} itemLocked={extra.itemLocked ?? false} state={state} titleId="t1" {...f} /></ToastProvider>);
  return f;
}

describe('FieldRow', () => {
  it('shows the source chip and offers Lock on a field that comes from TMDB', () => {
    row('overview', fieldState('Old', 'tmdb'));
    expect(screen.getByText('From TMDB')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Lock Overview' })).toBeTruthy();
  });
  it('an edited field reads "Your edit" and Unlock asks before going back', async () => {
    const f = row('name', fieldState('New', 'user', { kept: { source: 'nfo', value: 'Dune (2021)' } }));
    expect(screen.getByText('Your edit')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Unlock Name' }));
    expect(screen.getByText('Go back to the NFO file value?')).toBeTruthy();
    expect(screen.getByText('Dune (2021)')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Revert' }));
    expect(api.revertMetadata).toHaveBeenCalledWith('t1', ['name']);
    await vi.waitFor(() => expect(f.onReverted).toHaveBeenCalled());
    expect(await screen.findByText('Name reverted.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeTruthy();
  });
  it('"Keep my edit" closes the popover and calls nothing', async () => {
    row('name', fieldState('New', 'user', { kept: { source: 'nfo', value: 'Old' } }));
    await userEvent.click(screen.getByRole('button', { name: 'Unlock Name' }));
    await userEvent.click(screen.getByRole('button', { name: 'Keep my edit' }));
    expect(screen.queryByText('Go back to the NFO file value?')).toBeNull();
    expect(api.revertMetadata).not.toHaveBeenCalled();
  });
  it('says there is nothing to go back to when no value is kept', async () => {
    row('name', fieldState('New', 'user'));
    await userEvent.click(screen.getByRole('button', { name: 'Unlock Name' }));
    expect(screen.getByText('Nothing to go back to. The field will be empty until the next scan or refresh.')).toBeTruthy();
  });
  it('disables the lock button with an explanation when the item is locked', () => {
    row('overview', fieldState('Old', 'tmdb'), { itemLocked: true });
    expect((screen.getByRole('button', { name: 'Locked by the item lock' }) as HTMLButtonElement).disabled).toBe(true);
  });
  it('pins and unpins through the draft', async () => {
    const f = row('overview', fieldState('Old', 'tmdb'), { draft: { changes: {}, pin: ['overview'] } });
    await userEvent.click(screen.getByRole('button', { name: 'Unlock Overview' }));
    expect(f.togglePin).toHaveBeenCalledWith('t1', 'overview');
  });
  it('an unsaved edit on a non-user field unlocks by discarding', async () => {
    const f = row('overview', fieldState('Old', 'tmdb'), { draft: { changes: { overview: { value: 'New', base: 'Old' } }, pin: [] } });
    expect(screen.getByText('Your edit')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Unlock Overview' }));
    expect(f.discardField).toHaveBeenCalledWith('t1', 'overview');
  });
  it('typing an overview records a change whose base is the loaded value', async () => {
    const f = row('overview', fieldState('Old', 'tmdb'));
    await userEvent.type(screen.getByRole('textbox', { name: 'Overview' }), '!');
    expect(f.setField).toHaveBeenCalledWith('t1', 'overview', 'Old!', 'Old');
  });
  it('a blank name is refused with a message and the draft goes back to the loaded name', async () => {
    const f = row('name', fieldState('Dune', 'tmdb'));
    await userEvent.clear(screen.getByRole('textbox', { name: 'Name' }));
    expect(screen.getByRole('alert').textContent).toContain("A name can't be empty.");
    expect(f.setField).toHaveBeenLastCalledWith('t1', 'name', 'Dune', 'Dune');
  });
});
