import { describe, expect, it, vi } from 'vitest';
const {
  act, fireEvent, screen, waitFor, within, userEvent, useReducer, useState, useToast, render, mocks, user, libraryItem, musicItem, upNextSnapshot,
  openNavigationMenu, navigateWithMenu, workspaceState, cancelDialog, openPalette, openLinkPalette, setMobile, setDrawerOnly, paletteChunk,
  ApiRequestError, keepaliveWritesSettled, updateRemotePlaybackProgress, createInitialWorkspaceState, workspaceReducer,
  LuminaApp, ChannelsSurface, followOutcome, useUnsavedChanges, watchSurface, cancelSpeculativeStart,
  claimSpeculativeStart, forgetPlaybackWarmup, takePlayIntent, artistSummary, movieSummary, titleDetail, titlePage,
  docFixture, forgetTitles, forgetWallStores, capabilities, CHANNEL_ID, channelPage, remoteEntry, recoEntry,
  openAppPath, openMemberPicker, resetDeviceRing, recordRecoOpen, clearAlbumQueue, chooseSelect,
} = (await import('./LuminaKeyboard.fixture')).fixture;
import type { LibraryItem, MemberInterests, SourceAutomation, UserProfile, UserTag, YouTubeSearchResult, AcquisitionFormat } from './LuminaKeyboard.fixture';

it('a failed search-history write never blocks the search or adds a recent item', async () => {
  const browser = userEvent.setup();
  mocks.recordSearchHistory.mockRejectedValue(new Error('history unavailable'));
  render(<LuminaApp />);

  await browser.type(await openPalette(browser), 'film noir');
  await browser.keyboard('{Meta>}{Enter}{/Meta}');

  expect(await screen.findByRole('heading', { name: 'Results for “film noir”' })).toBeTruthy();
  expect(await screen.findByRole('button', { name: /^Film in the vault/ })).toBeTruthy();
  await waitFor(() => expect(mocks.recordSearchHistory).toHaveBeenCalledWith('film noir'));
  await navigateWithMenu(browser, 'Home');
  await openPalette(browser);
  expect(screen.queryByRole('group', { name: 'Recent searches' })).toBeNull();
});
it('a late Explore response never replaces the newer query', async () => {
  const browser = userEvent.setup();
  let finishSlow!: (value: { items: YouTubeSearchResult[]; errors: [] }) => void;
  const stale: YouTubeSearchResult = { id: 'stale', title: 'A stale result that must never land', uploader: 'Old channel', source: 'youtube', kind: 'video', webpage_url: 'https://example.test/stale' };
  const fresh: YouTubeSearchResult = { id: 'fresh', title: 'Fresh harbour result', uploader: 'Current channel', source: 'youtube', kind: 'video', webpage_url: 'https://example.test/fresh' };
  mocks.sourceSearch
    .mockReturnValueOnce(new Promise((resolve) => { finishSlow = resolve; }))
    .mockResolvedValueOnce({ items: [fresh], errors: [] });
  mocks.searchLibrary.mockResolvedValue({ items: [] });
  render(<LuminaApp />);

  await browser.type(await openPalette(browser), 'slow');
  await browser.keyboard('{Meta>}{Enter}{/Meta}');
  await navigateWithMenu(browser, 'Home');
  await browser.type(await openPalette(browser), 'harbour');
  await browser.keyboard('{Meta>}{Enter}{/Meta}');

  expect(await screen.findByRole('heading', { name: 'Results for “harbour”' })).toBeTruthy();
  expect(await screen.findByRole('button', { name: /^Fresh harbour result/ })).toBeTruthy();
  await act(async () => { finishSlow({ items: [stale], errors: [] }); });
  expect(screen.getByRole('heading', { name: 'Results for “harbour”' })).toBeTruthy();
  expect(screen.getByRole('button', { name: /^Fresh harbour result/ })).toBeTruthy();
  expect(screen.queryByRole('button', { name: /^A stale result that must never land/ })).toBeNull();
});

describe('keyboard navigation in the authenticated shell', () => {
  it('opens phone anchored navigation, restores its trigger, and focuses the selected destination', async () => {
    setMobile(true);
    const keyboard = userEvent.setup();
    const { container } = render(<LuminaApp />);
    const trigger = screen.getByRole('button', { name: 'Open navigation' });
    trigger.focus();
    await keyboard.keyboard('{Enter}');
    const dialog = screen.getByRole('dialog', { name: 'Navigation' });
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Home' }));
    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(screen.queryByRole('dialog', { name: 'Navigation' })).toBeNull();
    await keyboard.click(trigger);
    await keyboard.click(within(screen.getByRole('dialog', { name: 'Navigation' })).getByRole('button', { name: 'Movies' }));
    const heading = await screen.findByRole('heading', { level: 1, name: 'Movies' });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(screen.queryByRole('dialog', { name: 'Navigation' })).toBeNull();
    expect(container.querySelector('.g-mobile-tabs button:nth-child(2)')?.getAttribute('aria-current')).toBe('page');
  });

  it('marks desktop navigation current and moves focus to the changed surface', async () => {
    const keyboard = userEvent.setup();
    render(<LuminaApp />);

    await openNavigationMenu(keyboard);
    const explore = within(screen.getByRole('navigation', { name: 'Primary' })).getByRole('button', { name: 'Streaming' });
    explore.focus();
    await keyboard.keyboard('{Enter}');

    const heading = await screen.findByRole('heading', { level: 1, name: 'Streaming' });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    await openNavigationMenu(keyboard);
    expect(within(screen.getByRole('navigation', { name: 'Primary' })).getByRole('button', { name: 'Streaming' }).getAttribute('aria-current')).toBe('page');
    expect(document.querySelectorAll('main')).toHaveLength(1);
  });

  it('opens desktop navigation on Enter and focuses its first destination', async () => {
    const keyboard = userEvent.setup();
    render(<LuminaApp />);

    const trigger = document.querySelector('.g-topbar-start button') as HTMLButtonElement;
    trigger.focus();
    await keyboard.keyboard('{Enter}');
    expect(document.activeElement).toBe(within(screen.getByRole('navigation', { name: 'Primary' })).getByRole('button', { name: 'Home' }));
  });
});

describe('keyboard-safe interactive overlays', () => {
  it('inspects an owned channel match by its saved source instead of searching the live catalog by label', async () => {
    const browser = userEvent.setup();
    const sourceUrl = 'https://www.youtube.com/@mars-astronomy';
    workspaceState.sourceAutomations = [{
      id: 'channel-1', user_id: user.id, label: 'Mars Astronomy', source_url: sourceUrl, source_type: 'channel', cron_expression: '0 */6 * * *', active: true, auto_download: false,
      format_selection: {}, output_profile: {}, rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video' },
      duplicate_policy: 'skip_same_source', last_run_summary: {}, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    } satisfies SourceAutomation];
    render(<LuminaApp />);

    await browser.type(await openPalette(browser), 'mars');
    const channel = await screen.findByRole('option', { name: /Mars Astronomy/ });
    await browser.click(channel);

    await waitFor(() => expect(mocks.previewUrl).toHaveBeenCalledWith(expect.objectContaining({ source_url: sourceUrl }), { signal: expect.any(AbortSignal) }));
    expect(mocks.youtubeSearch).not.toHaveBeenCalledWith(expect.objectContaining({ query: 'Mars Astronomy' }));
  });

  it('uses a roving radio group in the watch download menu and restores its trigger on Escape', async () => {
    const keyboard = userEvent.setup();
    const remote: YouTubeSearchResult = { id: 'video-1', title: 'Remote film', uploader: 'Film Maker', webpage_url: 'https://example.test/video-1' };

    function WatchHarness() {
      const [quality, setQuality] = useState<AcquisitionFormat>('best');
      const [open, setOpen] = useState(false);
      return watchSurface({
        downloadMenuOpen: open,
        downloadQuality: quality,
        onCloseDownloadMenu: () => setOpen(false),
        onDownloadQuality: setQuality,
        onToggleDownloadMenu: () => setOpen((value) => !value),
        selection: { kind: 'remote', item: remote, preview: null },
      });
    }

    render(<WatchHarness />);
    const trigger = screen.getByRole('button', { name: 'Download options' });
    trigger.focus();
    await keyboard.keyboard('{Enter}');
    const firstPreset = await screen.findByRole('radio', { name: /Best available/ });
    await waitFor(() => expect(document.activeElement).toBe(firstPreset));

    await keyboard.keyboard('{ArrowRight}');
    const nextPreset = screen.getByRole('radio', { name: /1080p/ });
    await waitFor(() => expect(document.activeElement).toBe(nextPreset));
    expect(nextPreset.getAttribute('aria-checked')).toBe('true');
    expect(firstPreset.tabIndex).toBe(-1);

    await keyboard.keyboard('{Escape}');
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(screen.queryByRole('radiogroup', { name: 'Download quality' })).toBeNull();
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
  });
});

describe('palette openers', () => {
  it('⌘K and Ctrl K open the palette with the input focused', async () => {
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1 });
    await userEvent.keyboard('{Meta>}k{/Meta}');
    expect(document.activeElement).toBe(await screen.findByRole('combobox'));
    cancelDialog();
    await waitFor(() => expect(screen.queryByRole('combobox')).toBeNull());
    await userEvent.keyboard('{Control>}k{/Control}');
    expect(document.activeElement).toBe(await screen.findByRole('combobox'));
  });

  it('Escape while the palette chunk is still loading cancels the open', async () => {
    await import('./features/palette/CommandPalette'); // warm, so the release mounts the palette at once
    let release = () => undefined as void;
    paletteChunk.gate = new Promise<void>((resolve) => { release = resolve; });
    try {
      render(<LuminaApp />);
      await screen.findByRole('heading', { level: 1 });
      await userEvent.click(screen.getByRole('button', { name: /^Search Lumina, / }));
      await userEvent.keyboard('{Escape}');
      paletteChunk.gate = null;
      release();
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 1000)); });
      expect(screen.queryByRole('combobox')).toBeNull();
    } finally { paletteChunk.gate = null; release(); }
  });

  it('slash opens the palette only outside text fields', async () => {
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1 });
    await userEvent.keyboard('/');
    expect(document.activeElement).toBe(await screen.findByRole('combobox'));
    cancelDialog();
    await waitFor(() => expect(screen.queryByRole('combobox')).toBeNull());
    const field = document.createElement('input');
    document.body.append(field);
    field.focus();
    await userEvent.keyboard('/');
    expect(field.value).toBe('/');
    expect(screen.queryByRole('combobox')).toBeNull();
    field.remove();
  });

  it('the Add link button opens link mode and the Search trigger opens search', async () => {
    const browser = userEvent.setup();
    render(<LuminaApp />);
    await browser.click(await screen.findByRole('button', { name: 'Add a link' }));
    expect(await screen.findByRole('dialog', { name: 'Add a link' })).toBeTruthy();
    cancelDialog();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect((await openPalette(browser)).getAttribute('placeholder')).toBe('Search titles, channels, videos, settings…');
  });

  it('closing the palette forgets its query, so the next opening starts clean', async () => {
    const browser = userEvent.setup();
    render(<LuminaApp />);
    await browser.type(await openPalette(browser), 'alp');
    cancelDialog();
    await waitFor(() => expect(screen.queryByRole('combobox')).toBeNull());
    expect((await openPalette(browser) as HTMLInputElement).value).toBe('');
  });

  it('has no fixed banner and no combobox outside the palette', async () => {
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1 });
    expect(document.querySelector('.g-toast')).toBeNull(); // the live toast row; the retired .app-toast could never match
    expect(screen.queryByRole('combobox')).toBeNull();
  });
});

describe('drawer breakpoint', () => {
  it('the header menu opens anchored navigation on tablet widths', async () => {
    setDrawerOnly(true);
    render(<LuminaApp />);
    await userEvent.click(await screen.findByRole('button', { name: 'Open navigation' }));
    expect(await screen.findByRole('dialog', { name: 'Navigation' })).toBeTruthy();
  });
});

describe('shell toasts and openers', () => {
  let toaster: ReturnType<typeof useToast>;
  const Grab = () => { toaster = useToast(); return null; };

  it('sign-out clears the leaving member\'s toasts and their Undo', async () => {
    const browser = userEvent.setup();
    const undo = vi.fn();
    mocks.logoutSession.mockResolvedValue(undefined);
    window.history.replaceState(null, '', '/settings/account');
    render(<><Grab /><LuminaApp /></>);
    act(() => { toaster({ tone: 'info', message: 'Hidden for A.', action: { label: 'Undo', onAction: undo } }); });
    expect(screen.getByRole('button', { name: 'Undo' })).toBeTruthy();
    await browser.click(await screen.findByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(screen.queryByText('Hidden for A.')).toBeNull());
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
    expect(undo).not.toHaveBeenCalled();
  });

  it('the offline toast goes away when the connection returns', async () => {
    const view = render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1 });
    const workspace = mocks.useAuthenticatedWorkspace.mock.results[0].value;
    mocks.useAuthenticatedWorkspace.mockReturnValue({ ...workspace, state: { ...workspaceState, jobsState: 'offline' } });
    view.rerender(<LuminaApp />);
    await screen.findByText("Lumina can't reach the server. Trying again…");
    mocks.useAuthenticatedWorkspace.mockReturnValue({ ...workspace, state: { ...workspaceState, jobsState: 'ready' } });
    view.rerender(<LuminaApp />);
    await waitFor(() => expect(screen.queryByText("Lumina can't reach the server. Trying again…")).toBeNull());
  });

  it('slash and Ctrl K do nothing while signed out or while a dialog is open', async () => {
    const base = mocks.useAuthenticatedWorkspace();
    mocks.useAuthenticatedWorkspace.mockReturnValue({ ...base, state: { ...workspaceState, authStage: 'login', currentUser: null } });
    const out = render(<LuminaApp />);
    const down = (init: KeyboardEventInit) => { const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init }); document.body.dispatchEvent(event); return event; };
    expect(down({ key: '/' }).defaultPrevented).toBe(false);
    expect(down({ key: 'k', ctrlKey: true }).defaultPrevented).toBe(false);
    out.unmount();
    mocks.useAuthenticatedWorkspace.mockReturnValue({ ...base, state: workspaceState });
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1 });
    const dialog = document.createElement('dialog');
    dialog.setAttribute('open', '');
    document.body.append(dialog);
    expect(down({ key: '/' }).defaultPrevented).toBe(false);
    expect(down({ key: 'k', metaKey: true }).defaultPrevented).toBe(false);
    dialog.remove();
    expect(down({ key: '/' }).defaultPrevented).toBe(true);
  });
});
