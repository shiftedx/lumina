import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';

import {
  TheaterModeControl,
  isTheaterModeShortcut,
  resolveTheaterModePreference,
  theaterModePreferenceFromUiPrefs,
  withTheaterModePreference,
} from './theaterMode';

describe('theater mode seam', () => {
  it('restores a member theater choice and preserves unrelated UI preferences when updating it', () => {
    expect(resolveTheaterModePreference({ memberPreference: true, localFallback: false }))
      .toEqual({ theaterMode: true, source: 'member' });
    expect(resolveTheaterModePreference({ memberSettingsAvailable: true, localFallback: true }))
      .toEqual({ theaterMode: false, source: 'default' });
    expect(theaterModePreferenceFromUiPrefs({ theater_mode: 'true' })).toBeUndefined();
    expect(withTheaterModePreference({ sidebar_collapsed: true, theme: 'dawn' }, true))
      .toEqual({ sidebar_collapsed: true, theme: 'dawn', theater_mode: true });
  });

  it('uses the T shortcut only outside editable controls', () => {
    const input = document.createElement('input');
    const editable = document.createElement('div');
    editable.contentEditable = 'true';

    expect(isTheaterModeShortcut(new KeyboardEvent('keydown', { key: 't' }))).toBe(true);
    expect(isTheaterModeShortcut(new KeyboardEvent('keydown', { key: 't', ctrlKey: true }))).toBe(false);
    expect(isTheaterModeShortcut(new KeyboardEvent('keydown', { key: 't', bubbles: true }))).toBe(true);
    expect(isTheaterModeShortcut({ key: 't', target: input } as unknown as KeyboardEvent)).toBe(false);
    expect(isTheaterModeShortcut({ key: 't', target: editable } as unknown as KeyboardEvent)).toBe(false);
  });

  it('provides a 44px accessible control and changes its pressed state on repeated button and shortcut toggles', () => {
    function Harness() {
      const [theaterMode, setTheaterMode] = useState(false);
      return <TheaterModeControl theaterMode={theaterMode} onTheaterModeChange={setTheaterMode} />;
    }

    render(<Harness />);
    const toggle = screen.getByRole('button', { name: 'Enter theater mode' });
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    expect(toggle.getAttribute('title')).toBe('Enter theater mode');
    expect((toggle as HTMLElement).style.minHeight).toBe('44px');
    expect((toggle as HTMLElement).style.minWidth).toBe('44px');

    fireEvent.click(toggle);
    expect(screen.getByRole('button', { name: 'Exit theater mode' }).getAttribute('aria-pressed')).toBe('true');
    fireEvent.keyDown(document, { key: 't' });
    expect(screen.getByRole('button', { name: 'Enter theater mode' }).getAttribute('aria-pressed')).toBe('false');
    fireEvent.keyDown(screen.getByRole('button', { name: 'Enter theater mode' }), { key: 't' });
    expect(screen.getByRole('button', { name: 'Exit theater mode' }).getAttribute('aria-pressed')).toBe('true');
  });
});
