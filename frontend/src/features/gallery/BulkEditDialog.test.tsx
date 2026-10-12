import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import * as api from '../../api';
import { BulkEditDialog } from './BulkEditDialog';

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) { this.removeAttribute('open'); this.dispatchEvent(new Event('close')); };
});
afterEach(() => vi.restoreAllMocks());

const open = (onDone = vi.fn()) => {
  vi.spyOn(api, 'getMetadataVocabulary').mockResolvedValue([]);
  render(<BulkEditDialog onClose={vi.fn()} onDone={onDone} titleIds={['a', 'b', 'c']} />);
  return onDone;
};

describe('BulkEditDialog', () => {
  it('disables Apply until something is chosen', async () => {
    open();
    expect(screen.getByRole('heading', { name: 'Edit details' })).toBeTruthy();
    const apply = screen.getByRole('button', { name: 'Apply to 3 titles' }) as HTMLButtonElement;
    expect(apply.disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeTruthy();
    await userEvent.type(screen.getByRole('combobox', { name: 'Add genres' }), 'Noir{Enter}');
    expect(apply.disabled).toBe(false);
  });

  it('applies the ops and reports the result with the batch id', async () => {
    const bulk = vi.spyOn(api, 'bulkEditMetadata').mockResolvedValue({ batch_id: 'batch-1', applied: 3, skipped: [] });
    const onDone = open();
    await userEvent.type(screen.getByRole('combobox', { name: 'Add genres' }), 'Noir{Enter}');
    await userEvent.click(screen.getByRole('button', { name: 'Apply to 3 titles' }));
    expect(bulk).toHaveBeenCalledWith({ title_ids: ['a', 'b', 'c'], ops: [{ op: 'add', field: 'genres', values: ['Noir'] }] });
    await waitFor(() => expect(onDone).toHaveBeenCalledWith('Updated 3 titles.', 'batch-1'));
  });

  it('keeps the draft and says so when the save fails', async () => {
    vi.spyOn(api, 'bulkEditMetadata').mockRejectedValue(new Error('boom'));
    const onDone = open();
    await userEvent.type(screen.getByRole('combobox', { name: 'Add genres' }), 'Noir{Enter}');
    await userEvent.click(screen.getByRole('button', { name: 'Apply to 3 titles' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Lumina could not save these changes. Try again.');
    expect(screen.getByText('Noir')).toBeTruthy();
    expect(onDone).not.toHaveBeenCalled();
  });

  it('offers Leave, Lock and Unlock for each lock, Leave by default', () => {
    open();
    for (const name of ['genres', 'tags', 'parental rating', 'items']) {
      const group = screen.getByRole('group', { name: `Lock ${name}` });
      expect(group.querySelectorAll('input[type="radio"]').length).toBe(3);
      expect((group.querySelector('input[type="radio"]') as HTMLInputElement).checked).toBe(true);
    }
  });
});
