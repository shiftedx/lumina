import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { parseRoute, routePath } from './app/routes';

vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), listConnectedApps: vi.fn().mockResolvedValue([]) }));
const { YOU_SECTIONS } = await import('./features/settings/sections/you');
const { SectionRows } = await import('./features/settings/SettingRow');
const { SettingsHostContext } = await import('./features/settings/settingsHost');
const { sectionById } = await import('./features/settings/registry');
const { MUTE_COPY } = await import('./features/settings/youCards');
const { SourceSettings } = await import('./SourceSettings');

const user = { id: 'u1', username: 'one', display_name: 'One', role: 'viewer', is_active: true } as never;
const prefs = { normalizeLoudness: false, autoSkip: { intro: false, credits: false, recap: false }, profanity: { enabled: false, words: [] } };

function hostProps() {
  return {
    user, formatPreset: 'best', onFormatChange: vi.fn(), onLogout: vi.fn(), theme: 'system' as const, onThemeChange: vi.fn(),
    autoplayUpNext: true, onAutoplayUpNextChange: vi.fn(), onPlaybackMaxHeightChange: vi.fn(), playbackPrefs: prefs, onPlaybackPrefsChange: vi.fn(),
    interests: { categories: [{ key: 'music', label: 'Music' }], selected_keys: [] }, onSaveInterests: vi.fn(),
    suppressions: { items: [], channels: [] }, onRestoreSuppression: vi.fn(), searchHistory: [], onClearSearchHistory: vi.fn(),
    acquisitionDefaults: <SourceSettings onChange={vi.fn()} value={{ formatPreset: 'best', outputContainer: 'mp4', downloadSubtitles: false, outputFolder: '' }} />,
  };
}

function renderAll() {
  const props = hostProps();
  // Rows render straight from the registry, independent of the shell around them.
  render(<SettingsHostContext.Provider value={props}>{YOU_SECTIONS.map((section) => <SectionRows key={section.id} section={section} />)}</SettingsHostContext.Provider>);
  return props;
}

describe('"You" sections', () => {
  it('lists the nine You sections in spec order', () => {
    expect(YOU_SECTIONS.map((section) => section.label)).toEqual(['Profile', 'Display', 'Home & discovery', 'Streaming', 'Playback', 'Downloads', 'Connected apps', 'Privacy & data', 'About']);
  });

  it('renders every You row with its ⓘ', () => {
    renderAll();
    for (const section of YOU_SECTIONS) {
      for (const entry of section.entries) {
        const row = document.querySelector<HTMLElement>(`[data-setting-id="${entry.id}"]`);
        expect(row, entry.id).not.toBeNull();
        expect(within(row as HTMLElement).getByRole('button', { name: `About ${entry.label}` })).toBeTruthy();
      }
    }
  });

  it('still saves personal preferences immediately', async () => {
    const browser = userEvent.setup();
    const props = renderAll();
    await browser.click(screen.getByRole('switch', { name: 'Autoplay next video' }));
    expect(props.onAutoplayUpNextChange).toHaveBeenCalledWith(false);
    await browser.click(screen.getByRole('radio', { name: 'Dark' }));
    expect(props.onThemeChange).toHaveBeenCalledWith('dark');
    await browser.click(screen.getByRole('switch', { name: 'Skip credits' }));
    expect(props.onPlaybackPrefsChange).toHaveBeenLastCalledWith({ autoSkip: { intro: false, credits: true, recap: false } });
    await browser.selectOptions(screen.getByRole('combobox', { name: 'Maximum quality' }), '720');
    expect(props.onPlaybackMaxHeightChange).toHaveBeenLastCalledWith(720);
  });

  it('moves explanations into ⓘ and shows every download choice without an extra click', () => {
    renderAll();
    const mute = document.querySelector('[data-setting-id="playback.mute"]') as HTMLElement;
    const button = within(mute).getByRole('button', { name: 'About Mute strong language' });
    expect(document.getElementById(button.getAttribute('aria-controls') ?? '')?.textContent).toBe(MUTE_COPY);
    expect(document.querySelector('details')).toBeNull();
    expect(screen.getByRole('combobox', { name: 'Container' })).toBeTruthy();
  });
});

/** One You section's rows in the host context; renderAll renders every section at once. */
function renderSection(id: string, patch: Record<string, unknown> = {}) {
  const props = { ...hostProps(), ...patch } as ReturnType<typeof hostProps> & Record<string, ReturnType<typeof vi.fn>>;
  render(<SettingsHostContext.Provider value={props as never}><SectionRows section={YOU_SECTIONS.find((section) => section.id === id)!} /></SettingsHostContext.Provider>);
  return props;
}

describe('You sections on the primitives (app polish 9.1, 10.2, 12.1)', () => {
  it('booleans are switches', () => {
    renderSection('playback');
    expect(document.querySelector('.settings-toggle')).toBeNull();
    expect(screen.getAllByRole('switch').length).toBeGreaterThan(0);
  });

  it('Theme is a segmented control', () => {
    renderSection('appearance');
    expect(screen.getByRole('group', { name: 'Theme' })).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'System' })).toBeTruthy();
    expect(document.querySelector('.theme-choice')).toBeNull();
  });

  it('mounts the caption rows with the iPhone note when the host passes captions', async () => {
    const onCaptionsChange = vi.fn();
    renderSection('playback', { captions: { size: 'medium', background: 'shadow' }, onCaptionsChange });
    expect(screen.getByRole('heading', { level: 3, name: 'Captions' })).toBeTruthy();
    expect(screen.getByText("On iPhone and iPad, full-screen captions follow the system's caption style.")).toBeTruthy();
    await userEvent.click(screen.getByRole('radio', { name: 'Large' }));
    expect(onCaptionsChange).toHaveBeenCalledWith({ size: 'large', background: 'shadow' });
  });

  it('secret fields are password inputs', () => {
    renderSection('account');
    const secrets = document.querySelectorAll('input[autocomplete$="password"]');
    expect(secrets.length).toBeGreaterThan(0);
    for (const field of secrets) expect(field.getAttribute('type')).toBe('password');
  });
});

describe('Home & discovery on the primitives', () => {
  const entry = (scope: string, id: string, extra: Record<string, unknown>) => ({ id, scope, ...extra });
  const suppressions = {
    items: [entry('item', 'item-1', { title: 'An Old Clip', channel_name: 'Creator' })],
    titles: [entry('title', 'title-1', { title: 'A Film' })],
    fewer: [entry('fewer', 'fewer-1', { channel_name: 'Quiet Channel', recovers_at: '2027-02-07T10:00:00Z' })],
    channels: [entry('channel', 'chan-1', { channel_name: 'Muted Channel' })],
  };

  it('is deep-linkable at /settings/discovery as "Home & discovery"', () => {
    expect(routePath({ surface: 'settings', section: 'discovery' })).toBe('/settings/discovery');
    expect(parseRoute('/settings/discovery', '')).toEqual({ surface: 'settings', section: 'discovery' });
    expect(sectionById('discovery')).toMatchObject({ label: 'Home & discovery', group: 'you' });
  });

  it('shows its three rows with level-3 headings and the four lists with Restore', async () => {
    const props = renderSection('discovery', { suppressions });
    for (const name of ['Your interests', 'Hidden recommendations', 'Recommendation history']) expect(screen.getByRole('heading', { level: 3, name })).toBeTruthy();
    for (const name of ['Not interested: videos', 'Not interested: titles', 'Showing fewer: channel', 'Hidden channels']) expect(screen.getByRole('heading', { level: 3, name })).toBeTruthy();
    expect(screen.getByText(/^Back to normal on /)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Restore recommendations of Quiet Channel' }));
    expect(props.onRestoreSuppression).toHaveBeenLastCalledWith('fewer-1');
    expect(document.querySelector('.admin-confirm, .suppression-settings-card, .setting-target')).toBeNull();
  });

  it('interests are checkboxes with the same Save interests button', async () => {
    const props = renderSection('discovery');
    await userEvent.click(screen.getByRole('checkbox', { name: 'Music' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save interests' }));
    expect(props.onSaveInterests).toHaveBeenCalledWith(['music']);
  });
});
