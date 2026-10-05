import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), listConnectedApps: vi.fn().mockResolvedValue([]) }));
const { SettingsSurface } = await import('./features/settings/SettingsSurface');

afterEach(() => vi.restoreAllMocks());
const viewer = { id: 'u1', username: 'dana', display_name: 'Dana', role: 'viewer', is_active: true } as never;
const at = (element: HTMLElement, top: number) => Object.defineProperty(element, 'getBoundingClientRect', {
  configurable: true, value: () => ({ left: 0, top, width: 200, height: 48, right: 200, bottom: top + 48, x: 0, y: top, toJSON: () => ({}) }),
});

function shell(section: 'playback' | null = 'playback') {
  const props = { user: viewer, formatPreset: 'best', onFormatChange: vi.fn(), onLogout: vi.fn(), autoplayUpNext: true, onAutoplayUpNextChange: vi.fn(), section };
  render(<SettingsSurface {...props} />);
  return props;
}

describe('Settings on a D-pad', () => {
  it('moves between sidebar links with Up/Down, from the search box too', () => {
    shell();
    const search = screen.getByRole('searchbox', { name: 'Search settings' });
    const links = screen.getAllByRole('link');
    at(search, 0);
    links.forEach((link, index) => at(link, 60 + index * 50));
    search.focus();
    fireEvent.keyDown(search, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(links[0]);
    fireEvent.keyDown(links[0], { key: 'ArrowDown' });
    expect(document.activeElement).toBe(links[1]);
    fireEvent.keyDown(links[1], { key: 'ArrowUp' });
    expect(document.activeElement).toBe(links[0]);
  });

  it('crosses from the sidebar into a row and back to the current section', () => {
    shell();
    const playback = screen.getByRole('link', { name: 'Playback' });
    playback.focus();
    fireEvent.keyDown(playback, { key: 'ArrowRight' });
    const info = screen.getByRole('button', { name: 'About Autoplay next video' });
    expect(document.activeElement).toBe(info);
    fireEvent.keyDown(info, { key: 'ArrowRight' });
    const autoplay = screen.getByRole('switch', { name: 'Autoplay next video' });
    expect(document.activeElement).toBe(autoplay);
    fireEvent.keyDown(autoplay, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(info);
    fireEvent.keyDown(info, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(playback);
  });

  it('OK (Enter) toggles a checkbox, which natively only toggles on Space', () => {
    const props = shell();
    const autoplay = screen.getByRole('switch', { name: 'Autoplay next video' });
    autoplay.focus();
    fireEvent.keyDown(autoplay, { key: 'Enter' });
    expect(props.onAutoplayUpNextChange).toHaveBeenCalledWith(false);
  });

  it('walks the Settings home with the D-pad: into the first card, along a group, and back to the sidebar', () => {
    shell(null);
    const nav = screen.getByRole('navigation', { name: 'Settings sections' });
    const cards = within(screen.getByRole('region', { name: 'All settings' })).getAllByRole('link');
    const first = within(nav).getAllByRole('link')[0];
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(cards[0]);
    fireEvent.keyDown(cards[0], { key: 'ArrowRight' });
    expect(document.activeElement).toBe(cards[1]);
    fireEvent.keyDown(cards[1], { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(cards[0]);
    fireEvent.keyDown(cards[0], { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(first);
  });
});
