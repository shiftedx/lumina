import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ExploreSurface } from './features/explore/ExploreSurface';
import type { PopularSnapshot, YouTubeSearchResult } from './types';

const baseSnapshot: PopularSnapshot = {
  items: [],
  categories: [],
  state: 'loading',
  refreshing: true,
  stale: false,
};

function renderExplore(popular: PopularSnapshot | null, results: YouTubeSearchResult[] = [], onOpen = vi.fn(), onQueue = vi.fn(), query = '') {
  return render(<ExploreSurface error={null} isQueueing={() => false} library={[]} loading={false} onOpen={onOpen} onQueue={onQueue} onSearch={vi.fn()} popular={popular} popularError={null} query={query} results={results} />);
}

describe('Explore Popular now states', () => {
  it('announces loading and renders a terminal empty state', () => {
    const { rerender } = renderExplore(baseSnapshot);
    expect(screen.getByRole('status', { name: 'Loading Popular' })).toBeTruthy();

    rerender(<ExploreSurface error={null} isQueueing={() => false} library={[]} loading={false} onOpen={vi.fn()} onQueue={vi.fn()} onSearch={vi.fn()} popular={{ ...baseSnapshot, state: 'empty', refreshing: false }} popularError={null} query="" results={[]} />);
    expect(screen.getByText('Nothing popular yet.')).toBeTruthy();
    expect(screen.queryByRole('status', { name: 'Loading Popular' })).toBeNull();
  });

  it('labels partial results while keeping successful cards visible', () => {
    renderExplore({
      ...baseSnapshot,
      state: 'partial',
      refreshing: false,
      categories: [{ key: 'gaming', label: 'Gaming', state: 'ready' }],
      items: [{ id: 'one', title: 'One result', artwork_url: '/api/artwork/remote/one', webpage_url: 'https://example.test/watch/one', category_keys: ['gaming'] }],
    });
    expect(screen.getByText('Showing a partial feed while more categories warm up.')).toBeTruthy();
    expect(screen.getByRole('button', { name: /^One result/ })).toBeTruthy();
  });

  it('does not mistake zero-item partial or stale snapshots for loading', () => {
    const { rerender } = renderExplore({ ...baseSnapshot, state: 'partial', refreshing: false });
    expect(screen.getByText('Nothing popular yet.')).toBeTruthy();
    expect(screen.queryByRole('status', { name: 'Loading Popular' })).toBeNull();

    rerender(<ExploreSurface error={null} isQueueing={() => false} library={[]} loading={false} onOpen={vi.fn()} onQueue={vi.fn()} onSearch={vi.fn()} popular={{ ...baseSnapshot, state: 'stale', refreshing: false, stale: true }} popularError={null} query="" results={[]} />);
    expect(screen.getByText('Nothing popular yet.')).toBeTruthy();
    expect(screen.queryByRole('status', { name: 'Loading Popular' })).toBeNull();
  });
});

describe('Explore capability gating', () => {
  const entry = (can_acquire: boolean) => ({
    id: 'x', title: 'Gated source', uploader: 'Anon', webpage_url: 'https://example.test/x', artwork_url: '/api/a',
    capabilities: { provider: 'generic', lifecycle: 'vod', can_play: true, can_acquire, chat: { live: 'unavailable', replay: 'unavailable' } },
  }) as YouTubeSearchResult;

  it('shows no Save action for a source Lumina cannot acquire, and one it can operate from the keyboard', async () => {
    const user = userEvent.setup();
    const onQueue = vi.fn();
    const { unmount } = renderExplore(null, [entry(false)], vi.fn(), vi.fn(), 'x');
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    unmount();
    const actionable = entry(true);
    renderExplore(null, [actionable], vi.fn(), onQueue, 'x');
    screen.getByRole('button', { name: 'Save' }).focus();
    await user.keyboard('{Enter}');
    expect(onQueue).toHaveBeenCalledWith(actionable);
  });
});
