import axe from 'axe-core';
import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HomeSurface } from './features/home/HomeSurface';
import type { LiveShelfProps } from './features/home/LiveShelf';
import type { LibraryItem, PlaybackProgress, UserProfile, YouTubeSearchResult } from './types';

vi.mock('./features/home/LiveShelf', async () => {
  const { useEffect } = await import('react');
  return {
    LiveShelf: ({ onStatus }: LiveShelfProps) => {
      useEffect(() => onStatus('empty'), []); // eslint-disable-line react-hooks/exhaustive-deps -- a stand-in that reports once
      return null;
    },
  };
});
const homeApi = vi.hoisted(() => ({ listNextUp: vi.fn(), listTitles: vi.fn(), getHomeTitleRows: vi.fn(), listHouseholdCollections: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...homeApi }));
homeApi.listNextUp.mockResolvedValue([]);
homeApi.listTitles.mockResolvedValue({ items: [] });
homeApi.getHomeTitleRows.mockResolvedValue({ rows: [] });
homeApi.listHouseholdCollections.mockResolvedValue([]);

const noop = vi.fn();
const user = { id: 'user-1', username: 'one', display_name: 'One Person', role: 'viewer', is_active: true } as UserProfile;

function libraryItem(id: string): LibraryItem {
  return { id, remote_id: `remote-${id}`, title: `Saved ${id}`, uploader: `Owner ${id}`, status: 'available', webpage_url: `https://example.test/${id}`, duration: 600 } as LibraryItem;
}

function playback(id: string): PlaybackProgress {
  return { id: `pb-${id}`, item_id: id, item: libraryItem(id), position_seconds: 120, duration_seconds: 600, completed: false } as PlaybackProgress;
}

function remoteItem(id: string): YouTubeSearchResult {
  return { id, title: `Video ${id}`, uploader: `Channel ${id}`, webpage_url: `https://example.test/r/${id}`, artwork_url: `/api/artwork/remote/${id}`, duration: 300, view_count: 1000 } as YouTubeSearchResult;
}

// Seeded-tier Home: Continue Watching populated (playback progress cards),
// subscription videos populated, and a full recently-saved shelf — the state the
// mocked resilience Home never reaches, where the aria violation was measured.
function renderSeededHome() {
  return render(
    <HomeSurface
      channels={[{ id: 'ch-1', label: 'Followed', source_type: 'channel' } as never]}
      continueWatching={[playback('a'), playback('b')]}
      homeShelves={null}
      isQueueing={() => false}
      library={[libraryItem('a'), libraryItem('b'), libraryItem('c')]}
      libraryProblem={null}
      libraryRetrying={false}
      libraryState="ready"
      onHomeShelvesChange={noop}
      onNavigate={noop}
      onOpenLibrary={noop}
      onOpenRemote={noop}
      onOpenRoute={noop}
      onOpenTitle={noop}
      onPlay={noop}
      onQueueRemote={noop}
      onRetryLibrary={noop}
      onSignIn={noop}
      recentLibrary={[libraryItem('a'), libraryItem('b'), libraryItem('c')]}
      subscriptionOutcomes={[]}
      subscriptionVideos={[remoteItem('x'), remoteItem('y')]}
      user={user}
    />,
  );
}

function renderLoadingHome() {
  return render(
    <HomeSurface
      channels={[{ id: 'ch-1', label: 'Followed', source_type: 'channel' } as never]}
      continueWatching={[]}
      homeShelves={null}
      isQueueing={() => false}
      library={[]}
      libraryProblem={null}
      libraryRetrying={false}
      libraryState="loading"
      onHomeShelvesChange={noop}
      onNavigate={noop}
      onOpenLibrary={noop}
      onOpenRemote={noop}
      onOpenRoute={noop}
      onOpenTitle={noop}
      onPlay={noop}
      onQueueRemote={noop}
      onRetryLibrary={noop}
      onSignIn={noop}
      recentLibrary={[]}
      subscriptionOutcomes={[{ channel: { id: 'ch-1', label: 'Followed' }, status: 'loading', items: [] } as never]}
      subscriptionVideos={[]}
      user={user}
    />,
  );
}

async function prohibitedAttrOffenders(container: HTMLElement): Promise<string[]> {
  const results = await axe.run(container, { runOnly: ['aria-prohibited-attr'] });
  return results.violations.flatMap((violation) => violation.nodes.map((node) => node.html));
}

describe('seeded Home surface accessibility', () => {
  it('carries no aria-prohibited-attr violation on the loaded Continue Watching shelf or its cards', async () => {
    const { container } = renderSeededHome();
    // The populated Continue Watching shelf is the state the harness caught: its
    // scroller <div> must carry a role that permits its aria-label.
    const shelf = container.querySelector('[data-shelf-section="continue"]');
    expect(shelf).not.toBeNull();
    expect(shelf?.tagName).toBe('SECTION');
    expect(shelf?.hasAttribute('aria-labelledby')).toBe(true);
    expect(await prohibitedAttrOffenders(container)).toEqual([]);
  });

  it('carries no aria-prohibited-attr violation while shelves are still loading', async () => {
    const { container } = renderLoadingHome();
    expect(await prohibitedAttrOffenders(container)).toEqual([]);
  });
});
