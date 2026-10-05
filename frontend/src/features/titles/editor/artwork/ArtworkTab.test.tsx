import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../../../ui';
import { ApiRequestError } from '../../../../api';
import { artworkDoc, image } from './artworkFixtures';
import ArtworkTab from './ArtworkTab';

const api = vi.hoisted(() => ({
  getTitleMetadata: vi.fn(), saveMetadataEdits: vi.fn(), revertMetadata: vi.fn(), removeTitleImage: vi.fn(), reorderBackdrops: vi.fn(),
  uploadTitleImage: vi.fn(), listImageCandidates: vi.fn(), chooseTitleImage: vi.fn(),
}));
vi.mock('../../../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../../../api')>()), ...api }));

const three = [image('Primary', 0), image('Backdrop', 0), image('Backdrop', 1), image('Backdrop', 2), image('Logo', 0)];
const setup = (doc = artworkDoc('movie', three)) => {
  const onImages = vi.fn(); const onChanged = vi.fn();
  render(<ToastProvider><ArtworkTab doc={doc} onChanged={onChanged} onImages={onImages} /></ToastProvider>);
  return { onImages, onChanged };
};
const openMenu = async (name: string) => { await userEvent.click(screen.getByRole('button', { name: `${name} options` })); };
const item = (name: string) => screen.getByRole('menuitem', { name });

beforeEach(() => { Object.values(api).forEach((fn) => fn.mockReset()); });

describe('ArtworkTab', () => {
  it('says artwork saves as you go, with one card per slot and its origin chip', () => {
    setup();
    expect(document.body.textContent).toContain('Artwork changes save as you make them. Each one can be undone from History.');
    for (const label of ['Poster', 'Backdrop 1', 'Backdrop 2', 'Backdrop 3', 'Logo']) expect(screen.getByRole('button', { name: `${label} options` })).toBeTruthy();
    expect(screen.getAllByText('From TMDB')).toHaveLength(5);
  });

  it('an empty slot offers a way in, not Remove', async () => {
    setup(artworkDoc('movie', [image('Primary', 0)]));
    expect(document.body.textContent).toContain('No logo');
    await openMenu('Logo');
    expect(item('Choose from TMDB…')).toBeTruthy();
    expect(item('Upload…')).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Remove' })).toBeNull();
  });

  it('backdrops add Move left and Move right, disabled at the ends', async () => {
    setup();
    await openMenu('Poster');
    expect(screen.queryByRole('menuitem', { name: 'Move left' })).toBeNull();
    expect(item('Remove')).toBeTruthy();
    await userEvent.keyboard('{Escape}');
    await openMenu('Backdrop 1');
    expect(item('Move left').getAttribute('aria-disabled')).toBe('true');
    await userEvent.keyboard('{Escape}');
    await openMenu('Backdrop 3');
    expect(item('Move right').getAttribute('aria-disabled')).toBe('true');
  });

  it('Move left sends the tags in their new order and announces it', async () => {
    const fresh = [image('Backdrop', 0)];
    api.reorderBackdrops.mockResolvedValue(fresh);
    const { onImages, onChanged } = setup();
    await openMenu('Backdrop 3');
    await userEvent.click(item('Move left'));
    await waitFor(() => expect(onImages).toHaveBeenCalledWith(fresh));
    expect(api.reorderBackdrops).toHaveBeenCalledWith('t1', ['tag-Backdrop-0', 'tag-Backdrop-2', 'tag-Backdrop-1']);
    expect(onChanged).toHaveBeenCalled();
    expect(screen.getByRole('status').textContent).toBe('Backdrop 3 moved to position 2 of 3.');
  });

  it('Remove asks first, then sends the slot and its base tag; cancel sends nothing', async () => {
    api.removeTitleImage.mockResolvedValue([]);
    const { onImages } = setup();
    await openMenu('Poster');
    await userEvent.click(item('Remove'));
    expect(document.body.textContent).toContain('Remove this poster?');
    expect(document.body.textContent).toContain('History');
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(api.removeTitleImage).not.toHaveBeenCalled();
    await openMenu('Backdrop 2');
    await userEvent.click(item('Remove'));
    await userEvent.click(screen.getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(onImages).toHaveBeenCalledWith([]));
    expect(api.removeTitleImage).toHaveBeenCalledWith('t1', 'Backdrop', 1, 'tag-Backdrop-1');
  });

  it('the lock pins the current image through the edits route', async () => {
    const pinned = [image('Primary', 0, { source: 'user' })];
    api.saveMetadataEdits.mockResolvedValue({ batch_id: 'b', conflicts: [], titles: [{ images: pinned }] });
    const { onImages } = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Lock Poster' }));
    await waitFor(() => expect(onImages).toHaveBeenCalledWith(pinned));
    expect(api.saveMetadataEdits).toHaveBeenCalledWith([{ title_id: 't1', changes: {}, pin: ['images.Primary'] }]);
  });

  it('Unlock on a user-chosen image asks, then reverts to the source image', async () => {
    const back = [image('Primary', 0)];
    api.revertMetadata.mockResolvedValue({ batch_id: 'b', title: { images: back } });
    const { onImages } = setup(artworkDoc('movie', [image('Primary', 0, { source: 'user', origin: 'upload' })]));
    await userEvent.click(screen.getByRole('button', { name: 'Unlock Poster' }));
    expect(document.body.textContent).toContain('Go back to the source image?');
    await userEvent.click(screen.getByRole('button', { name: 'Revert' }));
    await waitFor(() => expect(onImages).toHaveBeenCalledWith(back));
    expect(api.revertMetadata).toHaveBeenCalledWith('t1', ['images.Primary']);
  });

  it('an item-locked title disables the lock buttons with the reason', () => {
    setup(artworkDoc('movie', three, { locked: true }));
    const lock = screen.getAllByRole('button', { name: 'Locked by the item lock' });
    expect(lock.length).toBeGreaterThan(0);
    expect((lock[0] as HTMLButtonElement).disabled).toBe(true);
  });

  it('a stale tag (409) reloads the images and says so', async () => {
    const fresh = [image('Primary', 0, { tag: 'newer' })];
    api.removeTitleImage.mockRejectedValue(new ApiRequestError('conflict', 409));
    api.getTitleMetadata.mockResolvedValue({ images: fresh });
    const { onImages } = setup();
    await openMenu('Poster');
    await userEvent.click(item('Remove'));
    await userEvent.click(screen.getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(onImages).toHaveBeenCalledWith(fresh));
    expect(document.body.textContent).toContain('Someone changed this artwork while you were looking. The latest is shown.');
  });

  it('any other failure is a plain error toast and leaves the images alone', async () => {
    api.removeTitleImage.mockRejectedValue(new Error('boom'));
    const { onImages } = setup();
    await openMenu('Poster');
    await userEvent.click(item('Remove'));
    await userEvent.click(screen.getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(document.body.textContent).toContain('Lumina could not change the artwork. Try again.'));
    expect(onImages).not.toHaveBeenCalled();
  });

  it('without a TMDB key it says how to fix it and disables Choose from TMDB, not Upload', async () => {
    setup(artworkDoc('movie', three, { tmdb_configured: false }));
    expect(document.body.textContent).toContain('Add a TMDB key in Settings → Server → Metadata to choose artwork from TMDB.');
    await openMenu('Poster');
    expect(item('Choose from TMDB…').getAttribute('aria-disabled')).toBe('true');
    expect(item('Upload…').getAttribute('aria-disabled')).toBeNull();
  });

  it('Choose from TMDB… opens the dialog for that slot', async () => {
    api.listImageCandidates.mockResolvedValue([]);
    setup();
    await openMenu('Backdrop 2');
    await userEvent.click(item('Choose from TMDB…'));
    await waitFor(() => expect(api.listImageCandidates).toHaveBeenCalledWith('t1', 'Backdrop', 1));
    expect(document.body.textContent).toContain('Choose a backdrop 2');
  });

  const fileInput = (name: string) => screen.getByRole('button', { name: `${name} options` }).closest('.ed-art-card')!.querySelector('input[type="file"]') as HTMLInputElement;

  it('has a hidden file input accepting JPEG, PNG and WebP', () => {
    setup();
    expect(fileInput('Poster').getAttribute('accept')).toBe('image/jpeg,image/png,image/webp');
  });

  it('uploading sends the file with slot and base tag, shows progress, then the new images', async () => {
    let finish: (v: unknown) => void = () => {};
    api.uploadTitleImage.mockReturnValue(new Promise((r) => { finish = r; }));
    const { onImages, onChanged } = setup();
    const file = new File(['x'], 'p.jpg', { type: 'image/jpeg' });
    await userEvent.upload(fileInput('Poster'), file);
    expect(api.uploadTitleImage).toHaveBeenCalledWith('t1', 'Primary', 0, file, 'tag-Primary-0');
    expect(screen.getByRole('progressbar', { name: 'Uploading Poster' })).toBeTruthy();
    const fresh = [image('Primary', 0, { origin: 'upload' })];
    finish(fresh);
    await waitFor(() => expect(onImages).toHaveBeenCalledWith(fresh));
    expect(onChanged).toHaveBeenCalled();
    expect(screen.getByRole('status').textContent).toBe('Poster replaced.');
    expect(screen.queryByRole('progressbar')).toBeNull();
  });

  it('refuses a file over 15 MB in the browser and sends nothing', async () => {
    setup();
    const file = new File(['x'], 'big.jpg', { type: 'image/jpeg' });
    Object.defineProperty(file, 'size', { value: 15 * 1024 * 1024 + 1 });
    await userEvent.upload(fileInput('Poster'), file);
    expect(api.uploadTitleImage).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain('That image is larger than 15 MB.');
  });

  it.each([[415, 'Lumina accepts JPEG, PNG or WebP images.'], [422, 'That image is larger than 8,000 pixels on a side.'], [413, 'That image is larger than 15 MB.'], [503, 'Lumina could not upload that image. Try again.']])('shows the server copy for %i and keeps the old image', async (status, copy) => {
    api.uploadTitleImage.mockRejectedValue(new ApiRequestError('x', status));
    const { onImages } = setup();
    await userEvent.upload(fileInput('Poster'), new File(['x'], 'p.jpg', { type: 'image/jpeg' }));
    await waitFor(() => expect(document.body.textContent).toContain(copy));
    expect(onImages).not.toHaveBeenCalled();
  });

  it.each([[409, 'duplicate', "That image is already one of this title's backdrops."], [408, 'body_timeout', 'The upload stalled. Try again on a steadier connection.'], [507, 'storage_full', "Lumina's space for uploaded artwork is full. Remove some uploaded images, then try again."]])('explains upload %i %s in words without reloading', async (status, code, copy) => {
    api.uploadTitleImage.mockRejectedValue(new ApiRequestError(code, status));
    const { onImages } = setup();
    await userEvent.upload(fileInput('Poster'), new File(['x'], 'p.jpg', { type: 'image/jpeg' }));
    await waitFor(() => expect(document.body.textContent).toContain(copy));
    expect(onImages).not.toHaveBeenCalled();
  });

  it('uploading to the first empty backdrop slot uses the next index and a null base tag', async () => {
    api.uploadTitleImage.mockResolvedValue([]);
    setup();
    const file = new File(['x'], 'p.jpg', { type: 'image/jpeg' });
    await userEvent.upload(fileInput('Backdrop 4'), file);
    expect(api.uploadTitleImage).toHaveBeenCalledWith('t1', 'Backdrop', 3, file, null);
  });
});
