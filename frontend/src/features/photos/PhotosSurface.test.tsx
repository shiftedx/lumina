import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as rootApi from '../../api';
import type { UserProfile } from '../../types';
import { ToastProvider } from '../../ui';
import * as api from './photoApi';
import { PhotosSurface } from './PhotosSurface';

vi.mock('./photoApi', async (original) => ({
  ...await original<typeof import('./photoApi')>(),
  listPhotoAlbums: vi.fn(), createPhotoAlbum: vi.fn(), updatePhotoAlbum: vi.fn(), deletePhotoAlbum: vi.fn(),
  listPhotoPeople: vi.fn(), getFaceSettings: vi.fn(), listPhotos: vi.fn(), restorePhoto: vi.fn(), updatePhotoPerson: vi.fn(),
}));
vi.mock('../../api', async (original) => ({ ...await original<typeof import('../../api')>(), downloadLocalModel: vi.fn() }));

const user: UserProfile = { id: 'u1', username: 'keeper', display_name: 'Keeper', role: 'admin', is_active: true };
const album: api.PhotoAlbum = { id: 'a1', name: 'Lake weekends', description: null, visibility: 'private', photo_count: 12, cover_url: '/api/photos/p1/image?width=400', created_at: '2026-10-24T00:00:00Z', deleted_at: null, can_edit: true };
const photo = (id: string, deleted: boolean): api.Photo => ({
  id, title: id, filename: `${id}.jpg`, content_type: 'image/jpeg', width: 1600, height: 900, captured_at: '2026-10-24T12:00:00Z', added_at: '2026-10-24T13:00:00Z', camera: null, bytes: 42,
  favorite: false, visibility: 'private', thumbnail_url: `/api/photos/${id}/image?width=400`, preview_url: `/api/photos/${id}/image?width=1200`, original_url: null,
  deleted_at: deleted ? '2026-10-24T14:00:00Z' : null, album_ids: [], person_ids: [], can_edit: true,
});

describe('PhotosSurface collections', () => {
  beforeEach(() => {
    vi.mocked(api.listPhotoAlbums).mockResolvedValue({ items: [album] });
    vi.mocked(api.createPhotoAlbum).mockResolvedValue({ ...album, id: 'a2', name: 'New album' });
    vi.mocked(api.listPhotoPeople).mockResolvedValue({ items: [], next_offset: null });
    vi.mocked(api.getFaceSettings).mockResolvedValue({ enabled: false, auto_group: true, consented_at: null, model: { id: 'face', state: 'ready', reason: null, bytes_done: 1, bytes_total: 1 }, jobs: {}, pipeline_signature: 'test' });
    vi.mocked(api.listPhotos).mockReset();
    vi.mocked(api.restorePhoto).mockReset();
    vi.mocked(api.updatePhotoPerson).mockReset();
    vi.mocked(rootApi.downloadLocalModel).mockReset().mockResolvedValue({ id: 'opencv-sface-2021dec', role: 'face', name: 'OpenCV SFace', description: 'Face grouping', licence: 'Apache-2.0', size_bytes: 10, ram_bytes: 10, default: true, active: true, state: 'downloading', bytes_done: 0, bytes_total: 10, reason: null, running: false, features: [] });
  });

  it('opens an album as a filtered timeline', async () => {
    const onRoute = vi.fn();
    render(<ToastProvider><PhotosSurface onRoute={onRoute} route={{ surface: 'photos', view: 'albums' }} user={user} /></ToastProvider>);
    await userEvent.click(await screen.findByRole('button', { name: /Lake weekends/ }));
    expect(onRoute).toHaveBeenCalledWith(expect.objectContaining({ view: 'timeline', albumId: 'a1' }));
  });

  it('creates a household album through the confirmed API', async () => {
    render(<ToastProvider><PhotosSurface onRoute={vi.fn()} route={{ surface: 'photos', view: 'albums' }} user={user} /></ToastProvider>);
    await userEvent.click(await screen.findByRole('button', { name: 'New album' }));
    await userEvent.type(screen.getByLabelText('Album name'), 'New album');
    await userEvent.click(screen.getByRole('combobox', { name: 'Album visibility' }));
    await userEvent.click(screen.getByRole('option', { name: 'Household' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(api.createPhotoAlbum).toHaveBeenCalledWith({ name: 'New album', visibility: 'shared' });
  });

  it('accepts a TIFF with no browser MIME and reports an unsupported GIF', async () => {
    const view = render(<ToastProvider><PhotosSurface onRoute={vi.fn()} route={{ surface: 'photos', view: 'albums' }} user={user} /></ToastProvider>);
    await userEvent.click(await screen.findByRole('button', { name: 'Import' }));
    const input = view.container.querySelector<HTMLInputElement>('#ph-files')!;
    fireEvent.change(input, { target: { files: [new File(['tiff'], 'archive.tiff', { type: '' }), new File(['gif'], 'loop.gif', { type: 'image/gif' })] } });
    expect(screen.getByText('archive.tiff')).toBeTruthy();
    expect(screen.queryByText('loop.gif')).toBeNull();
    expect(screen.getByRole('status').textContent).toContain('1 file not added');
  });

  it('does not fetch the photo feed behind collection-only views', async () => {
    render(<ToastProvider><PhotosSurface onRoute={vi.fn()} route={{ surface: 'photos', view: 'albums' }} user={user} /></ToastProvider>);
    await screen.findByText('Lake weekends');
    expect(api.listPhotos).not.toHaveBeenCalled();
  });

  it('does not let a deferred Trash restore reload abort the active Timeline query', async () => {
    const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1000);
    let finishRestore!: (value: api.Photo) => void;
    const deleted = photo('Deleted photo', true);
    const live = photo('Timeline photo', false);
    const timelineSignals: AbortSignal[] = [];
    vi.mocked(api.listPhotos).mockImplementation(async (query, signal) => {
      if (!query.deleted && signal) timelineSignals.push(signal);
      return { items: [query.deleted ? deleted : live], total: 1, next_cursor: null };
    });
    vi.mocked(api.restorePhoto).mockReturnValue(new Promise((resolve) => { finishRestore = resolve; }));
    function Harness() {
      const [route, setRoute] = useState({ surface: 'photos' as const, view: 'trash' as const });
      return <PhotosSurface onRoute={(next) => setRoute(next as typeof route)} route={route} user={user} />;
    }
    render(<ToastProvider><Harness /></ToastProvider>);
    await userEvent.click(await screen.findByRole('button', { name: 'Restore Deleted photo' }));
    await userEvent.click(screen.getByRole('button', { name: 'Timeline' }));
    await screen.findByRole('button', { name: 'View Timeline photo' });
    await act(async () => finishRestore({ ...deleted, deleted_at: null }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'View Timeline photo' })).toBeTruthy());
    expect(vi.mocked(api.listPhotos).mock.calls.map(([query]) => query.deleted)).toEqual([true, false]);
    expect(timelineSignals).toHaveLength(1);
    expect(timelineSignals[0].aborted).toBe(false);
    width.mockRestore();
  });

  it('lets an owner download the missing face model from People', async () => {
    vi.mocked(api.getFaceSettings).mockResolvedValue({ enabled: true, auto_group: true, consented_at: '2026-10-24T00:00:00Z', model: { id: 'opencv-sface-2021dec', state: 'absent', reason: null, bytes_done: 0, bytes_total: 10 }, jobs: {}, pipeline_signature: 'test' });
    render(<ToastProvider><PhotosSurface onRoute={vi.fn()} route={{ surface: 'photos', view: 'people' }} user={user} /></ToastProvider>);
    await userEvent.click(await screen.findByRole('button', { name: 'Download face model' }));
    expect(rootApi.downloadLocalModel).toHaveBeenCalledWith('opencv-sface-2021dec');
  });

  it('can reveal and unhide a hidden people group', async () => {
    const hidden: api.PhotoPerson = { id: 'person-hidden', name: 'Alex', photo_count: 3, face_count: 4, thumbnail_url: null, hidden: true, revision: 1 };
    vi.mocked(api.getFaceSettings).mockResolvedValue({ enabled: true, auto_group: true, consented_at: '2026-10-24T00:00:00Z', model: { id: 'opencv-sface-2021dec', state: 'ready', reason: null, bytes_done: 10, bytes_total: 10 }, jobs: {}, pipeline_signature: 'test' });
    vi.mocked(api.listPhotoPeople).mockImplementation(async (includeHidden) => ({ items: includeHidden ? [hidden] : [], next_offset: null }));
    vi.mocked(api.updatePhotoPerson).mockResolvedValue({ ...hidden, hidden: false });
    render(<ToastProvider><PhotosSurface onRoute={vi.fn()} route={{ surface: 'photos', view: 'people' }} user={user} /></ToastProvider>);
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Show hidden groups' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Unhide' }));
    expect(api.updatePhotoPerson).toHaveBeenCalledWith(hidden.id, { hidden: false });
  });

  it('refreshes people in place while face analysis is pending', async () => {
    const discovered: api.PhotoPerson = { id: 'person-new', name: null, photo_count: 2, face_count: 2, thumbnail_url: null, hidden: false, revision: 1 };
    vi.mocked(api.getFaceSettings)
      .mockResolvedValueOnce({ enabled: true, auto_group: true, consented_at: '2026-10-24T00:00:00Z', model: { id: 'opencv-sface-2021dec', state: 'ready', reason: null, bytes_done: 10, bytes_total: 10 }, jobs: { queued: 2 }, pipeline_signature: 'test' })
      .mockResolvedValue({ enabled: true, auto_group: true, consented_at: '2026-10-24T00:00:00Z', model: { id: 'opencv-sface-2021dec', state: 'ready', reason: null, bytes_done: 10, bytes_total: 10 }, jobs: { succeeded: 2 }, pipeline_signature: 'test' });
    vi.mocked(api.listPhotoPeople).mockResolvedValueOnce({ items: [], next_offset: null }).mockResolvedValue({ items: [discovered], next_offset: null });
    let poll!: () => void;
    const interval = vi.spyOn(window, 'setInterval').mockImplementation(((callback: TimerHandler) => { poll = callback as () => void; return 1; }) as typeof window.setInterval);
    render(<ToastProvider><PhotosSurface onRoute={vi.fn()} route={{ surface: 'photos', view: 'people' }} user={user} /></ToastProvider>);
    await screen.findByText('2 photos waiting or being analyzed');
    act(() => poll());
    expect(await screen.findByText('Unnamed person')).toBeTruthy();
    expect(screen.getByText('Analysis is up to date')).toBeTruthy();
    interval.mockRestore();
  });

  it('loads people beyond the first paginated response', async () => {
    const firstPage: api.PhotoPerson[] = Array.from({ length: 100 }, (_, index) => ({ id: `person-${index + 1}`, name: index ? `Person ${index + 1}` : 'Alex', photo_count: 3, face_count: 4, thumbnail_url: null, hidden: false, revision: 1 }));
    const personLater: api.PhotoPerson = { ...firstPage[0], id: 'person-101', name: 'Morgan' };
    vi.mocked(api.getFaceSettings).mockResolvedValue({ enabled: true, auto_group: true, consented_at: '2026-10-24T00:00:00Z', model: { id: 'opencv-sface-2021dec', state: 'ready', reason: null, bytes_done: 10, bytes_total: 10 }, jobs: {}, pipeline_signature: 'test' });
    vi.mocked(api.listPhotoPeople).mockImplementation(async (_includeHidden, _signal, offset) => offset === 100
      ? { items: [personLater], next_offset: null }
      : { items: firstPage, next_offset: 100 });
    render(<ToastProvider><PhotosSurface onRoute={vi.fn()} route={{ surface: 'photos', view: 'people' }} user={user} /></ToastProvider>);
    await screen.findByText('Alex');
    await userEvent.click(screen.getByRole('button', { name: 'Load more people' }));
    expect(await screen.findByText('Morgan')).toBeTruthy();
    expect(api.listPhotoPeople).toHaveBeenCalledWith(false, undefined, 100, 100);
  });
});
