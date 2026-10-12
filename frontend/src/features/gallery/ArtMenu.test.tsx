import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api';
import { movieSummary } from '../../test/galleryFixtures';
import { recoEntry, recoTitle } from '../../test/recoFixtures';
import { remoteEntry } from '../../test/remoteFixtures';
import { ArtActionsProvider, type ArtActions, ArtMenu } from './ArtMenu';
import { RecoFeedbackProvider, RecoCard } from '../reco/recoFeedback';
import { remoteTarget } from '../reco/recoModel';

const CHANNEL = 'UC0123456789abcdefghijkl';
const actions = (patch: Partial<ArtActions> = {}): ArtActions => ({ user: { role: 'member' }, navigate: vi.fn(), playLibrary: vi.fn(), openRemote: vi.fn(), saveRemote: vi.fn(), ...patch });
const labels = () => [...document.querySelectorAll('[role="menuitem"]')].map((row) => row.textContent);
const open = (name: string) => userEvent.click(screen.getByRole('button', { name: `More options for ${name}` }));

beforeEach(() => { vi.restoreAllMocks(); });

describe('ArtMenu on library titles', () => {
  const film = movieSummary('m1', { name: 'Arrival', play_item_id: 'item-1' });

  it('offers play, watchlist, watched and favorite to a member, and no editing', async () => {
    const a = actions();
    render(<ArtActionsProvider value={a}><ArtMenu subject={{ kind: 'title', title: film }} /></ArtActionsProvider>);
    await open('Arrival');
    expect(labels()).toEqual(['Play', 'Add to watchlist', 'Mark watched', 'Favorite']);
    await userEvent.click(screen.getByRole('menuitem', { name: 'Play' }));
    expect(a.playLibrary).toHaveBeenCalledWith('item-1');
  });

  it('adds Edit details and Change artwork for a member who may edit, opening the editor route', async () => {
    const a = actions({ user: { role: 'member', can_edit_details: true } });
    render(<ArtActionsProvider value={a}><ArtMenu subject={{ kind: 'title', title: film }} /></ArtActionsProvider>);
    await open('Arrival');
    expect(labels()).toEqual(expect.arrayContaining(['Edit details', 'Change artwork']));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Change artwork' }));
    expect(a.navigate).toHaveBeenCalledWith('/title/m1/edit?tab=artwork');
  });

  it('marks watched through the title page\'s API and then offers the opposite', async () => {
    const watched = vi.spyOn(api, 'setTitleWatched').mockResolvedValue({ ...film.user_data, played: true });
    render(<ArtActionsProvider value={actions()}><ArtMenu subject={{ kind: 'title', title: film }} /></ArtActionsProvider>);
    await open('Arrival');
    await userEvent.click(screen.getByRole('menuitem', { name: 'Mark watched' }));
    expect(watched).toHaveBeenCalledWith('m1', true);
    await open('Arrival');
    expect(screen.getByRole('menuitem', { name: 'Mark unwatched' })).toBeTruthy();
  });

  it('renders nothing for a title with nothing to offer outside the app', () => {
    const { container } = render(<ArtMenu subject={{ kind: 'title', title: film }} />);
    expect(container.querySelector('.g-art-menu')).toBeNull();
  });
});

describe('ArtMenu on web videos', () => {
  const video = remoteEntry('v1', { title: 'Harbor walk', uploader: 'Harbor Films', uploader_id: CHANNEL });

  it('offers play, queue, save and the channel; Escape closes it and focus returns to the button', async () => {
    const a = actions();
    render(<ArtActionsProvider value={a}><ArtMenu subject={{ kind: 'remote', item: video }} /></ArtActionsProvider>);
    await open('Harbor walk');
    expect(labels()).toEqual(['Play', 'Add to queue', 'Save to vault', 'Open channel']);
    await userEvent.click(screen.getByRole('menuitem', { name: 'Save to vault' }));
    expect(a.saveRemote).toHaveBeenCalledWith(video);
    await open('Harbor walk');
    await userEvent.click(screen.getByRole('menuitem', { name: 'Open channel' }));
    expect(a.navigate).toHaveBeenCalledWith(`/channel/youtube/${CHANNEL}`);
    await open('Harbor walk');
    fireEvent.keyDown(screen.getByRole('menuitem', { name: 'Play' }), { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'More options for Harbor walk' }));
  });

  it('does not offer Save on a live stream', async () => {
    const live = { ...video, capabilities: { ...video.capabilities, lifecycle: 'live' } } as typeof video;
    render(<ArtActionsProvider value={actions()}><ArtMenu subject={{ kind: 'remote', item: live }} /></ArtActionsProvider>);
    await open('Harbor walk');
    expect(labels()).not.toContain('Save to vault');
  });
});

describe('ArtMenu inside a recommendation card', () => {
  it('heads the menu with the reason and sends Not interested through the same suppression call', async () => {
    const suppress = vi.fn().mockResolvedValue({ id: 's1' });
    const item = recoEntry('v1', 0, { title: 'Harbor walk', uploader: 'Harbor Films' });
    render(
      <RecoFeedbackProvider onError={vi.fn()} onExpired={vi.fn()} resetKey="m" restore={vi.fn()} suppress={suppress}>
        <RecoCard reco={item.reco} target={remoteTarget(item)}><ArtMenu subject={{ kind: 'remote', item }} /></RecoCard>
      </RecoFeedbackProvider>,
    );
    await open('Harbor walk');
    const menu = screen.getByRole('menu');
    expect(within(menu).getByRole('group', { name: item.reco!.reason })).toBeTruthy();
    await userEvent.click(screen.getByRole('menuitem', { name: 'Not interested' }));
    expect(suppress).toHaveBeenCalledWith(expect.objectContaining({ scope: 'item', source_id: 'v1', list_id: item.reco!.list_id }));
  });

  it('offers title feedback only on an annotated title', async () => {
    const title = recoTitle(1);
    render(
      <RecoFeedbackProvider onError={vi.fn()} onExpired={vi.fn()} resetKey="m" restore={vi.fn()} suppress={vi.fn()}>
        <RecoCard reco={title.reco} target={{ kind: 'title', title }}><ArtMenu subject={{ kind: 'title', title }} /></RecoCard>
      </RecoFeedbackProvider>,
    );
    await open(title.name);
    expect(labels()).toEqual(['Not interested']);
  });
});
