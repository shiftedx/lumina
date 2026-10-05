import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../api';
import type { UserProfile } from '../../types';
import { ToastProvider } from '../../ui';
import { CommandPalette, type CommandPaletteProps } from './CommandPalette';

const user = { id: 'u1', username: 'alexandria', display_name: 'Alexandria', role: 'admin', is_active: true } as UserProfile;
const match = (id: string, name: string, category = 'movies') => ({ kind: 'title', id, title: name, subtitle: '2024 · Film', score: 1, lexical_score: 1, semantic_score: 0, match_mode: 'lexical', media_title: { id, name, type: 'movie', category, year: 2024 } });
let token = 1;
function props(patch: Partial<CommandPaletteProps> = {}): CommandPaletteProps {
  return {
    open: true, mode: 'search', onClose: vi.fn(), user,
    history: [{ id: 'h1', query: 'alpine', searched_at: '' }, { id: 'h2', query: 'baking', searched_at: '' }] as never,
    channels: [], sessionKey: 'u1', captureSessionToken: () => token, isSessionTokenCurrent: (value) => value === token,
    context: { user, mobile: false, sidebarCollapsed: false, theme: 'system', navigate: vi.fn(), setTheme: vi.fn(), toggleSidebar: vi.fn(), signOut: vi.fn(), openLinkMode: vi.fn() },
    onRemoveHistory: vi.fn(), onClearHistory: vi.fn(), onOpenTitle: vi.fn(), onOpenMoment: vi.fn(), onOpenLibraryItem: vi.fn(), onOpenChannel: vi.fn(), onOpenRemote: vi.fn(), onOpenUrl: vi.fn(), onSearchEverything: vi.fn(),
    ...patch,
  };
}
const input = () => screen.getByRole('combobox');
const active = () => document.getElementById(input().getAttribute('aria-activedescendant') ?? '');

beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); token = 1; });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('CommandPalette', () => {
  it('opens with the input focused, recents and pinned actions, and no request', () => {
    const search = vi.spyOn(api, 'searchLibrary');
    render(<CommandPalette {...props()} />);
    expect(document.activeElement).toBe(input());
    expect(input().getAttribute('placeholder')).toBe('Search titles, channels, videos, settings…');
    const list = screen.getByRole('listbox');
    expect(within(list).getByRole('group', { name: 'Recent searches' })).toBeTruthy();
    expect(within(list).getByRole('group', { name: 'Actions' })).toBeTruthy();
    expect(active()?.textContent).toContain('alpine');
    expect(search).not.toHaveBeenCalled();
  });

  it('debounces 200 ms, groups titles by category and announces the count once', async () => {
    const search = vi.spyOn(api, 'searchLibrary').mockResolvedValue({ query: 'al', mode: 'hybrid', matches: [match('m1', 'Alpha'), match('s1', 'Alpine Show', 'shows')], items: [], index_generation: 1 } as never);
    vi.spyOn(api, 'youtubeSearch').mockResolvedValue({ items: [] } as never);
    render(<CommandPalette {...props()} />);
    await userEvent.type(input(), 'al');
    expect(search).not.toHaveBeenCalled();
    await act(async () => { vi.advanceTimersByTime(200); });
    expect(search).toHaveBeenCalledTimes(1);
    expect(search).toHaveBeenCalledWith('al', 12);
    expect((await screen.findByRole('group', { name: 'Movies' })).textContent).toContain('Alpha');
    expect(screen.getByRole('group', { name: 'Shows' }).textContent).toContain('Alpine Show');
    expect(screen.getByText(/^\d+ results?$/)).toBeTruthy();
  });

  it('drops a response for an older query', async () => {
    let resolveFirst: (value: unknown) => void = () => undefined;
    vi.spyOn(api, 'searchLibrary')
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }) as never)
      .mockResolvedValueOnce({ query: 'alp', mode: 'hybrid', matches: [match('m2', 'Alpine')], items: [], index_generation: 1 } as never);
    vi.spyOn(api, 'youtubeSearch').mockResolvedValue({ items: [] } as never);
    render(<CommandPalette {...props()} />);
    await userEvent.type(input(), 'al');
    await act(async () => { vi.advanceTimersByTime(200); });
    await userEvent.type(input(), 'p');
    await act(async () => { vi.advanceTimersByTime(200); });
    await act(async () => { resolveFirst({ query: 'al', mode: 'hybrid', matches: [match('m1', 'Stale')], items: [], index_generation: 1 }); });
    expect(screen.queryByText('Stale')).toBeNull();
    expect(await screen.findByText('Alpine')).toBeTruthy();
  });

  it('shows library results before a slow YouTube answer, and keeps the active row when it lands', async () => {
    let resolveYouTube: (value: unknown) => void = () => undefined;
    vi.spyOn(api, 'searchLibrary').mockResolvedValue({ query: 'al', mode: 'hybrid', matches: [match('m1', 'Alpha'), match('m2', 'Alder')], items: [], index_generation: 1 } as never);
    vi.spyOn(api, 'youtubeSearch').mockImplementation(() => new Promise((resolve) => { resolveYouTube = resolve; }) as never);
    render(<CommandPalette {...props()} />);
    await userEvent.type(input(), 'al');
    await act(async () => { vi.advanceTimersByTime(200); });
    expect(await screen.findByText('Alder')).toBeTruthy();
    await userEvent.keyboard('{ArrowDown}');
    const before = active()?.textContent;
    expect(before).toContain('Alder');
    await act(async () => { resolveYouTube({ items: [{ id: 'y1', title: 'Alpine drive', webpage_url: 'https://youtube.com/watch?v=y1' }] }); });
    expect(active()?.textContent).toBe(before);
  });

  it('ignores answers after the session changes', async () => {
    let resolveLocal: (value: unknown) => void = () => undefined;
    vi.spyOn(api, 'searchLibrary').mockImplementation(() => new Promise((resolve) => { resolveLocal = resolve; }) as never);
    vi.spyOn(api, 'youtubeSearch').mockResolvedValue({ items: [] } as never);
    render(<CommandPalette {...props()} />);
    await userEvent.type(input(), 'al');
    await act(async () => { vi.advanceTimersByTime(200); });
    token = 2;
    await act(async () => { resolveLocal({ query: 'al', mode: 'hybrid', matches: [match('m1', 'Someone else')], items: [], index_generation: 1 }); });
    expect(screen.queryByText('Someone else')).toBeNull();
  });

  it('Enter opens the active row; ⌘↵ searches everything', async () => {
    vi.spyOn(api, 'searchLibrary').mockResolvedValue({ query: 'al', mode: 'hybrid', matches: [match('m1', 'Alpha')], items: [], index_generation: 1 } as never);
    vi.spyOn(api, 'youtubeSearch').mockResolvedValue({ items: [] } as never);
    const p = props();
    render(<CommandPalette {...p} />);
    await userEvent.type(input(), 'al');
    await act(async () => { vi.advanceTimersByTime(200); });
    await screen.findByText('Alpha');
    while (!active()?.textContent?.includes('Alpha')) await userEvent.keyboard('{ArrowDown}');
    await userEvent.keyboard('{Enter}');
    expect(p.onOpenTitle).toHaveBeenCalledWith(expect.objectContaining({ id: 'm1' }));
    await userEvent.keyboard('{Meta>}{Enter}{/Meta}');
    expect(p.onSearchEverything).toHaveBeenCalledWith('al');
  });

  it('Esc clears, then closes back to the opener', async () => {
    const p = props();
    render(<CommandPalette {...p} />);
    await userEvent.type(input(), 'al');
    await userEvent.keyboard('{Escape}');
    expect((input() as HTMLInputElement).value).toBe('');
    expect(p.onClose).not.toHaveBeenCalled();
    act(() => { input().closest('dialog')!.dispatchEvent(new Event('cancel', { cancelable: true })); });
    expect(p.onClose).toHaveBeenCalledOnce();
  });

  it('Delete removes only a recent search; Clear all clears them', async () => {
    const p = props();
    render(<CommandPalette {...p} />);
    await userEvent.keyboard('{Delete}');
    expect(p.onRemoveHistory).toHaveBeenCalledWith('h1');
    await userEvent.keyboard('{Control>}{End}{/Control}');
    await userEvent.keyboard('{Delete}');
    expect(p.onRemoveHistory).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole('button', { name: 'Remove “baking” from recent searches' }));
    expect(p.onRemoveHistory).toHaveBeenCalledWith('h2');
    await userEvent.click(screen.getByRole('button', { name: 'Clear all' }));
    expect(p.onClearHistory).toHaveBeenCalledOnce();
  });

  it('a URL in search mode offers Open link and never searches', async () => {
    const search = vi.spyOn(api, 'searchLibrary');
    const p = props();
    render(<CommandPalette {...p} />);
    await userEvent.type(input(), 'https://youtu.be/abc');
    await act(async () => { vi.advanceTimersByTime(400); });
    expect(search).not.toHaveBeenCalled();
    expect(active()?.textContent).toContain('Open link');
    await userEvent.keyboard('{Enter}');
    expect(p.onOpenUrl).toHaveBeenCalledWith('https://youtu.be/abc');
  });

  it('an empty palette shows no spinner and keeps its title out of the input row', () => {
    for (const mode of ['link', 'search'] as const) {
      const { unmount } = render(<CommandPalette {...props({ mode })} />);
      expect(document.querySelector('.g-palette .g-spinner')).toBeNull();
      expect(document.querySelector('.g-palette .g-dialog-title')?.classList.contains('sr-only')).toBe(true);
      unmount();
    }
  });

  it('link mode rejects text that is not a link', async () => {
    const p = props({ mode: 'link' });
    render(<CommandPalette {...p} />);
    expect(screen.getByRole('dialog', { name: 'Add a link' })).toBeTruthy();
    expect(input().getAttribute('inputmode')).toBe('url');
    await userEvent.type(input(), 'hello{Enter}');
    expect(screen.getByRole('alert').textContent).toContain("That doesn't look like a link.");
    expect(p.onOpenUrl).not.toHaveBeenCalled();
  });

  it('shows the degraded notices, the empty state and the error state', async () => {
    vi.spyOn(api, 'searchLibrary').mockResolvedValueOnce({ query: 'zz', mode: 'lexical', matches: [], items: [], index_generation: 1 } as never).mockRejectedValueOnce(new Error('down'));
    vi.spyOn(api, 'youtubeSearch').mockRejectedValue(new Error('quota'));
    render(<CommandPalette {...props()} />);
    await userEvent.type(input(), 'zz');
    await act(async () => { vi.advanceTimersByTime(200); });
    expect(await screen.findByText('Nothing found for “zz”.')).toBeTruthy();
    expect(screen.getByText('YouTube suggestions are taking a break. Vault and channel matches are still available.')).toBeTruthy();
    expect(screen.getByText('Meaning-based matching is unavailable. Showing fast title, creator, and tag matches.')).toBeTruthy();
    await userEvent.type(input(), 'z');
    await act(async () => { vi.advanceTimersByTime(200); });
    expect(await screen.findByText('Search is unavailable right now.')).toBeTruthy();
  });

  it('renders result text as text', async () => {
    vi.spyOn(api, 'searchLibrary').mockResolvedValue({ query: 'al', mode: 'hybrid', matches: [match('m1', '<img src=x onerror=alert(1)>')], items: [], index_generation: 1 } as never);
    vi.spyOn(api, 'youtubeSearch').mockResolvedValue({ items: [] } as never);
    const { container } = render(<CommandPalette {...props()} />);
    await userEvent.type(input(), 'al');
    await act(async () => { vi.advanceTimersByTime(200); });
    expect(await screen.findByText('<img src=x onerror=alert(1)>')).toBeTruthy();
    expect(container.ownerDocument.querySelector('img[src="x"]')).toBeNull();
  });
  it('shows nothing of the previous member after a session change, and starts clean on reopen', async () => {
    vi.spyOn(api, 'searchLibrary').mockResolvedValue({ query: 'al', mode: 'hybrid', matches: [match('m1', 'Alpha')], items: [], index_generation: 1 } as never);
    vi.spyOn(api, 'youtubeSearch').mockResolvedValue({ items: [] } as never);
    const { rerender } = render(<CommandPalette {...props()} />);
    await userEvent.type(input(), 'al');
    await act(async () => { vi.advanceTimersByTime(200); });
    await screen.findByText('Alpha');
    // Another member signs in on the same open palette: the old answers, query and highlight are gone at once.
    rerender(<CommandPalette {...props({ sessionKey: 'u2' })} />);
    expect((input() as HTMLInputElement).value).toBe('');
    expect(screen.queryByText('Alpha')).toBeNull();
    // Closing and opening again also starts empty.
    await userEvent.type(input(), 'x');
    rerender(<CommandPalette {...props({ sessionKey: 'u2', open: false })} />);
    rerender(<CommandPalette {...props({ sessionKey: 'u2' })} />);
    expect((input() as HTMLInputElement).value).toBe('');
  });

  it('Enter during IME composition does not open the active row', () => {
    const p = props();
    render(<CommandPalette {...p} />);
    fireEvent.keyDown(input(), { key: 'Enter', isComposing: true });
    expect((input() as HTMLInputElement).value).toBe(''); // activating the recent would have filled in 'alpine'
  });

  it('names the Ctrl key in the footer off Apple platforms', () => {
    vi.spyOn(window.navigator, 'platform', 'get').mockReturnValue('Win32');
    render(<CommandPalette {...props()} />);
    expect(document.querySelector('.g-palette-footer')?.textContent).toContain('Ctrl↵ to open in Explore');
  });

  it('gives a title row its poster slot in front of the text', async () => {
    vi.spyOn(api, 'searchLibrary').mockResolvedValue({ query: 'al', mode: 'hybrid', matches: [match('m1', 'Alpha')], items: [], index_generation: 1 } as never);
    vi.spyOn(api, 'youtubeSearch').mockResolvedValue({ items: [] } as never);
    render(<CommandPalette {...props()} />);
    await userEvent.type(input(), 'al');
    await act(async () => { vi.advanceTimersByTime(200); });
    const row = (await screen.findByText('Alpha')).closest('[role="option"]')!;
    expect(row.querySelector('.g-palette-art.is-poster .g-art')).not.toBeNull();
  });
});

describe('scan actions', () => {
  const result = { started: [{ root_id: 'r1', run_id: 'x' }], queued: ['r2'], skipped: [] };
  it('lists scan rows for an admin, runs one and toasts the summary', async () => {
    const roots = vi.spyOn(api, 'getLibraryAutomation').mockResolvedValue({ roots: [{ root_id: 'r1', label: 'TV', state: 'idle' }, { root_id: 'r2', label: 'Movies', state: 'idle' }] } as never);
    const scan = vi.spyOn(api, 'scanLibraries').mockResolvedValue(result as never);
    const onClose = vi.fn();
    render(<ToastProvider><CommandPalette {...props({ onClose })} /></ToastProvider>);
    await act(async () => { await Promise.resolve(); });
    expect(roots).toHaveBeenCalledTimes(1);
    expect(within(screen.getByRole('listbox')).queryByText('Scan TV now')).toBeNull();
    await userEvent.type(input(), 'scan');
    expect(screen.getByText('Scan libraries now')).toBeTruthy();
    expect(screen.getByText('Scan TV now')).toBeTruthy();
    await userEvent.click(screen.getByText('Scan TV now'));
    expect(scan).toHaveBeenCalledWith('r1');
    expect(onClose).toHaveBeenCalled();
    await act(async () => { await Promise.resolve(); });
    expect(document.body.textContent).toContain('Scanning 2 libraries');
  });

  it('Scan libraries now sends no root; a failed request toasts an error', async () => {
    vi.spyOn(api, 'getLibraryAutomation').mockResolvedValue({ roots: [] } as never);
    const scan = vi.spyOn(api, 'scanLibraries').mockRejectedValue(new Error('boom'));
    render(<ToastProvider><CommandPalette {...props()} /></ToastProvider>);
    await userEvent.type(input(), 'scan');
    await userEvent.click(screen.getByText('Scan libraries now'));
    expect(scan).toHaveBeenCalledWith(undefined);
    await act(async () => { await Promise.resolve(); });
    expect(document.body.textContent).toContain("Couldn't start the scan");
  });

  it('does not load roots or offer scans to a member, and ignores a roots failure', async () => {
    const roots = vi.spyOn(api, 'getLibraryAutomation').mockRejectedValue(new Error('403'));
    const member = { ...user, role: 'viewer' } as UserProfile;
    const { unmount } = render(<ToastProvider><CommandPalette {...props({ user: member, context: { ...props().context, user: member } })} /></ToastProvider>);
    await userEvent.type(input(), 'scan');
    expect(roots).not.toHaveBeenCalled();
    expect(screen.queryByText('Scan libraries now')).toBeNull();
    unmount();
    render(<ToastProvider><CommandPalette {...props()} /></ToastProvider>);
    await userEvent.type(input(), 'scan');
    expect(screen.getByText('Scan libraries now')).toBeTruthy();
  });
});
