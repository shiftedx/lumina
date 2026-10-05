import { renderToStaticMarkup } from 'react-dom/server';
import { render, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { ToastProvider } from './ui';
import { describe, expect, it, vi } from 'vitest';

import type { LibraryItem } from './types';
import { classifyCollectionLoadProblem } from './workspace';
import { CollectionAnnouncer, collectionLoadAnnouncement, preloadLikelySurfaces, useShellErrorToast } from './LuminaApp';
import { DownloadsSurface } from './features/downloads/DownloadsSurface';
import { HomeSurface } from './features/home/HomeSurface';
import type { LiveShelfProps } from './features/home/LiveShelf';
import { MobileTabs, Sidebar } from './app/Navigation';
import { OnboardingSurface } from './features/onboarding/Onboarding';
import { SettingsSurface } from './features/settings/SettingsSurface';
import { CollectionRecovery, StaleCollectionNotice } from './features/media/MediaCards';

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

// The title page chunk is only ever imported lazily, so its factory running means it was fetched.
const chunks = vi.hoisted(() => ({ titlePage: vi.fn() }));
vi.mock('./features/gallery/GalleryTitlePage', async (importOriginal) => {
  chunks.titlePage();
  return importOriginal();
});

const noop = vi.fn();

describe('surface preload', () => {
  it('fetches the title page chunk at idle with Watch and Settings, so the first poster click never shows the fallback', async () => {
    expect(chunks.titlePage).not.toHaveBeenCalled();
    preloadLikelySurfaces();
    await waitFor(() => expect(chunks.titlePage).toHaveBeenCalledTimes(1));
  });
});
const offlineProblem = classifyCollectionLoadProblem(new TypeError('offline'));

describe('collection recovery presentation', () => {
  it('mounts the focused playlist workspace in its destination surface', () => {
    const downloads = renderToStaticMarkup(<DownloadsSurface jobs={[]} loadState="empty" onCancel={noop} onClear={noop} onRetry={noop} onRetryLoad={noop} onSignIn={noop} playlistActivity={<section data-testid="playlist-activity">Playlist activity</section>} problem={null} retryingLoad={false} />);

    expect(downloads).toContain('data-testid="playlist-activity"');
  });

  it('never calls a stale empty download snapshot clear or caught up', () => {
    const html = renderToStaticMarkup(
      <DownloadsSurface
        jobs={[]}
        loadState="stale"
        onCancel={noop}
        onClear={noop}
        onRetry={noop}
        onRetryLoad={noop}
        onSignIn={noop}
        problem={offlineProblem}
        retryingLoad={false}
      />,
    );

    expect(html).toContain('No active downloads in the last-known snapshot');
    expect(html).not.toContain('The queue is clear');
  });

  it('keeps the recovery control mounted and focusable during a retry', () => {
    const html = renderToStaticMarkup(<CollectionRecovery label="Library" onRetry={noop} onSignIn={noop} problem={offlineProblem} retrying state="offline" />);

    expect(html).toContain('Library is unavailable');
    expect(html).toMatch(/<button[^>]*aria-disabled="true"[^>]*>.*Trying again/);
    expect(html).not.toContain('disabled=""');
  });

  it('has one polite status source and no duplicate live role in the visible stale notice', () => {
    const html = renderToStaticMarkup(<>
      <CollectionAnnouncer jobsRetrying={false} jobsState="ready" libraryRetrying={false} libraryState="stale" />
      <StaleCollectionNotice label="Library" onRetry={noop} onSignIn={noop} problem={offlineProblem} retrying={false} />
    </>);

    expect(html.match(/role="status"/g)).toHaveLength(1);
    expect(html).toContain('aria-live="polite"');
  });

  it('announces retrying rather than promising recovery is merely available', () => {
    expect(collectionLoadAnnouncement('Library', 'stale', true)).toBe('Retrying Library.');
  });
});

describe('authenticated navigation accessibility', () => {
  const user = { id: 'user-1', username: 'one', display_name: 'One', role: 'viewer', is_active: true } as const;

  it('marks the current desktop destination and exposes an open mobile drawer as modal', () => {
    const desktop = renderToStaticMarkup(<Sidebar activeDownloads={0} mobile={false} onClose={noop} onNavigate={noop} open={false} surface="library" />);
    const mobile = renderToStaticMarkup(<Sidebar activeDownloads={0} mobile onClose={noop} onNavigate={noop} open surface="downloads" />);
    expect(desktop).toMatch(/aria-current="page" aria-label="Library"/);
    expect(mobile).toContain('role="dialog"');
    expect(mobile).toContain('aria-modal="true"');
    expect(mobile).toMatch(/aria-current="page" aria-label="Downloads"/);
  });

  it('removes collapsed desktop navigation from interaction and the accessibility tree', () => {
    const html = renderToStaticMarkup(<Sidebar activeDownloads={0} collapsed mobile={false} onClose={noop} onNavigate={noop} open={false} surface="home" />);
    expect(html).toContain('class="g-sidebar is-hidden ');
    expect(html).toContain('aria-hidden="true"');
    expect(html).toContain('inert=""');
  });

  it('marks the active mobile tab, and Watch does not pretend to be Home', () => {
    const tabs = { activeDownloads: 0, moreOpen: false, onMore: noop, onNavigate: noop, onSearch: noop };
    expect(renderToStaticMarkup(<MobileTabs {...tabs} surface="library" />)).toMatch(/aria-current="page" class="is-active"[^>]*>.*?<span>Library<\/span>/);
    expect(renderToStaticMarkup(<MobileTabs {...tabs} surface="watch" />)).not.toMatch(/aria-current/);
  });

  it('exposes the selected acquisition preset as a roving radio group', () => {
    const html = renderToStaticMarkup(<SettingsSurface formatPreset="best_1080p" onFormatChange={noop} onLogout={noop} section="downloads" user={user} />);
    expect(html).toContain('class="g-segmented"');
    expect(html).toMatch(/<input type="radio"[^>]+checked=""[^>]+value="best_1080p"/);
    // Download quality is one native radio group (the browser roves it); the stream cache lives in Playback.
    expect(html.match(/type="radio"/g)).toHaveLength(4);
  });

});

describe('member interest Home presentation', () => {
  const user = { id: 'member-1', username: 'member', display_name: 'Member', role: 'viewer', is_active: true } as const;
  const base = {
    channels: [], continueWatching: [], homeShelves: null, isQueueing: () => false, library: [], libraryProblem: null, libraryRetrying: false,
    libraryState: 'empty' as const, onHomeShelvesChange: noop, onNavigate: noop, onOpenLibrary: noop, onOpenRemote: noop, onOpenRoute: noop, onOpenTitle: noop,
    onPlay: noop, onQueueRemote: noop, onRetryLibrary: noop, onSignIn: noop, recentLibrary: [], subscriptionOutcomes: [], subscriptionVideos: [], user,
  };
  const music = { categories: [{ key: 'music', label: 'Music' }], selected_keys: ['music'] };

  it('offers a fresh member one start card with interests instead of empty shelves', async () => {
    render(<HomeSurface {...base} interests={{ ...music, selected_keys: [] }} onPersonalize={noop} />);
    await screen.findByRole('button', { name: 'Discover something' });
    expect(screen.getByRole('heading', { name: 'Make Home yours' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Choose interests' })).toBeTruthy();
    expect(screen.queryByText('Recently saved')).toBeNull();
    expect(screen.queryByText('Picked for you')).toBeNull();
  });

  it('always heads the shelf Picked for you, and keeps useful stale recommendations', () => {
    const recommendation = { items: [{ id: 'm-1', title: 'Music pick', webpage_url: 'https://example.test/m-1' }], categories: [], state: 'stale' as const, refreshing: false, stale: true };
    render(<HomeSurface {...base} interests={music} onPersonalize={noop} recommendations={recommendation} />);
    expect(screen.getByRole('region', { name: 'Picked for you' })).toBeTruthy();
    expect(screen.getByText('Showing last-known picks while discovery refreshes.')).toBeTruthy();
    expect(screen.queryByText('Because you chose Music')).toBeNull();
  });

  it('shows Picked for you from the server\'s picks alone, with no interests chosen, and then no start card', () => {
    const recommendation = { items: [{ id: 'w-1', title: 'Watched pick', webpage_url: 'https://example.test/w-1' }], categories: [], state: 'ready' as const, refreshing: false, stale: false };
    render(<HomeSurface {...base} interests={{ ...music, selected_keys: [] }} recommendations={recommendation} />);
    expect(screen.getByRole('region', { name: 'Picked for you' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Make Home yours' })).toBeNull();
  });

  it('keeps Home intact when personalized discovery fails', () => {
    render(<HomeSurface {...base} interests={music} onPersonalize={noop} onRetryRecommendations={noop} recommendationError="Discovery unavailable" recommendations={{ items: [], categories: [], state: 'failed', refreshing: false, stale: false }} />);
    const picked = screen.getByRole('region', { name: 'Picked for you' });
    expect(picked.textContent).toContain('Personalized discovery is taking a pause. Discovery unavailable');
    expect(within(picked).getByRole('button', { name: 'Try again' })).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1 }).textContent).toContain('Good');
  });

  it('keeps the personalized shelf visibly loading while discovery has a cold snapshot', () => {
    render(<HomeSurface {...base} interests={music} recommendations={{ items: [], categories: [], state: 'loading', refreshing: true, stale: false }} />);
    expect(screen.getByRole('region', { name: 'Picked for you' }).getAttribute('aria-busy')).toBe('true');
  });
});

describe('first-run onboarding surface presentation', () => {
  const categories = [{ key: 'music', label: 'Music' }, { key: 'cooking', label: 'Cooking' }];

  it('offers a new member interests, continue, and an explicit skip without endorsement', () => {
    const html = renderToStaticMarkup(
      <OnboardingSurface categories={categories} onContinue={noop} onSkip={noop} phase="setup" />,
    );
    expect(html).toContain('Set up your Home');
    expect(html).toContain('type="checkbox"');
    expect(html).toContain('Music');
    expect(html).toContain('Continue');
    expect(html).toContain('Skip for now');
    // Interest selection is not endorsement: no channel follows or acquisitions.
    expect(html).toContain('will not follow creators');
  });

  it('announces preparation progress politely instead of choosing interests', () => {
    const html = renderToStaticMarkup(
      <OnboardingSurface categories={categories} onContinue={noop} onSkip={noop} phase="preparing" />,
    );
    expect(html).toContain('aria-live="polite"');
    expect(html).toContain('role="status"');
    expect(html).toContain('Preparing your Home');
    expect(html).not.toContain('type="checkbox"');
  });

  it('drops choreography for reduced motion and surfaces a save error', () => {
    const reduced = renderToStaticMarkup(
      <OnboardingSurface categories={categories} onContinue={noop} onSkip={noop} phase="preparing" reducedMotion />,
    );
    expect(reduced).toContain('reduced-motion');

    const errored = renderToStaticMarkup(
      <OnboardingSurface categories={categories} error="We could not save your setup just now. Please try again." onContinue={noop} onSkip={noop} phase="setup" />,
    );
    expect(errored).toContain('role="alert"');
    expect(errored).toContain('We could not save your setup just now.');
  });
});

describe('Home wiring', () => {
  it('forgets Home\'s cache on a member switch', async () => {
    const { rememberShelf, cachedShelf } = await import('./features/home/homeCache');
    const { URL: NodeURL } = await import('node:url');
    const source = (await import('node:fs')).readFileSync(new NodeURL('./LuminaApp.tsx', import.meta.url), 'utf8');
    // The member-switch effect clears walls, titles, playback warm-up and Home together.
    expect(source).toMatch(/forgetTitles\(\);\s*forgetHomeCache\(\);/);
    rememberShelf('member-1', 'next_up', []);
    expect(cachedShelf('member-1', 'next_up')).toEqual([]);
  });
});

describe('shell error toasts', () => {
  it('toasts the same error text again when it recurs', async () => {
    let fail: (message: string) => void = () => undefined;
    function Probe() {
      const [error, setError] = useState<string | null>(null);
      fail = setError;
      useShellErrorToast(error, () => setError(null));
      return null;
    }
    const { act } = await import('@testing-library/react');
    render(<ToastProvider><Probe /></ToastProvider>);
    act(() => fail('Download failed.'));
    await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(1));
    act(() => fail('Download failed.'));
    await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(2));
  });

  it('toasts a held failure once per failure object and never clears it', async () => {
    let fail: (failure: { message: string }) => void = () => undefined;
    let shown = '';
    function Probe() {
      const [failure, setFailure] = useState<{ message: string } | null>(null);
      fail = setFailure;
      shown = failure?.message ?? '';
      useShellErrorToast(failure?.message ?? null, undefined, failure);
      return null;
    }
    const { act } = await import('@testing-library/react');
    render(<ToastProvider><Probe /></ToastProvider>);
    act(() => fail({ message: 'No preview.' }));
    await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(1));
    expect(shown).toBe('No preview.');
    act(() => fail({ message: 'No preview.' }));
    await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(2));
  });
});
