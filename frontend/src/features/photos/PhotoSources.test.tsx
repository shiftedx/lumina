import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from './photoApi';
import { PhotoSources } from './PhotoSources';

vi.mock('./photoApi', async (original) => ({
  ...await original<typeof import('./photoApi')>(),
  listPhotoSources: vi.fn(), createPhotoSource: vi.fn(), scanPhotoSource: vi.fn(), cancelPhotoSource: vi.fn(), deletePhotoSource: vi.fn(),
}));

const source: api.PhotoSource = {
  id: 'source-1', root_id: 'root-1', root_label: 'Family NAS', relative_folder: 'Photos', owner_user_id: 'owner-1', visibility: 'shared',
  state: 'scanning', enabled: true, indexed_count: 1200, error_code: null, error_count: 0, last_scan_at: '2026-10-24T12:00:00Z', last_completed_at: '2026-10-23T12:00:00Z',
};
const roots: api.PhotoSourceRoot[] = [{ id: 'root-1', label: 'Family NAS', online: true }];

describe('PhotoSources', () => {
  beforeEach(() => {
    vi.mocked(api.listPhotoSources).mockReset().mockResolvedValue({ items: [], roots });
    vi.mocked(api.createPhotoSource).mockReset().mockResolvedValue({ ...source, state: 'queued', indexed_count: 0, last_scan_at: null, last_completed_at: null });
    vi.mocked(api.scanPhotoSource).mockReset();
    vi.mocked(api.cancelPhotoSource).mockReset();
    vi.mocked(api.deletePhotoSource).mockReset();
  });

  it('adds a confined folder from a registered root and queues its first scan', async () => {
    render(<PhotoSources onChanged={vi.fn()} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Add source' }));
    await userEvent.type(screen.getByLabelText(/Folder inside that root/), 'Family/Photos');
    await userEvent.click(screen.getByRole('combobox', { name: 'Source visibility' }));
    await userEvent.click(screen.getByRole('option', { name: 'Household' }));
    await userEvent.click(screen.getByRole('button', { name: 'Add and scan' }));
    expect(api.createPhotoSource).toHaveBeenCalledWith({ root_id: 'root-1', relative_folder: 'Family/Photos', visibility: 'shared' });
    expect(await screen.findByText(/Family NAS \/ Photos/)).toBeTruthy();
  });

  it('cancels an active scan without offering any original-file deletion', async () => {
    vi.mocked(api.listPhotoSources).mockResolvedValue({ items: [source], roots });
    vi.mocked(api.cancelPhotoSource).mockResolvedValue({ ...source, state: 'canceling' });
    render(<PhotoSources onChanged={vi.fn()} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel scan' }));
    await waitFor(() => expect(api.cancelPhotoSource).toHaveBeenCalledWith(source.id));
    expect(screen.getByText(/never moves, edits, or deletes the original files/i)).toBeTruthy();
  });

  it('reports partial scans without hiding successfully indexed photos', async () => {
    vi.mocked(api.listPhotoSources).mockResolvedValue({ items: [{ ...source, state: 'failed', error_code: 'partial_scan', error_count: 3 }], roots });
    render(<PhotoSources onChanged={vi.fn()} />);
    expect(await screen.findByText(/scan finished, but some files could not be indexed/i)).toBeTruthy();
    expect(screen.getByText(/3 files need attention/i)).toBeTruthy();
    expect(screen.getByText(/1,200 photos indexed/i)).toBeTruthy();
  });
});
