import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createContext, type ReactNode, useContext, useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { searchSettings, visibleSections } from './features/settings/registry';
import { SectionForm } from './features/settings/SectionForm';
import { SectionRows, SettingRow } from './features/settings/SettingRow';
import type { SettingsSectionDef } from './features/settings/settingsTypes';
import { confirmLeaveSettings } from './features/settings/unsavedChanges';

const Toggle = () => <label><input type="checkbox" /> Even out loudness</label>;
const playback: SettingsSectionDef = {
  id: 'playback', group: 'you', label: 'Playback', summary: 'How video plays for you.',
  entries: [
    { id: 'playback.loudness', label: 'Sound', info: 'Keeps quiet films and loud episodes at a similar volume. Affects only you.', keywords: ['normalize'], Control: Toggle },
    { id: 'playback.autoplay', label: 'Autoplay', info: 'Starts the next related video. Affects only you.', Control: Toggle },
  ],
};
const backups: SettingsSectionDef = {
  id: 'backups', group: 'server', label: 'Backups', summary: 'Copies of the database.',
  entries: [{ id: 'backups.list', label: 'Database backups', info: 'Copies of accounts and settings. Covers the whole server.', layout: 'block', Control: () => <button type="button">Back up now</button> }],
};

describe('settings search', () => {
  it('matches label, ⓘ text, keyword and section name, and every word must match', () => {
    expect(searchSettings('sound', [playback]).map((match) => match.entries.map((entry) => entry.id))).toEqual([['playback.loudness']]);
    expect(searchSettings('QUIET films', [playback])[0].entries[0].id).toBe('playback.loudness');
    expect(searchSettings('normal', [playback])[0].entries[0].id).toBe('playback.loudness');
    expect(searchSettings('playback', [playback])[0].entries).toHaveLength(2);
    expect(searchSettings('sound autoplay', [playback])).toEqual([]);
    expect(searchSettings('   ', [playback, backups])).toEqual([]);
  });

  it('never offers Server sections to members', () => {
    expect(visibleSections({ role: 'viewer' } as never, [playback, backups]).map((section) => section.id)).toEqual(['playback']);
    expect(visibleSections({ role: 'admin' } as never, [playback, backups]).map((section) => section.id)).toEqual(['playback', 'backups']);
  });
});

describe('SettingRow', () => {
  it('renders the label as a heading, an ⓘ button and the control, tagged with its registry id', () => {
    render(<SettingRow id="playback.loudness" info="Keeps quiet films and loud episodes at a similar volume. Affects only you." label="Sound"><Toggle /></SettingRow>);
    const row = document.querySelector('[data-setting-id="playback.loudness"]') as HTMLElement;
    expect(row.hasAttribute('data-focus-row')).toBe(true);
    expect(screen.getByRole('heading', { level: 3, name: 'Sound' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'About Sound' }).getAttribute('aria-controls')).toBe('setting-playback-loudness-info');
    expect(screen.getByRole('checkbox', { name: 'Even out loudness' })).toBeTruthy();
  });

  it('adds the live detail slot only when there is detail', () => {
    const { rerender } = render(<SettingRow id="ai.search-model" info="Two short. Sentences here." label="Search model">x</SettingRow>);
    expect(document.querySelector('.g-setting-detail')).toBeNull();
    rerender(<SettingRow detail="Downloading 40%" id="ai.search-model" info="Two short. Sentences here." label="Search model">x</SettingRow>);
    expect(document.querySelector('.g-setting-detail')?.getAttribute('aria-live')).toBe('polite');
  });
});

describe('Save forms and the unsaved-changes guard', () => {
  afterEach(() => vi.restoreAllMocks());
  const Form = createContext<{ value: string; set: (value: string) => void } | null>(null);
  function Provider({ children }: { children: ReactNode }) {
    const [saved, setSaved] = useState('3');
    const [value, set] = useState('3');
    return <Form.Provider value={{ value, set }}><SectionForm dirty={value !== saved} label="Media server" onDiscard={() => set(saved)} onSave={() => setSaved(value)} saveLabel="Save media server settings" saving={false} status={null}>{children}</SectionForm></Form.Provider>;
  }
  function Field() {
    const form = useContext(Form);
    return <label>Conversions <input onChange={(event) => form?.set(event.target.value)} type="number" value={form?.value ?? ''} /></label>;
  }
  const section: SettingsSectionDef = { id: 'media', group: 'server', label: 'Media server', summary: 'Details.', Provider, entries: [{ id: 'media.sessions', label: 'Simultaneous conversions', info: 'One sentence. Two sentences.', Control: Field }] };

  it('wraps rows in the section Provider with one Save bar, and asks before leaving unsaved edits', async () => {
    const browser = userEvent.setup();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<SectionRows section={section} />);
    const save = screen.getByRole('button', { name: 'Save media server settings' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(confirmLeaveSettings()).toBe(true);
    expect(confirm).not.toHaveBeenCalled();
    const field = screen.getByRole('spinbutton', { name: 'Conversions' });
    await browser.clear(field);
    await browser.type(field, '5');
    expect(save.disabled).toBe(false);
    const bar = document.querySelector('.g-save-bar') as HTMLElement;
    expect(bar.textContent).toContain('Unsaved changes');
    expect(screen.getByRole('status').textContent).toBe('Unsaved changes');
    expect(confirmLeaveSettings()).toBe(false);
    expect(confirm).toHaveBeenCalledWith('You have unsaved changes in Media server. Leave without saving?');
    await browser.click(save);
    expect(confirmLeaveSettings()).toBe(true);
  });

  it('the save bar says Unsaved changes and Discard reverts: discards unsaved edits back to the saved value', async () => {
    const browser = userEvent.setup();
    render(<SectionRows section={section} />);
    const field = screen.getByRole('spinbutton', { name: 'Conversions' });
    await browser.type(field, '9');
    await browser.click(screen.getByRole('button', { name: 'Discard' }));
    expect((field as HTMLInputElement).value).toBe('3');
    expect(screen.queryByRole('button', { name: 'Discard' })).toBeNull();
    expect(confirmLeaveSettings()).toBe(true);
  });
});
