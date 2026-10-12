import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { watchSurface } from '../../test/watchSurface';
import type { LibraryItem } from '../../types';

// Stands in for the player: it only mounts the playback menu the surface builds.
vi.mock('../../localPlayer', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../localPlayer')>()),
  LocalLibraryPlayer: ({ extensions }: { extensions?: { menu?: React.ReactNode } }) => <div className="lumina-player">{extensions?.menu}</div>,
}));
vi.mock('../../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api')>()),
  listSubtitleTracks: vi.fn().mockResolvedValue([]),
  getMuteRanges: vi.fn().mockResolvedValue([]),
  getLiveDiscovery: vi.fn().mockResolvedValue({ items: [], hero: [] }),
}));

afterEach(() => vi.restoreAllMocks());

const item = { id: 'l1', title: 'Film', kind: 'video', status: 'available', duration: 600 } as LibraryItem;

describe('caption style in the watch menu', () => {
  it('offers Caption style and reports the chosen size', async () => {
    const onCaptionsChange = vi.fn();
    render(watchSurface({ selection: { kind: 'library', item }, captions: { size: 'medium', background: 'shadow' }, onCaptionsChange }));
    await userEvent.click(await screen.findByRole('button', { name: 'Playback settings' }));
    await userEvent.click(screen.getByRole('button', { name: 'Caption style…' }));
    await userEvent.click(screen.getByRole('radio', { name: 'Large' }));
    expect(onCaptionsChange).toHaveBeenCalledWith({ size: 'large', background: 'shadow' });
  });
});
