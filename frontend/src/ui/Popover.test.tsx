import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Popover } from '.';

afterEach(() => vi.useRealTimers());
const setup = (openOnHover = false) => render(<Popover label="Download activity" openOnHover={openOnHover} trigger={(props) => <button {...props} type="button">Activity</button>}><p>Download activity may be out of date</p></Popover>);

describe('Popover', () => {
  it('toggles a labelled non-modal dialog on click and closes on Esc back to the trigger', async () => {
    setup();
    const trigger = screen.getByRole('button', { name: 'Activity' });
    await userEvent.click(trigger);
    expect(screen.getByRole('dialog', { name: 'Download activity' })).not.toBeNull();
    expect(trigger.getAttribute('aria-haspopup')).toBe('dialog');
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('opens on hover or keyboard focus when asked, and closes after the pointer leaves', () => {
    vi.useFakeTimers();
    setup(true);
    const trigger = screen.getByRole('button', { name: 'Activity' });
    fireEvent.pointerEnter(trigger);
    expect(screen.getByRole('dialog')).not.toBeNull();
    fireEvent.pointerLeave(trigger);
    act(() => { vi.advanceTimersByTime(200); });
    expect(screen.queryByRole('dialog')).toBeNull();
    act(() => { trigger.focus(); });
    fireEvent.focus(trigger);
    expect(screen.getByRole('dialog')).not.toBeNull();
    fireEvent.blur(trigger);
    act(() => { vi.advanceTimersByTime(200); });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('keeps a hover popover open when the hovering pointer then clicks the trigger', async () => {
    setup(true);
    await userEvent.click(screen.getByRole('button', { name: 'Activity' })); // hover opens it, the click must not toggle it shut
    expect(screen.getByRole('dialog', { name: 'Download activity' })).not.toBeNull();
  });

  it('closes a click-opened popover when a click, focus move or Tab goes elsewhere', async () => {
    render(<><Popover label="Download activity" trigger={(props) => <button {...props} type="button">Activity</button>}><button type="button">Inside</button></Popover><button type="button">Elsewhere</button></>);
    const trigger = screen.getByRole('button', { name: 'Activity' });
    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole('button', { name: 'Inside' }));
    expect(screen.getByRole('dialog')).not.toBeNull(); // inside the panel is not a leave
    await userEvent.click(screen.getByRole('button', { name: 'Elsewhere' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await userEvent.click(trigger);
    await userEvent.click(document.body); // a click on nothing focusable
    expect(screen.queryByRole('dialog')).toBeNull();
    await userEvent.click(trigger);
    act(() => { screen.getByRole('button', { name: 'Elsewhere' }).focus(); });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('follows its trigger when anything scrolls', async () => {
    setup();
    const trigger = screen.getByRole('button', { name: 'Activity' });
    const box = (top: number) => ({ left: 10, right: 110, top, bottom: top + 30, width: 100, height: 30, x: 10, y: top, toJSON: () => ({}) });
    trigger.getBoundingClientRect = () => box(100);
    await userEvent.click(trigger);
    const panel = screen.getByRole('dialog');
    const before = panel.style.top;
    trigger.getBoundingClientRect = () => box(60);
    act(() => { document.dispatchEvent(new Event('scroll')); });
    expect(panel.style.top).not.toBe(before);
  });

  it('follows its trigger when the window resizes', async () => {
    setup();
    const trigger = screen.getByRole('button', { name: 'Activity' });
    const box = (top: number) => ({ left: 10, right: 110, top, bottom: top + 30, width: 100, height: 30, x: 10, y: top, toJSON: () => ({}) });
    trigger.getBoundingClientRect = () => box(100);
    await userEvent.click(trigger);
    const panel = screen.getByRole('dialog');
    const before = panel.style.top;
    trigger.getBoundingClientRect = () => box(40);
    act(() => { window.dispatchEvent(new Event('resize')); });
    expect(panel.style.top).not.toBe(before);
  });
});
