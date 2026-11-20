import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { MusicMixButton } from './MusicMix';

const mocks = vi.hoisted(() => ({ preview: vi.fn(), save: vi.fn(), append: vi.fn(), play: vi.fn() }));
vi.mock('../../api', () => ({ getContextualMusicMix: mocks.preview, saveMusicPlaylist: mocks.save }));
vi.mock('./musicSession', () => ({ appendMusicMix: mocks.append, playMusicMix: mocks.play, useMusicSession: () => ({ owner: 'member', unavailablePolicy: 'wait', sharedEntries: [{ kind: 'acquisition', state: 'awaiting_import' }] }) }));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.preview.mockResolvedValue({ seed: { id: 'album', kind: 'album', name: 'Album' }, items: [
    { id: 'one', title: 'One', artist: 'Artist', reason: { code: 'seed_track', item_id: 'one' } },
    { id: 'two', title: 'Two', artist: 'Artist', reason: { code: 'shared_genre', genre: 'Jazz' } },
  ], exhausted: true });
  mocks.save.mockResolvedValue({}); mocks.append.mockResolvedValue(undefined); mocks.play.mockResolvedValue(undefined);
});

it('previews ordered reasons, saves without playback, then confirms replacement of a waiting queue', async () => {
  const user = userEvent.setup();
  render(<MusicMixButton enabled id="album" kind="album" name="Album" />);
  await user.click(screen.getByRole('button', { name: 'Instant Mix for Album' }));
  const dialog = await screen.findByRole('dialog', { name: 'Instant Mix · Album' });
  expect((await within(dialog).findAllByRole('listitem')).map((row) => row.textContent)).toEqual(['OneArtist · From this album', 'TwoArtist · Shared genre: Jazz']);
  await user.click(within(dialog).getByRole('button', { name: 'Save as Playlist' }));
  expect(mocks.save).toHaveBeenCalledWith({ name: 'Album mix', shared: false, item_ids: ['one', 'two'] });
  expect(mocks.append).not.toHaveBeenCalled(); expect(mocks.play).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole('button', { name: 'Play Mix' }));
  const confirmation = screen.getByRole('dialog', { name: 'Replace queue?' });
  expect(confirmation.textContent).toContain('waiting request');
  await user.click(within(confirmation).getByRole('button', { name: 'Play Mix' }));
  expect(mocks.play).toHaveBeenCalledOnce();
});

it('leaves playback alone when dismissed or empty', async () => {
  mocks.preview.mockResolvedValue({ seed: { id: 'one', kind: 'track', name: 'One' }, items: [], exhausted: true });
  const user = userEvent.setup();
  render(<MusicMixButton enabled id="one" kind="track" name="One" />);
  await user.click(screen.getByRole('button', { name: 'Track Radio for One' }));
  expect(await screen.findByText('No matching tracks are available for this mix.')).toBeTruthy();
  await user.keyboard('{Escape}');
  expect(mocks.append).not.toHaveBeenCalled(); expect(mocks.play).not.toHaveBeenCalled(); expect(mocks.save).not.toHaveBeenCalled();
});
