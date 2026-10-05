import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { InfoButton } from './features/settings/InfoButton';

describe('ⓘ explanation', () => {
  it('is a real button that discloses its text and returns focus on Escape', async () => {
    const browser = userEvent.setup();
    render(<InfoButton label="Semantic search" text="Finds titles by meaning. Uses the search model. Affects everyone on this server." />);
    const button = screen.getByRole('button', { name: 'About Semantic search' });
    const popover = document.getElementById(button.getAttribute('aria-controls') ?? '') as HTMLElement;
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(popover.hidden).toBe(true);
    expect(popover.textContent).toBe('Finds titles by meaning. Uses the search model. Affects everyone on this server.');
    button.focus();
    await browser.keyboard('{Enter}');
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(popover.hidden).toBe(false);
    await browser.keyboard('{Escape}');
    expect(popover.hidden).toBe(true);
    expect(document.activeElement).toBe(button);
  });

  it('closes on a TV Back key and on a tap outside, leaving focus on what was tapped', async () => {
    const browser = userEvent.setup();
    render(<><InfoButton label="Theme" text="Light or dark colours. Affects only you." /><input aria-label="Elsewhere" /></>);
    const button = screen.getByRole('button', { name: 'About Theme' });
    await browser.click(button);
    fireEvent.keyDown(button, { key: 'GoBack' });
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(button);
    await browser.click(button);
    const elsewhere = screen.getByRole('textbox', { name: 'Elsewhere' });
    await browser.click(elsewhere);
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(elsewhere);
  });

  it('keeps one explanation open at a time, by pointer and by keyboard', async () => {
    const browser = userEvent.setup();
    render(<><InfoButton label="Autoplay" text="Plays the next video. Affects only you." /><InfoButton label="Theme" text="Light or dark colours. Affects only you." /></>);
    const autoplay = screen.getByRole('button', { name: 'About Autoplay' });
    const theme = screen.getByRole('button', { name: 'About Theme' });
    await browser.click(autoplay);
    await browser.click(theme);
    expect(autoplay.getAttribute('aria-expanded')).toBe('false');
    expect(theme.getAttribute('aria-expanded')).toBe('true');
    autoplay.focus();
    await browser.keyboard('{Enter}');
    expect(autoplay.getAttribute('aria-expanded')).toBe('true');
    expect(theme.getAttribute('aria-expanded')).toBe('false');
  });
});

describe('ⓘ explanation look (app polish)', () => {
  it('renders the note as a g-popover and keeps its role and aria wiring', () => {
    render(<InfoButton label="Theme" text="Light or dark colours. Affects only you." />);
    const button = screen.getByRole('button', { name: 'About Theme' });
    const note = document.getElementById(button.getAttribute('aria-controls') ?? '') as HTMLElement;
    expect(note.classList.contains('g-popover')).toBe(true);
    expect(note.getAttribute('role')).toBe('note');
    expect(button.getAttribute('aria-expanded')).toBe('false');
  });
});
