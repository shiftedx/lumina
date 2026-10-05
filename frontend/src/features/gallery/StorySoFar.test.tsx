import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { RecapResponse } from '../../types';
import { pollDelay } from '../watch/usePollJob';

const api = vi.hoisted(() => ({ getRecap: vi.fn(), requestRecap: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));
const { STORY_ABANDON_MS, StorySoFar } = await import('./StorySoFar');

const recap = (patch: Partial<RecapResponse>): RecapResponse => ({ episode_title_id: 'ep-2-4', state: 'none', points: [], fallback: [], suggest_preroll: false, ...patch });
const guide = [
  { episode_id: 'ep-2-3', name: 'The Ferry', season_number: 2, index_number: 3, overview: 'The ferry is late again.' },
  { episode_id: 'ep-2-2', name: 'Fog Line', season_number: 2, index_number: 2, overview: null },
];
const points = (count: number) => Array.from({ length: count }, (_, index) => ({ text: `Point ${index + 1}`, citations: [] }));
const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0); });
const items = () => screen.getAllByRole('listitem').map((item) => item.textContent);

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks(); });

describe('StorySoFar', () => {
  it('shows the assistant’s points from the dialogue, three at first and up to eight with Read all', async () => {
    api.getRecap.mockResolvedValue(recap({ state: 'succeeded', points: points(10), fallback: guide }));
    render(<StorySoFar episodeId="ep-2-4" />);
    await flush();
    expect(screen.getByRole('heading', { level: 2, name: 'The story so far' })).toBeTruthy();
    expect(screen.getByText('From the episodes’ dialogue')).toBeTruthy();
    expect(items()).toEqual(['Point 1', 'Point 2', 'Point 3']);
    // A TV remote's arrows reach Read all like any other control on the page.
    expect(screen.getByRole('button', { name: 'Read all' }).hasAttribute('data-focus-item')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Read all' }));
    expect(items()).toHaveLength(8);
    expect(screen.queryByRole('button', { name: 'Read all' })).toBeNull();
    expect(api.requestRecap).not.toHaveBeenCalled();
  });

  it('asks for a recap once when none exists, then polls until it is ready', async () => {
    api.getRecap.mockResolvedValueOnce(recap({ fallback: guide })).mockResolvedValue(recap({ state: 'succeeded', points: points(2), fallback: guide }));
    api.requestRecap.mockResolvedValue(recap({ state: 'queued', fallback: guide }));
    render(<StorySoFar episodeId="ep-2-4" />);
    await flush();
    expect(api.requestRecap).toHaveBeenCalledTimes(1);
    expect(api.requestRecap).toHaveBeenCalledWith('ep-2-4');
    expect(screen.queryByRole('heading', { name: 'The story so far' })).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(pollDelay(0)); });
    expect(items()).toEqual(['Point 1', 'Point 2']);
    expect(api.requestRecap).toHaveBeenCalledTimes(1);
  });

  it('uses the episode guide when the AI is off, and skips episodes with no overview', async () => {
    api.getRecap.mockResolvedValue(recap({ state: 'fallback', fallback: guide }));
    render(<StorySoFar episodeId="ep-2-4" />);
    await flush();
    expect(screen.getByText('From the episode guide')).toBeTruthy();
    expect(items()).toEqual(['S2 · E3 · The Ferry The ferry is late again.']);
    expect(screen.queryByRole('button', { name: 'Read all' })).toBeNull();
    expect(api.requestRecap).not.toHaveBeenCalled();
  });

  it('falls back to the guide when the recap failed, cannot be requested, or takes longer than 8 s', async () => {
    api.getRecap.mockResolvedValue(recap({ state: 'failed', fallback: guide }));
    const first = render(<StorySoFar episodeId="ep-2-4" />);
    await flush();
    expect(screen.getByText('From the episode guide')).toBeTruthy();
    first.unmount();

    api.getRecap.mockResolvedValue(recap({ fallback: guide }));
    api.requestRecap.mockRejectedValue(new Error('ai_not_configured'));
    const second = render(<StorySoFar episodeId="ep-2-4" />);
    await flush();
    expect(screen.getByText('From the episode guide')).toBeTruthy();
    second.unmount();

    api.getRecap.mockResolvedValue(recap({ state: 'running', fallback: guide }));
    render(<StorySoFar episodeId="ep-2-4" />);
    await flush();
    expect(screen.queryByText('From the episode guide')).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(STORY_ABANDON_MS); });
    expect(screen.getByText('From the episode guide')).toBeTruthy();
    const polls = api.getRecap.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(api.getRecap.mock.calls.length).toBe(polls);
  });

  it('renders nothing when there is nothing to show or the recap cannot be read', async () => {
    api.getRecap.mockResolvedValue(recap({ state: 'fallback', fallback: [] }));
    const first = render(<StorySoFar episodeId="ep-2-4" />);
    await flush();
    expect(first.container.innerHTML).toBe('');
    first.unmount();

    api.getRecap.mockRejectedValue(new Error('Episode not found'));
    const second = render(<StorySoFar episodeId="ep-2-4" />);
    await flush();
    await act(async () => { await vi.advanceTimersByTimeAsync(STORY_ABANDON_MS); });
    expect(second.container.innerHTML).toBe('');
  });
});
