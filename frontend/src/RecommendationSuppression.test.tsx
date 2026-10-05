import axe from 'axe-core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { HomeSurface } from './features/home/HomeSurface';
import type { LiveShelfProps } from './features/home/LiveShelf';
import { SuppressionSettingsCard } from './features/settings/youCards';
import { RecoFeedbackProvider } from './features/reco/recoFeedback';
import { LIST_ID, recoEntry, suppression, suppressionList } from './test/recoFixtures';
import type { MemberInterests, MemberRecommendationSnapshot, SuppressionList, UserProfile, YouTubeSearchResult } from './types';

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

const interests: MemberInterests = { categories: [{ key: 'music', label: 'Music' }], selected_keys: ['music'] };

function remoteItem(id: string, uploader: string): YouTubeSearchResult {
  return { id, title: `Video ${id}`, uploader, webpage_url: `https://example.test/r/${id}`, artwork_url: `/api/artwork/remote/${id}`, duration: 300, view_count: 1000 } as YouTubeSearchResult;
}

function recommendations(...items: YouTubeSearchResult[]): MemberRecommendationSnapshot {
  return { items, categories: [], state: 'ready', refreshing: false, stale: false, last_success_at: null, refreshed_at: null, next_refresh_at: null, error: null } as MemberRecommendationSnapshot;
}

function renderRecommendedHome(overrides: Partial<Parameters<typeof HomeSurface>[0]> = {}) {
  const handlers = { suppress: vi.fn().mockResolvedValue({ id: 'sup-1' }), restore: vi.fn().mockResolvedValue(undefined), onExpired: vi.fn(), onError: vi.fn() };
  const view = render(
    <RecoFeedbackProvider resetKey="member-1" {...handlers}>
      <HomeSurface
        channels={[]}
        continueWatching={[]}
        homeShelves={null}
        interests={interests}
        isQueueing={() => false}
        library={[]}
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
        recentLibrary={[]}
        recommendations={recommendations(
          recoEntry('a', 0, { title: 'Video a', uploader: 'Muted Channel' }, { reason: 'Because you finished Harbor walk at dawn' }),
          recoEntry('b', 1, { title: 'Video b', uploader: 'Other Channel' }, { reason: 'Something different · like Harbor walk at dawn', slot: 'explore', reason_code: 'explore' }),
        )}
        subscriptionOutcomes={[]}
        subscriptionVideos={[]}
        user={user}
        {...overrides}
      />
    </RecoFeedbackProvider>,
  );
  return { ...view, ...handlers };
}

describe('Picked for you recommendation controls', () => {
  const trigger = (name: string) => screen.getByRole('button', { name: `More options for ${name}` });

  it('is headed Picked for you and keeps each pick\'s reason in its menu, not under its card', async () => {
    const { container } = renderRecommendedHome();
    // Home stays hidden until the hero decision lands (CLS), so wait for the visible region.
    expect(await screen.findByRole('region', { name: 'Picked for you' })).toBeTruthy();
    expect(screen.queryByText('Because you finished Harbor walk at dawn')).toBeNull();
    expect(container.querySelector('.reco-reason')).toBeNull();
    await userEvent.click(trigger('Video a'));
    expect(screen.getByRole('group', { name: 'Because you finished Harbor walk at dawn' })).toBeTruthy();
  });

  it('hides a pick on Not interested: saves it with its list, then shows Hidden. Undo in its place', async () => {
    const { suppress } = renderRecommendedHome();
    await userEvent.click(trigger('Video a'));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Not interested' }));
    await waitFor(() => expect(suppress).toHaveBeenCalledWith(expect.objectContaining({ scope: 'item', source_id: 'a', list_id: LIST_ID })));
    expect(await screen.findByText('Hidden.')).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Undo' }));
    expect(screen.queryByRole('button', { name: 'More options for Video a' })).toBeNull();
    expect(trigger('Video b')).toBeTruthy();
  });

  it('shows fewer from a channel and Undo restores what that saved', async () => {
    const { suppress, restore } = renderRecommendedHome();
    await userEvent.click(trigger('Video a'));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Show fewer from Muted Channel' }));
    await waitFor(() => expect(suppress).toHaveBeenCalledWith(expect.objectContaining({ scope: 'fewer', uploader: 'Muted Channel' })));
    expect(await screen.findByText('Showing fewer from Muted Channel.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(restore).toHaveBeenCalledWith('sup-1'));
    expect(await screen.findByRole('button', { name: 'More options for Video a' })).toBeTruthy();
  });

  it('keeps the menu, without a reason, when the picks carry no annotation (the switch is off)', () => {
    const { container } = renderRecommendedHome({ recommendations: recommendations(remoteItem('a', 'Muted Channel')) });
    expect(trigger('Video a')).toBeTruthy();
    expect(screen.queryByText(/Because you/)).toBeNull();
    expect(container.querySelector('.g-remote-card [aria-describedby]')).toBeNull();
  });

  it('has no axe violations with the menu open', async () => {
    const { container } = renderRecommendedHome();
    await userEvent.click(trigger('Video a'));
    const results = await axe.run(container, { runOnly: ['button-name', 'aria-prohibited-attr', 'aria-valid-attr-value', 'aria-required-attr', 'aria-required-children', 'aria-allowed-role'] });
    expect(results.violations.flatMap((violation) => violation.nodes.map((node) => node.html))).toEqual([]);
  });
});

describe('SuppressionSettingsCard', () => {
  const suppressions = suppressionList({
    items: [suppression('item', 'item-1', { title: 'An Old Clip', channel_name: 'Creator' })],
    titles: [suppression('title', 'title-1', { title: 'A Film' })],
    fewer: [suppression('fewer', 'fewer-1', { channel_name: 'Quiet Channel', recovers_at: '2027-02-07T10:00:00Z' })],
    channels: [suppression('channel', 'chan-1', { channel_name: 'Muted Channel' })],
  });

  it('lists the four kinds of feedback in spec order, each restorable', async () => {
    const onRestore = vi.fn();
    render(<SuppressionSettingsCard suppressions={suppressions} onRestore={onRestore} />);

    expect(screen.getAllByRole('heading', { level: 3 }).map((heading) => heading.textContent)).toEqual([
      'Not interested: videos', 'Not interested: titles', 'Showing fewer: channel', 'Hidden channels',
    ]);
    expect(screen.getByText('An Old Clip')).toBeTruthy();
    expect(screen.getByText('A Film')).toBeTruthy();
    expect(screen.getByText('Quiet Channel')).toBeTruthy();
    expect(screen.getByText('Muted Channel')).toBeTruthy();

    for (const [name, id] of [['an old clip', 'item-1'], ['a film', 'title-1'], ['quiet channel', 'fewer-1'], ['muted channel', 'chan-1']]) {
      await userEvent.click(screen.getByRole('button', { name: new RegExp(`restore recommendations of ${name}`, 'i') }));
      expect(onRestore).toHaveBeenLastCalledWith(id);
    }
  });

  it('says when a shown-fewer channel is back to normal', () => {
    render(<SuppressionSettingsCard suppressions={suppressions} onRestore={vi.fn()} />);
    expect(screen.getByText(/^Back to normal on .*2027$/)).toBeTruthy();
  });

  it('copes with a server that sends no fewer or titles list (an older answer)', () => {
    const older = { items: suppressions.items, channels: suppressions.channels } as SuppressionList;
    render(<SuppressionSettingsCard suppressions={older} onRestore={vi.fn()} />);
    expect(screen.getAllByRole('heading', { level: 3 }).map((heading) => heading.textContent)).toEqual(['Not interested: videos', 'Hidden channels']);
  });

  it('shows an empty state when nothing is suppressed', () => {
    render(<SuppressionSettingsCard suppressions={suppressionList()} onRestore={vi.fn()} />);
    expect(screen.getByText(/nothing is suppressed/i).className).toContain('g-setting-note');
  });
});
