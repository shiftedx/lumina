import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from './photoApi';
import PhotoViewer from './PhotoViewer';

vi.mock('./photoApi', async (original) => ({
  ...await original<typeof import('./photoApi')>(),
  listPhotoAlbums: vi.fn(), listPhotoFaces: vi.fn(), listPhotoPeople: vi.fn(), assignPhotoFace: vi.fn(), splitPhotoPerson: vi.fn(), createPhotoPerson: vi.fn(),
}));

const photo: api.Photo = {
  id: 'photo-1', title: null, filename: 'Lake.jpg', content_type: 'image/jpeg', width: 1600, height: 900,
  captured_at: '2026-10-24T12:00:00Z', added_at: '2026-10-24T13:00:00Z', camera: 'Lumina Camera', bytes: 42,
  favorite: false, visibility: 'private', thumbnail_url: '/api/photos/photo-1/image?width=400', preview_url: '/api/photos/photo-1/image?width=1200',
  original_url: '/api/photos/photo-1/original', deleted_at: null, album_ids: [], person_ids: ['person-1'], can_edit: true,
};
const person = (id: string, name: string | null): api.PhotoPerson => ({ id, name, photo_count: 4, face_count: 5, thumbnail_url: null, hidden: false, revision: 1 });

describe('PhotoViewer face corrections', () => {
  beforeEach(() => {
    vi.mocked(api.listPhotoAlbums).mockResolvedValue({ items: [] });
    vi.mocked(api.listPhotoFaces).mockResolvedValue({ items: [{ id: 'face-1', person_id: 'person-1', box: { x: 0.1, y: 0.1, width: 0.2, height: 0.2 }, confidence: 0.95, thumbnail_url: '/api/photo-faces/face-1/image' }] });
    vi.mocked(api.listPhotoPeople).mockResolvedValue({ items: [person('person-1', 'Alex'), person('person-2', 'Sam')], next_offset: null });
    vi.mocked(api.assignPhotoFace).mockResolvedValue({ id: 'face-1', person_id: 'person-2', assignment_source: 'manual' });
    vi.mocked(api.splitPhotoPerson).mockResolvedValue({ person: person('person-3', null) });
    vi.mocked(api.createPhotoPerson).mockResolvedValue({ person: person('person-4', 'Jamie') });
  });

  it('reassigns a mistaken face and can separate it into a new group', async () => {
    render(<PhotoViewer onClose={vi.fn()} onNearEnd={vi.fn()} onPatch={vi.fn()} photoId={photo.id} photos={[photo]} />);
    await userEvent.click(screen.getByRole('button', { name: 'Show photo details' }));
    const assignment = await screen.findByRole('combobox', { name: 'Person for face 1' });
    await userEvent.click(assignment);
    await userEvent.click(screen.getByRole('option', { name: 'Sam' }));
    expect(api.assignPhotoFace).toHaveBeenCalledWith('face-1', 'person-2');
    await userEvent.click(screen.getByRole('button', { name: 'Separate into new group' }));
    expect(api.splitPhotoPerson).toHaveBeenCalledWith('person-2', ['face-1']);
  });

  it('loads later people pages for the correction picker', async () => {
    vi.mocked(api.listPhotoPeople).mockImplementation(async (_includeHidden, _signal, offset) => offset === 100
      ? { items: [person('person-101', 'Morgan')], next_offset: null }
      : { items: [person('person-1', 'Alex')], next_offset: 100 });
    render(<PhotoViewer onClose={vi.fn()} onNearEnd={vi.fn()} onPatch={vi.fn()} photoId={photo.id} photos={[photo]} />);
    await userEvent.click(screen.getByRole('button', { name: 'Show photo details' }));
    const assignment = await screen.findByRole('combobox', { name: 'Person for face 1' });
    await userEvent.click(assignment);
    await waitFor(() => expect(api.listPhotoPeople).toHaveBeenCalledWith(true, expect.any(AbortSignal), 100, 200));
    expect(vi.mocked(api.listPhotoPeople).mock.calls.at(-1)?.[1]?.aborted).toBe(false);
    expect(await screen.findByRole('option', { name: 'Morgan' })).toBeTruthy();
  });

  it('names a single ungrouped face as a new person', async () => {
    const onPatch = vi.fn();
    vi.mocked(api.listPhotoFaces).mockResolvedValue({ items: [{ id: 'face-1', person_id: null, box: { x: 0.1, y: 0.1, width: 0.2, height: 0.2 }, confidence: 0.95, thumbnail_url: null }] });
    render(<PhotoViewer onClose={vi.fn()} onNearEnd={vi.fn()} onPatch={onPatch} photoId={photo.id} photos={[{ ...photo, person_ids: [] }]} />);
    await userEvent.click(screen.getByRole('button', { name: 'Show photo details' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Name person' }));
    await userEvent.type(screen.getByLabelText('Name'), 'Jamie');
    await userEvent.click(screen.getByRole('button', { name: 'Save person' }));
    expect(api.createPhotoPerson).toHaveBeenCalledWith('Jamie', ['face-1']);
    expect(onPatch).toHaveBeenCalledWith(expect.objectContaining({ person_ids: ['person-4'] }));
  });
});
