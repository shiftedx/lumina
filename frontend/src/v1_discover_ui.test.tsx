import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ExploreSurface } from './features/explore/ExploreSurface';
import type { LibraryItem, TitleSummary, YouTubeSearchResult } from './types';

const noop = vi.fn();
const remote = (id: string, source: YouTubeSearchResult['source'], kind: YouTubeSearchResult['kind'] = 'video') => ({ id, title: `Result ${id}`, uploader: 'Creator', source, kind, webpage_url: `https://example.test/${id}` }) as YouTubeSearchResult;
const saved = { id: 'lib-1', remote_id: 'r-lib', title: 'Saved harbour film', uploader: 'Owner', status: 'available', webpage_url: 'https://example.test/lib', duration: 60 } as LibraryItem;

function renderExplore(props: Partial<Parameters<typeof ExploreSurface>[0]> = {}) {
  return render(
    <ExploreSurface
      error={null} isQueueing={() => false} library={[]} loading={false} onOpen={noop} onQueue={noop} onSearch={noop} popular={null} popularError={null}
      query="harbour" results={[remote('y1', 'youtube'), remote('y2', 'youtube', 'short'), remote('s1', 'soundcloud')]}
      {...props}
    />,
  );
}

describe('Discover multi-source results', () => {
  it('test_discover_partial_sources: a failed provider shows an inline retry while other results stay usable', () => {
    const onSearch = vi.fn();
    renderExplore({ onSearch, results: [remote('y1', 'youtube')], sourceErrors: [{ source: 'soundcloud', message: 'HTTP Error 429: Too Many Requests', retryable: true }] });
    expect(screen.getByRole('alert').textContent).toContain('SoundCloud results are unavailable');
    expect(screen.getByRole('button', { name: /^Result y1/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Retry SoundCloud' }));
    expect(onSearch).toHaveBeenCalledWith('harbour');
  });

  it('filters by source and library, with the results grouped by kind', () => {
    renderExplore({ libraryResults: [saved], onOpenLibrary: noop });
    expect(screen.getByRole('heading', { level: 2, name: 'Shorts' })).toBeTruthy();
    expect(screen.getByRole('heading', { level: 2, name: 'In your library' })).toBeTruthy();
    expect(screen.queryByRole('group', { name: 'Type' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'SoundCloud' }));
    expect(screen.queryByRole('button', { name: /^Result y1/ })).toBeNull();
    expect(screen.getByRole('button', { name: /^Result s1/ })).toBeTruthy();

    expect(screen.queryByRole('heading', { level: 2, name: 'In your library' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'All' }));
    fireEvent.click(screen.getByRole('button', { name: 'Your library (1)' }));
    expect(screen.getByRole('button', { name: /^Saved harbour film/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Result y1/ })).toBeNull();
  });

  it('keeps previous results visible, labelled, while a refresh is in flight and offers more', () => {
    const onLoadMore = vi.fn();
    const { rerender } = renderExplore({ onLoadMore });
    fireEvent.click(screen.getByRole('button', { name: 'More results' }));
    expect(onLoadMore).toHaveBeenCalledTimes(1);
    rerender(<ExploreSurface error={null} isQueueing={() => false} library={[]} loading onLoadMore={onLoadMore} onOpen={noop} onQueue={noop} onSearch={noop} popular={null} popularError={null} query="harbour" results={[remote('y1', 'youtube')]} />);
    expect(screen.queryByRole('button', { name: 'More results' })).toBeNull();
    expect(screen.getByRole('button', { name: /^Result y1/ })).toBeTruthy();
  });

  it('moves focus between result cards with arrow keys', () => {
    renderExplore();
    const first = screen.getByRole('button', { name: /^Result y1/ });
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowRight' });
    expect(document.activeElement).not.toBe(first);
    expect(document.activeElement?.hasAttribute('data-focus-item')).toBe(true);
  });

  it('lists title hits from the vault search before library files', () => {
    const onOpenTitle = vi.fn();
    const hit: TitleSummary = { id: 'm1', type: 'movie', name: 'Arrival', year: 2016, genres: [], added_at: '2026-09-01T00:00:00Z', user_data: { played: false, is_favorite: false, position_seconds: 0 } };
    renderExplore({ onOpenTitle, titleResults: [hit] });
    const titles = screen.getByRole('region', { name: 'In your library' });
    fireEvent.click(within(titles).getByRole('button', { name: /Arrival/ }));
    expect(onOpenTitle).toHaveBeenCalledWith(hit);
  });
});
