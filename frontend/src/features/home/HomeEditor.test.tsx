import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { HomeEditor } from './HomeEditor';
import { type HomeShelfId, type HomeShelfPref, normalizeHomeShelves } from './homeShelves';

/** The editor with a parent that applies every change, as HomeSurface does (null = the default layout). */
function renderEditor(stored: unknown = null, empty: HomeShelfId[] = ['live']) {
  const onChange = vi.fn();
  const onDone = vi.fn();
  function Harness() {
    const [layout, setLayout] = useState<HomeShelfPref[]>(normalizeHomeShelves(stored));
    return <HomeEditor empty={new Set(empty)} onChange={(next) => { onChange(next); setLayout(normalizeHomeShelves(next)); }} onDone={onDone} shelves={layout} />;
  }
  render(<Harness />);
  return { onChange, onDone };
}
const handle = (name: string) => screen.getByRole('button', { name: `Reorder ${name}` });
const said = () => document.querySelector('.h-editor [role="status"]')?.textContent;
const order = () => [...document.querySelectorAll('[data-shelf-row]')].map((row) => row.getAttribute('data-shelf-row'));
const key = (target: Element, name: string) => fireEvent.keyDown(target, { key: name });

describe('HomeEditor', () => {
  it('announces the shelves and focuses the first handle on entry', () => {
    renderEditor();
    expect(said()).toBe('Editing Home. 13 shelves.');
    expect(document.activeElement).toBe(handle('Continue watching'));
    expect(screen.getByRole('list', { name: 'Home shelves' }).children).toHaveLength(13);
    expect(handle('Next up').getAttribute('aria-describedby')).toBe('h-edit-hint');
    expect(document.getElementById('h-edit-hint')?.textContent).toBe('Drag, or use Move up and Move down, to reorder. Switch a shelf off to hide it.');
  });

  it('keyboard pick-up moves the shelf and keeps focus on its handle; Space drops', () => {
    const { onChange } = renderEditor();
    const next = handle('Next up');
    next.focus();
    key(next, ' ');
    expect(next.getAttribute('aria-pressed')).toBe('true');
    expect(said()).toBe('Next up picked up, position 3 of 13. Use Up and Down to move, Space to drop, Escape to cancel.');
    key(next, 'ArrowDown');
    expect(said()).toBe('Next up, position 4 of 13.');
    key(handle('Next up'), 'ArrowDown');
    expect(order().slice(0, 5)).toEqual(['continue', 'live', 'watchlist', 'new_in_library', 'next_up']);
    expect(document.activeElement).toBe(handle('Next up'));
    expect(onChange).toHaveBeenCalledTimes(2);
    key(handle('Next up'), ' ');
    expect(said()).toBe('Next up dropped at position 5 of 13.');
    expect(handle('Next up').getAttribute('aria-pressed')).toBe('false');
  });

  it('Home and End move a picked-up shelf to the first and last position', () => {
    renderEditor();
    key(handle('Next up'), 'Enter');
    key(handle('Next up'), 'End');
    expect(order().at(-1)).toBe('next_up');
    expect(said()).toBe('Next up, position 13 of 13.');
    key(handle('Next up'), 'Home');
    expect(order()[0]).toBe('next_up');
    expect(said()).toBe('Next up, position 1 of 13.');
  });

  it('Tab drops the shelf where it is', () => {
    renderEditor();
    key(handle('Next up'), ' ');
    key(handle('Next up'), 'ArrowUp');
    key(handle('Next up'), 'Tab');
    expect(said()).toBe('Next up dropped at position 2 of 13.');
    expect(handle('Next up').getAttribute('aria-pressed')).toBe('false');
  });

  it('Escape cancels a pick-up, then leaves edit mode', () => {
    const { onDone } = renderEditor();
    key(handle('Next up'), ' ');
    key(handle('Next up'), 'ArrowDown');
    key(handle('Next up'), 'ArrowDown');
    key(handle('Next up'), 'Escape');
    expect(order()[2]).toBe('next_up');
    expect(said()).toBe('Reorder cancelled. Next up is back at position 3.');
    expect(onDone).not.toHaveBeenCalled();
    key(handle('Next up'), 'Escape');
    expect(onDone).toHaveBeenCalledTimes(1);
    key(handle('Live now'), 'GoBack');
    expect(onDone).toHaveBeenCalledTimes(2);
  });

  it('arrow keys on an idle handle are left to focus navigation', () => {
    const { onChange } = renderEditor();
    const event = new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true });
    handle('Next up').dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
    expect(onChange).not.toHaveBeenCalled();
  });

  it('Move up and Move down keep focus on the same button, which is aria-disabled at the ends', () => {
    const { onChange } = renderEditor();
    const up = screen.getByRole('button', { name: 'Move Live now up' });
    fireEvent.click(up);
    expect(order()[0]).toBe('live');
    expect(said()).toBe('Live now, position 1 of 13.');
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Move Live now up' }));
    expect(screen.getByRole('button', { name: 'Move Live now up' }).getAttribute('aria-disabled')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Move Live now up' }));
    expect(onChange).toHaveBeenCalledTimes(1);
    const down = screen.getByRole('button', { name: 'Move Recently added music down' });
    expect(down.getAttribute('aria-disabled')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Move Continue watching down' }));
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Move Continue watching down' }));
  });

  it('the switch hides and shows a shelf, and each row says its status', () => {
    renderEditor(null, ['live']);
    const toggle = screen.getByRole('switch', { name: 'Show Next up' });
    expect(toggle.getAttribute('aria-checked')).toBe('true');
    fireEvent.click(toggle);
    expect(screen.getByRole('switch', { name: 'Show Next up' }).getAttribute('aria-checked')).toBe('false');
    expect(said()).toBe('Next up hidden.');
    const row = (id: string) => document.querySelector(`[data-shelf-row="${id}"]`) as HTMLElement;
    expect(within(row('next_up')).getByText('Hidden')).toBeTruthy();
    expect(within(row('live')).getByText('Nothing to show right now')).toBeTruthy();
    expect(row('next_up').classList.contains('is-hidden')).toBe(true);
    fireEvent.click(screen.getByRole('switch', { name: 'Show Next up' }));
    expect(said()).toBe('Next up shown.');
  });

  it('Reset asks for the default layout and focuses the first handle; Done leaves', () => {
    const { onChange, onDone } = renderEditor([{ id: 'recent_music', visible: false }]);
    expect(order()[0]).toBe('recent_music');
    const reset = screen.getByRole('button', { name: 'Reset to default' });
    const done = screen.getByRole('button', { name: 'Done' });
    expect(reset.hasAttribute('data-focus-item')).toBe(true); // E-I3: reachable below the last row by arrow keys / a TV remote
    expect(done.hasAttribute('data-focus-item')).toBe(true);
    reset.focus();
    act(() => { fireEvent.click(reset); });
    expect(onChange).toHaveBeenLastCalledWith(null);
    expect(order()[0]).toBe('continue');
    expect(document.activeElement).toBe(handle('Continue watching'));
    fireEvent.click(done);
    expect(onDone).toHaveBeenCalledTimes(1);
  });
});

describe('HomeEditor pointer drag', () => {
  const TOP = 100;
  const PITCH = 64;
  /** A layout box (jsdom lays nothing out). */
  const box = (top: number, height: number) => ({ x: 0, y: top, top, left: 0, right: 800, bottom: top + height, width: 800, height, toJSON: () => ({}) }) as DOMRect;
  /** Rows 64 px apart under a list whose top is at 100 px; the dragged row's own transform is ignored, like a layout box. */
  function stubGeometry() {
    return vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      if (this.matches('ol.h-edit')) return box(TOP, PITCH * 13);
      if (this.matches('[data-shelf-row]')) {
        const index = [...(this.parentElement?.children ?? [])].indexOf(this);
        return box(TOP + index * PITCH, PITCH);
      }
      return box(0, 0);
    });
  }
  /** The y of the middle of row `index`. */
  const rowY = (index: number) => TOP + index * PITCH + PITCH / 2;
  const pointer = (type: string, target: Element, clientY: number) => fireEvent(target, new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 1, button: 0, clientY }));

  it('lifts after 4 px, moves the neighbours out of the way, and drops at the closed-form target', () => {
    const geometry = stubGeometry();
    const { onChange } = renderEditor();
    const grip = handle('Next up');
    pointer('pointerdown', grip, rowY(2));
    pointer('pointermove', grip, rowY(2) + 2);
    expect(document.querySelector('[data-dragging]')).toBeNull();
    pointer('pointermove', grip, rowY(4));
    const row = document.querySelector('[data-shelf-row="next_up"]') as HTMLElement;
    expect(row.hasAttribute('data-dragging')).toBe(true);
    expect(row.style.transform).toBe('translateY(128px)');
    expect((document.querySelector('[data-shelf-row="watchlist"]') as HTMLElement).style.transform).toBe('translateY(-64px)');
    expect((document.querySelector('[data-shelf-row="because_you_watched"]') as HTMLElement).style.transform).toBe('');
    expect(onChange).not.toHaveBeenCalled(); // nothing is saved until the drop
    pointer('pointerup', grip, rowY(4));
    expect(order().slice(0, 5)).toEqual(['continue', 'live', 'watchlist', 'new_in_library', 'next_up']);
    expect(said()).toBe('Next up dropped at position 5 of 13.');
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[data-dragging]')).toBeNull();
    geometry.mockRestore();
  });

  it('auto-scrolls the page without the smooth CSS transition mid-drag (E-I2)', () => {
    // A two-argument scrollBy follows html { scroll-behavior: smooth }, so each frame restarts a smooth scroll and
    // the page crawls at a fraction of its speed. behavior: 'instant' is required to reach the full per-frame rate.
    const geometry = stubGeometry();
    let step: FrameRequestCallback | null = null;
    vi.stubGlobal('requestAnimationFrame', ((cb: FrameRequestCallback) => { step = cb; return 1; }) as typeof requestAnimationFrame);
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
    const scrollBy = vi.spyOn(window, 'scrollBy').mockImplementation(() => undefined);
    renderEditor();
    const grip = handle('Next up');
    const nearBottomEdge = window.innerHeight - 10; // within the 48 px edge band
    pointer('pointerdown', grip, rowY(2));
    pointer('pointermove', grip, nearBottomEdge);
    act(() => step?.(0));
    expect(scrollBy).toHaveBeenCalledTimes(1);
    const [call] = scrollBy.mock.calls[0];
    expect(call).toMatchObject({ behavior: 'instant' });
    expect((call as unknown as { top: number }).top).toBeCloseTo(9.5, 1);
    pointer('pointerup', grip, nearBottomEdge);
    scrollBy.mockRestore();
    vi.unstubAllGlobals();
    geometry.mockRestore();
  });

  it('under 4 px a press is not a drag', () => {
    const geometry = stubGeometry();
    const { onChange } = renderEditor();
    pointer('pointerdown', handle('Next up'), rowY(2));
    pointer('pointermove', handle('Next up'), rowY(2) + 3);
    pointer('pointerup', handle('Next up'), rowY(2) + 3);
    fireEvent.click(handle('Next up'));
    expect(onChange).not.toHaveBeenCalled();
    expect(said()).toBe('Editing Home. 13 shelves.');
    expect(handle('Next up').getAttribute('aria-pressed')).toBe('false');
    geometry.mockRestore();
  });

  it('a pointercancel or lost capture reverts without saving', () => {
    const geometry = stubGeometry();
    const { onChange } = renderEditor();
    for (const end of ['pointercancel', 'lostpointercapture']) {
      pointer('pointerdown', handle('Next up'), rowY(2));
      pointer('pointermove', handle('Next up'), rowY(6));
      pointer(end, handle('Next up'), rowY(6));
      expect(order()[2]).toBe('next_up');
      expect(said()).toBe('Reorder cancelled. Next up is back at position 3.');
      expect(document.querySelector('[data-dragging]')).toBeNull();
    }
    expect(onChange).not.toHaveBeenCalled();
    geometry.mockRestore();
  });

  it('Escape mid-drag reverts and does not leave edit mode', () => {
    const geometry = stubGeometry();
    const { onChange, onDone } = renderEditor();
    pointer('pointerdown', handle('Next up'), rowY(2));
    pointer('pointermove', handle('Next up'), rowY(0));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(said()).toBe('Reorder cancelled. Next up is back at position 3.');
    pointer('pointerup', handle('Next up'), rowY(0));
    expect(order()[2]).toBe('next_up');
    expect(onChange).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
    geometry.mockRestore();
  });

  it('ignores the secondary button and clamps a drag past either end', () => {
    const geometry = stubGeometry();
    renderEditor();
    fireEvent(handle('Next up'), new PointerEvent('pointerdown', { bubbles: true, pointerId: 1, button: 2, clientY: rowY(2) }));
    pointer('pointermove', handle('Next up'), rowY(6));
    expect(document.querySelector('[data-dragging]')).toBeNull();
    pointer('pointerdown', handle('Next up'), rowY(2));
    pointer('pointermove', handle('Next up'), rowY(40));
    pointer('pointerup', handle('Next up'), rowY(40));
    expect(order().at(-1)).toBe('next_up');
    geometry.mockRestore();
  });
});
