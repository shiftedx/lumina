import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ undoMetadataBatch: vi.fn() }));
vi.mock('../../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../../api')>()), ...api }));
const { ApiRequestError } = await import('../../../api');
const { undoBatch } = await import('./editorActions');

const skip = (reason: 'changed_since' | 'not_visible' | 'owner_only', field = 'overview', title_id = 't') => ({ title_id, field, reason });
beforeEach(() => api.undoMetadataBatch.mockReset());

describe('undoBatch', () => {
  it('says what an undo did and what it left alone', async () => {
    const toast = vi.fn();
    api.undoMetadataBatch.mockResolvedValue({ batch_id: 'u1', restored: 3, skipped: [skip('changed_since'), skip('changed_since', 'name')] });
    await undoBatch('b1', toast, () => undefined);
    expect(toast).toHaveBeenCalledWith({ tone: 'info', message: '2 fields changed since, so they were left as they are.' });
  });
  it('uses the singular, and a plain "Undone." when nothing was skipped', async () => {
    const toast = vi.fn();
    const reload = vi.fn();
    api.undoMetadataBatch.mockResolvedValueOnce({ batch_id: 'u1', restored: 1, skipped: [skip('changed_since')] });
    await undoBatch('b1', toast, reload);
    expect(toast).toHaveBeenLastCalledWith({ tone: 'info', message: '1 field changed since, so it was left as it is.' });
    api.undoMetadataBatch.mockResolvedValueOnce({ batch_id: 'u2', restored: 2, skipped: [] });
    await undoBatch('b2', toast, reload);
    expect(toast).toHaveBeenLastCalledWith({ tone: 'success', message: 'Undone.' });
    expect(reload).toHaveBeenCalledTimes(2);
  });
  it('adds the titles that left the library as a second sentence', async () => {
    const toast = vi.fn();
    api.undoMetadataBatch.mockResolvedValue({ batch_id: 'u1', restored: 0, skipped: [skip('changed_since'), skip('not_visible', 'name', 'x'), skip('not_visible', 'tags', 'x')] });
    await undoBatch('b1', toast, () => undefined);
    expect(toast).toHaveBeenCalledWith({ tone: 'info', message: "1 field changed since, so it was left as it is. 1 title isn't in your library, so it was left as it is." });
  });
  it('says only a vault owner can undo an owner-only skip', async () => {
    const toast = vi.fn();
    api.undoMetadataBatch.mockResolvedValue({ batch_id: 'u1', restored: 0, skipped: [skip('owner_only', 'provider_ids')] });
    await undoBatch('b1', toast, () => undefined);
    expect(toast).toHaveBeenCalledWith({ tone: 'info', message: 'Only a vault owner can undo this.' });
  });
  it('reports a failed undo and a batch already undone', async () => {
    const toast = vi.fn();
    const reload = vi.fn();
    api.undoMetadataBatch.mockRejectedValueOnce(new ApiRequestError('already_undone', 409));
    await undoBatch('b1', toast, reload);
    expect(toast).toHaveBeenLastCalledWith({ tone: 'error', message: 'That change was already undone.' });
    api.undoMetadataBatch.mockRejectedValueOnce(new Error('boom'));
    await undoBatch('b1', toast, reload);
    expect(toast).toHaveBeenLastCalledWith({ tone: 'error', message: 'Lumina could not undo that. Try again.' });
    expect(reload).not.toHaveBeenCalled();
  });
});
