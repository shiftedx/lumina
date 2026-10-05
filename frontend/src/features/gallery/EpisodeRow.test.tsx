import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { episodeSummary, userData } from '../../test/galleryFixtures';
import type { TitleSummary } from '../../types';
import type { GalleryArtProps } from './GalleryArt';
import { playNextScrollLeft } from './titlePageModel';

const api = vi.hoisted(() => ({ listSeasonEpisodes: vi.fn(), getEpisodeSummaries: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));
const art = vi.hoisted(() => ({ props: [] as GalleryArtProps[] }));
vi.mock('./GalleryArt', () => ({
  GalleryArt: (props: GalleryArtProps) => { art.props.push(props); return <span aria-hidden="true" data-art={props.kind} />; },
}));
const { EpisodeRow } = await import('./EpisodeRow');

afterEach(() => { vi.resetAllMocks(); vi.restoreAllMocks(); art.props.length = 0; });

const watched = userData({ played: true });
const inProgress = userData({ position_seconds: 960, duration_seconds: 2640 });
const episode = (index: number, patch: Partial<TitleSummary> = {}) => episodeSummary(1, index, { play_item_id: `i1${index}`, ...patch });
const row = (props: Partial<Parameters<typeof EpisodeRow>[0]> = {}) => <EpisodeRow onPlay={() => undefined} playNextId={null} season={1} seriesId="series-1" {...props} />;

describe('EpisodeRow', () => {
  it('offers Edit details per episode only when the member can edit', async () => {
    api.listSeasonEpisodes.mockResolvedValue([episode(1), episode(2)]);
    api.getEpisodeSummaries.mockResolvedValue({ available: false, items: [] });
    const onEdit = vi.fn();
    const { unmount } = render(row());
    await screen.findByRole('button', { name: /Episode 1/ });
    expect(screen.queryByRole('button', { name: /More for/ })).toBeNull();
    unmount();
    render(row({ canEdit: true, onEdit }));
    await userEvent.click((await screen.findAllByRole('button', { name: /More for/ }))[0]);
    await userEvent.click(screen.getByRole('menuitem', { name: 'Edit details' }));
    expect(onEdit.mock.calls[0][0]).toBe(episode(1).id);
  });

  it('shows each episode’s still, number, time and teaser, with the unwatched corner or the progress bar', async () => {
    api.listSeasonEpisodes.mockResolvedValue([episode(3), episode(1, { user_data: watched }), episode(2, { user_data: inProgress })]);
    api.getEpisodeSummaries.mockResolvedValue({ available: false, items: [] });
    const onPlay = vi.fn();
    render(row({ onPlay }));
    const first = await screen.findByRole('button', { name: 'Play 1. Episode 1, S1 · E1, watched' });
    const second = screen.getByRole('button', { name: 'Play 2. Episode 2, S1 · E2, in progress, 28 minutes left' });
    const third = screen.getByRole('button', { name: 'Play 3. Episode 3, S1 · E3, unwatched' });
    expect(screen.getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual([
      'Play 1. Episode 1, S1 · E1, watched', 'Play 2. Episode 2, S1 · E2, in progress, 28 minutes left', 'Play 3. Episode 3, S1 · E3, unwatched',
    ]);
    expect(within(first).getByText('1. Episode 1')).toBeTruthy();
    expect(within(first).getByText('44 min')).toBeTruthy();
    expect(within(second).getByText('28 min left')).toBeTruthy();
    expect(within(third).getByText('The keepers argue about the lamp.')).toBeTruthy();
    expect(first.querySelector('.g-marker-triangle, .g-marker-progress')).toBeNull();
    expect((second.querySelector('.g-marker-progress > span') as HTMLElement).style.width).toBe('36%');
    expect(second.querySelector('.g-marker-triangle')).toBeNull();
    expect(third.querySelector('.g-marker-triangle.is-small')).toBeTruthy();
    expect(art.props.at(-1)?.kind).toBe('still');
    // The time and teaser are read as the card's description.
    const described = (button: HTMLElement) => (button.getAttribute('aria-describedby') ?? '').split(' ').map((id) => document.getElementById(id)?.textContent);
    expect(described(second)).toEqual(['28 min left', 'The keepers argue about the lamp.']);
    fireEvent.click(second);
    expect(onPlay).toHaveBeenCalledWith('i12');
  });

  it('shows the AI summary on watched episodes only, and never on unwatched or in-progress ones', async () => {
    api.listSeasonEpisodes.mockResolvedValue([episode(1, { user_data: watched }), episode(2, { user_data: inProgress }), episode(3)]);
    api.getEpisodeSummaries.mockResolvedValue({ available: true, items: [1, 2, 3].map((index) => ({ episode_id: `ep-1-${index}`, overview: `Summary ${index}` })) });
    render(row());
    const first = await screen.findByRole('button', { name: 'Play 1. Episode 1, S1 · E1, watched' });
    expect(await within(first).findByText('Summary 1')).toBeTruthy();
    expect(screen.queryByText('Summary 2')).toBeNull();
    expect(screen.queryByText('Summary 3')).toBeNull();
    expect(api.getEpisodeSummaries).toHaveBeenCalledWith('series-1', 1, { timeoutMs: 8000 });
  });

  it('asks for no summaries while nothing in the season is watched', async () => {
    api.listSeasonEpisodes.mockResolvedValue([episode(1), episode(2)]);
    render(row());
    await screen.findByRole('button', { name: 'Play 1. Episode 1, S1 · E1, unwatched' });
    expect(api.getEpisodeSummaries).not.toHaveBeenCalled();
  });

  it('starts at the next episode with the one before peeking in, and loads the four in view first', async () => {
    vi.spyOn(HTMLElement.prototype, 'offsetLeft', 'get').mockImplementation(function (this: HTMLElement) {
      return this.tagName === 'LI' ? [...(this.parentElement?.children ?? [])].indexOf(this) * 344 : 0;
    });
    api.listSeasonEpisodes.mockResolvedValue([1, 2, 3, 4, 5, 6, 7, 8].map((index) => episode(index, index < 4 ? { user_data: watched } : {})));
    api.getEpisodeSummaries.mockResolvedValue({ available: false, items: [] });
    render(row({ playNextId: 'ep-1-4' }));
    await screen.findByRole('button', { name: 'Play 4. Episode 4, S1 · E4, unwatched' });
    expect(document.querySelector('ol')?.scrollLeft).toBe(playNextScrollLeft(3 * 344, 0));
    expect(art.props.slice(-8).map((props) => props.priority)).toEqual([3, 3, 3, 2, 2, 2, 2, 3]);
  });

  it('never lets a slower earlier season replace the season now selected', async () => {
    let resolveFirst: (value: TitleSummary[]) => void = () => undefined;
    api.listSeasonEpisodes.mockImplementation((_id: string, number: number) => (number === 1 ? new Promise((resolve) => { resolveFirst = resolve; }) : Promise.resolve([episodeSummary(2, 1)])));
    const { rerender } = render(row({ season: 1 }));
    rerender(row({ season: 2 }));
    expect(await screen.findByRole('button', { name: /S2 · E1/ })).toBeTruthy();
    await act(async () => { resolveFirst([episode(1)]); });
    expect(screen.queryByRole('button', { name: /S1 · E1/ })).toBeNull();
  });

  it('says when a season is loading, failed or empty, and tries again', async () => {
    api.listSeasonEpisodes.mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce([]);
    render(row());
    expect(screen.getByRole('status').textContent).toBe('Loading episodes…');
    expect((await screen.findByRole('alert')).textContent).toContain('Lumina could not load this season.');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('No episodes in this season yet.')).toBeTruthy();
  });
});
