import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { PersonDoc } from '../../../types';
import { ToastProvider } from '../../../ui';
import { fieldState } from './editorFixtures';

const api = vi.hoisted(() => ({
  getPerson: vi.fn(), renamePerson: vi.fn(), uploadPersonPhoto: vi.fn(), removePersonPhoto: vi.fn(), undoMetadataBatch: vi.fn(),
  revertMetadata: vi.fn(), getMetadataVocabulary: vi.fn(), listEpisodeGroups: vi.fn(),
}));
vi.mock('../../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../../api')>()), ...api }));
const { PersonDialog } = await import('./PersonDialog');
const { FieldRow } = await import('./FieldRow');

afterEach(() => vi.resetAllMocks());
const doc = (patch: Partial<PersonDoc> = {}): PersonDoc => ({
  person_id: 'p1', name: 'Keanu Reevs', source_name: 'Keanu Reevs', name_edited: false, photo_edited: false, image_url: null, title_count: 3, ...patch,
});

describe('PersonDialog', () => {
  it('renames a person everywhere, offers Undo, and resets to the source name', async () => {
    api.getPerson.mockResolvedValue(doc());
    api.renamePerson.mockResolvedValue({ batch_id: 'b1', person: doc({ name: 'Keanu Reeves', name_edited: true }) });
    api.undoMetadataBatch.mockResolvedValue({ batch_id: 'u', restored: 1, skipped: [] });
    const onChanged = vi.fn();
    render(<ToastProvider><PersonDialog onChanged={onChanged} onClose={vi.fn()} personId="p1" /></ToastProvider>);
    expect(await screen.findByText('Changes show on all 3 titles that credit them, here and in connected apps.')).toBeTruthy();
    const name = screen.getByLabelText('Name');
    await userEvent.clear(name);
    await userEvent.type(name, 'Keanu Reeves');
    await userEvent.click(screen.getByRole('button', { name: 'Save name' }));
    expect(api.renamePerson).toHaveBeenCalledWith('p1', 'Keanu Reeves');
    expect(onChanged).toHaveBeenCalled();
    expect(await screen.findByText('Name saved.')).toBeTruthy();
    expect(screen.getByText('Originally Keanu Reevs')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(api.undoMetadataBatch).toHaveBeenCalledWith('b1');
    await vi.waitFor(() => expect(screen.queryByText('Originally Keanu Reevs')).toBeNull()); // the undo reloads the person
  });

  it('goes back to the source name', async () => {
    api.getPerson.mockResolvedValue(doc({ name: 'Keanu Reeves', name_edited: true }));
    api.renamePerson.mockResolvedValue({ batch_id: 'b2', person: doc() });
    render(<ToastProvider><PersonDialog onChanged={vi.fn()} onClose={vi.fn()} personId="p1" /></ToastProvider>);
    await userEvent.click(await screen.findByRole('button', { name: 'Use Keanu Reevs' }));
    expect(api.renamePerson).toHaveBeenCalledWith('p1', null);
  });

  it('uploads a photo and can go back to the original', async () => {
    api.getPerson.mockResolvedValue(doc());
    api.uploadPersonPhoto.mockResolvedValue({ batch_id: 'b1', person: doc({ photo_edited: true, image_url: '/x.jpg' }) });
    api.removePersonPhoto.mockResolvedValue({ batch_id: 'b2', person: doc() });
    const { container } = render(<ToastProvider><PersonDialog onChanged={vi.fn()} onClose={vi.fn()} personId="p1" /></ToastProvider>);
    await screen.findByRole('button', { name: 'Upload photo' });
    const photo = new File(['x'], 'k.png', { type: 'image/png' });
    await userEvent.upload(container.ownerDocument.querySelector('.ed-person input[type="file"]') as HTMLInputElement, photo);
    expect(api.uploadPersonPhoto).toHaveBeenCalledWith('p1', photo);
    await userEvent.click(await screen.findByRole('button', { name: 'Use the original photo' }));
    expect(api.removePersonPhoto).toHaveBeenCalledWith('p1');
  });
});

describe('FieldRow move and order controls', () => {
  const seasons = [{ id: 's0', index_number: 0, name: 'Specials' }, { id: 's1', index_number: 1, name: 'Season 1' }];
  it('moves an episode by picking another season', async () => {
    const setField = vi.fn();
    render(<ToastProvider><FieldRow discardField={vi.fn()} draft={undefined} fieldKey="parent_id" itemLocked={false} onReverted={vi.fn()} seasons={seasons} setField={setField} state={fieldState('s1', null)} titleId="e1" togglePin={vi.fn()} /></ToastProvider>);
    await userEvent.selectOptions(screen.getByLabelText('Season'), 'Specials');
    expect(setField).toHaveBeenCalledWith('e1', 'parent_id', 's0', 's1');
  });
  it('sets a series episode order, aired by default', async () => {
    const setField = vi.fn();
    render(<ToastProvider><FieldRow discardField={vi.fn()} draft={undefined} fieldKey="display_order" itemLocked={false} onReverted={vi.fn()} setField={setField} state={fieldState(null, null)} titleId="t1" togglePin={vi.fn()} /></ToastProvider>);
    expect((screen.getByLabelText('Episode order') as HTMLSelectElement).value).toBe('aired');
    await userEvent.selectOptions(screen.getByLabelText('Episode order'), 'DVD');
    expect(setField).toHaveBeenCalledWith('t1', 'display_order', 'dvd', null);
  });
});
