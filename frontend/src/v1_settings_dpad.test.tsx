import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsSurface } from './features/settings/SettingsSurface';

beforeEach(() => vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false })));
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const viewer = { id: 'u1', username: 'dana', display_name: 'Dana', role: 'viewer', is_active: true } as never;
const at = (element: HTMLElement, top: number) => Object.defineProperty(element, 'getBoundingClientRect', {
  configurable: true, value: () => ({ left: 0, top, width: 200, height: 48, right: 200, bottom: top + 48, x: 0, y: top, toJSON: () => ({}) }),
});
function shell() {
  const props = { user: viewer, formatPreset: 'best', onFormatChange: vi.fn(), onLogout: vi.fn(), autoplayUpNext: true, onAutoplayUpNextChange: vi.fn(), playbackMaxHeight: null, onPlaybackMaxHeightChange: vi.fn(), playbackPrefs: { normalizeLoudness: true, autoSkip: { intro: false, credits: false, recap: false }, profanity: { enabled: false, words: [] } }, onPlaybackPrefsChange: vi.fn(), section: 'playback' as const };
  render(<SettingsSurface {...props} />);
  return props;
}

describe('Settings on a D-pad', () => {
  it('moves from persistent search to category links and between them', () => {
    shell();
    const search = screen.getByRole('searchbox', { name: 'Search settings' });
    const links = within(screen.getByRole('navigation', { name: 'Settings sections' })).getAllByRole('link');
    at(search, 0); links.forEach((link, index) => at(link, 60 + index * 50));
    search.focus(); fireEvent.keyDown(search, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(links[0]);
    fireEvent.keyDown(links[0], { key: 'ArrowDown' }); expect(document.activeElement).toBe(links[1]);
    fireEvent.keyDown(links[1], { key: 'ArrowUp' }); expect(document.activeElement).toBe(links[0]);
  });

  it('crosses into the current subpage, along subpages, then back to the category', () => {
    shell();
    const playback = screen.getByRole('link', { name: 'Playback' });
    playback.focus(); fireEvent.keyDown(playback, { key: 'ArrowRight' });
    const watching = screen.getByRole('link', { name: 'Watching' });
    expect(document.activeElement).toBe(watching);
    fireEvent.keyDown(watching, { key: 'ArrowRight' });
    const subtitles = screen.getByRole('link', { name: 'Subtitles' });
    expect(document.activeElement).toBe(subtitles);
    fireEvent.keyDown(subtitles, { key: 'ArrowLeft' }); expect(document.activeElement).toBe(watching);
    fireEvent.keyDown(watching, { key: 'ArrowLeft' }); expect(document.activeElement).toBe(playback);
  });

  it('moves within a row and OK toggles the switch', () => {
    const props = shell();
    const info = screen.getByRole('button', { name: 'About Autoplay next video' });
    info.focus(); fireEvent.keyDown(info, { key: 'ArrowRight' });
    const autoplay = screen.getByRole('switch', { name: 'Autoplay next video' });
    expect(document.activeElement).toBe(autoplay);
    fireEvent.keyDown(autoplay, { key: 'Enter' }); expect(props.onAutoplayUpNextChange).toHaveBeenCalledWith(false);
    fireEvent.keyDown(autoplay, { key: 'ArrowLeft' }); expect(document.activeElement).toBe(info);
  });

  it('leaves a closed choice by row while an open choice owns its option arrows', () => {
    shell();
    const quality = screen.getByRole('combobox', { name: 'Maximum quality' });
    quality.focus(); fireEvent.keyDown(quality, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByRole('switch', { name: 'Even out loudness' }));
    expect(screen.queryByRole('listbox')).toBeNull();

    quality.focus(); fireEvent.keyDown(quality, { key: 'Enter' });
    expect(screen.getByRole('listbox')).not.toBeNull();
    fireEvent.keyDown(quality, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(quality);
    expect(screen.getByRole('listbox')).not.toBeNull();
  });
});


it('moves between fields inside a block form before moving to the next settings row', () => {
  render(<SettingsSurface user={viewer} section="account" page="security" formatPreset="best" onFormatChange={vi.fn()} onLogout={vi.fn()} />);
  const current = screen.getByLabelText('Current password');
  const next = screen.getByLabelText('New password');
  document.querySelectorAll<HTMLElement>('.g-settings-pane button, .g-settings-pane input, .g-settings-page-link').forEach((element, index) => at(element, 200 + index * 50));
  at(current, 50); at(next, 110);
  current.focus(); fireEvent.keyDown(current, { key: 'ArrowDown' });
  expect(document.activeElement).toBe(next);
  fireEvent.keyDown(next, { key: 'ArrowUp' });
  expect(document.activeElement).toBe(current);
});
