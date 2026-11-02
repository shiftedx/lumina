import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { requestHomeEdit } from '../../app/commands';
import { movieSummary, stillItem } from '../../test/galleryFixtures';
import type { PlaybackProgress, UserProfile } from '../../types';
import { ToastProvider } from '../../ui';
import { forgetHomeCache } from './homeCache';
import { HomeSurface, type HomeSurfaceProps } from './HomeSurface';

vi.mock('../../api', async (original) => ({ ...(await original<typeof import('../../api')>()), getHomeArrivals: vi.fn().mockResolvedValue([]), listNextUp: vi.fn().mockResolvedValue([]), listTitles: vi.fn().mockResolvedValue({ items: [] }), getHomeTitleRows: vi.fn().mockResolvedValue({ rows: [] }), listHouseholdCollections: vi.fn().mockResolvedValue([]) }));
const user = { id: 'member', username: 'member', role: 'viewer', is_active: true } as UserProfile;
const title = movieSummary('cached', { name: 'Old movie' });
const progress: PlaybackProgress = { id: 'p', user_id: user.id, item_id: 'i', position_seconds: 1, completed: false, last_watched_at: '', created_at: '', updated_at: '', item: stillItem('i'), title };
const base: HomeSurfaceProps = { user, navigation: { categories: ['recordings'], features: { streaming: false, requests: false } }, library: [], recentLibrary: [stillItem('recording', { kind: 'recording', title: 'Saved recording' })], continueWatching: [progress], subscriptionVideos: [{ id: 'remote', title: 'Remote stream', webpage_url: 'https://example.test' }], subscriptionOutcomes: [], channels: [], homeShelves: [{ id: 'live', visible: false }, { id: 'recently_saved', visible: true }], onHomeShelvesChange: vi.fn(), onNavigate: vi.fn(), onOpenRoute: vi.fn(), onOpenLibrary: vi.fn(), onOpenRemote: vi.fn(), onOpenTitle: vi.fn(), onPlay: vi.fn(), onQueueRemote: vi.fn(), isQueueing: () => false, libraryState: 'ready', libraryProblem: null, libraryRetrying: false, onRetryLibrary: vi.fn(), onSignIn: vi.fn() };
afterEach(() => forgetHomeCache());

it('hides disabled streaming and stale movie content while preserving saved recordings and editor preferences', async () => {
  render(<ToastProvider><HomeSurface {...base} /></ToastProvider>);
  expect(await screen.findByRole('button', { name: /Saved recording/ })).toBeTruthy();
  expect(screen.queryByText('Old movie')).toBeNull();
  expect(screen.queryByText('Remote stream')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Open library' })).toBeNull();
  act(() => requestHomeEdit());
  const editor = await screen.findByRole('list', { name: 'Home shelves' });
  expect(within(editor).queryByText('Live now')).toBeNull();
  expect(within(editor).queryByText('New in Anime')).toBeNull();
  expect(within(editor).getByText('Recently saved')).toBeTruthy();
  fireEvent.click(within(editor).getByRole('switch', { name: 'Show Recently saved' }));
  expect(base.onHomeShelvesChange).toHaveBeenCalledWith(expect.arrayContaining([{ id: 'live', visible: false }, { id: 'recently_saved', visible: false }]));
});
