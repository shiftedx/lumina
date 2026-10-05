import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Menu, type MenuEntry } from '.';

const settings = vi.fn(); const signOut = vi.fn(); const light = vi.fn();
const ITEMS: MenuEntry[] = [
  { kind: 'group', label: 'Dana · Vault owner' },
  { kind: 'item', label: 'Switch member…', onSelect: vi.fn() },
  { kind: 'item', label: 'Settings', onSelect: settings },
  { kind: 'separator' },
  { kind: 'group', label: 'Theme' },
  { kind: 'radio', label: 'System', checked: true, onSelect: vi.fn() },
  { kind: 'radio', label: 'Light', checked: false, onSelect: light },
  { kind: 'item', label: 'Disabled thing', disabled: true, onSelect: vi.fn() },
  { kind: 'item', label: 'Sign out', onSelect: signOut },
];
const setup = () => render(<><Menu items={ITEMS} trigger={(props) => <button {...props} type="button">Dana, account menu</button>} /><button type="button">After</button></>);
const trigger = () => screen.getByRole('button', { name: 'Dana, account menu' });
const box = (r: { left: number; right: number; top: number; bottom: number }) => ({ ...r, width: r.right - r.left, height: r.bottom - r.top, x: r.left, y: r.top, toJSON: () => ({}) });

describe('Menu', () => {
  it('Enter, Space and ArrowDown open on the first item; ArrowUp on the last', async () => {
    setup();
    for (const key of ['{Enter}', ' ', '{ArrowDown}']) {
      trigger().focus();
      await userEvent.keyboard(key);
      expect(screen.getByRole('menu')).not.toBeNull();
      expect(trigger().getAttribute('aria-expanded')).toBe('true');
      expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'Switch member…' }));
      await userEvent.keyboard('{Escape}');
    }
    trigger().focus();
    await userEvent.keyboard('{ArrowUp}');
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'Sign out' }));
  });

  it('moves with Up/Down (wrapping), Home and End, and by type-ahead', async () => {
    let now = 0;
    const clock = vi.spyOn(Date, 'now').mockImplementation(() => now);
    setup();
    trigger().focus();
    await userEvent.keyboard('{ArrowDown}');
    await userEvent.keyboard('{ArrowUp}');
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'Sign out' }));
    await userEvent.keyboard('{ArrowDown}');
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'Switch member…' }));
    await userEvent.keyboard('{End}');
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'Sign out' }));
    await userEvent.keyboard('{Home}s');
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'Settings' }));
    await userEvent.keyboard('s'); // the same letter again cycles
    expect(document.activeElement).toBe(screen.getByRole('menuitemradio', { name: 'System' }));
    now += 1000;
    await userEvent.keyboard('li');
    expect(document.activeElement).toBe(screen.getByRole('menuitemradio', { name: 'Light' }));
    clock.mockRestore();
  });

  it('Esc closes and refocuses the trigger', async () => {
    setup();
    trigger().focus();
    await userEvent.keyboard('{Enter}{Escape}');
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });

  it('Tab closes, returns focus to the trigger and leaves the key to the browser', async () => {
    setup();
    trigger().focus();
    await userEvent.keyboard('{Enter}');
    // fireEvent: user-event would compute the next stop from the (removed) menu item, which a real browser does not.
    const proceeded = fireEvent.keyDown(screen.getByRole('menuitem', { name: 'Switch member…' }), { key: 'Tab' });
    expect(proceeded).toBe(true); // not default-prevented: the browser's own Tab goes on from the trigger to the next stop
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(trigger());
    await userEvent.tab();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'After' }));
  });

  it('a click on the open trigger closes it rather than reopening', async () => {
    setup();
    await userEvent.click(trigger());
    await userEvent.click(trigger());
    expect(screen.queryByRole('menu')).toBeNull();
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
  });

  it('runs the chosen item, marks radios, skips disabled items and refocuses the trigger', async () => {
    setup();
    await userEvent.click(trigger());
    expect(screen.getByRole('menuitemradio', { name: 'System' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('group', { name: 'Theme' })).not.toBeNull();
    await userEvent.click(screen.getByRole('menuitem', { name: 'Disabled thing' }));
    expect(screen.getByRole('menu')).not.toBeNull();
    await userEvent.keyboard('{Escape}');
    await userEvent.click(trigger());
    screen.getByRole('menuitemradio', { name: 'Light' }).focus();
    await userEvent.keyboard('{Enter}');
    expect(light).toHaveBeenCalledOnce();
    expect(document.activeElement).toBe(trigger());
  });

  it('closes when focus leaves it (click elsewhere)', async () => {
    setup();
    await userEvent.click(trigger());
    act(() => { screen.getByRole('button', { name: 'After' }).focus(); });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
  });

  it('opens above the trigger when there is no room below', async () => {
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 600 });
    setup();
    trigger().getBoundingClientRect = () => ({ top: 560, bottom: 590, left: 10, right: 110, width: 100, height: 30, x: 10, y: 560, toJSON: () => ({}) });
    await userEvent.click(trigger());
    const menu = screen.getByRole('menu');
    menu.getBoundingClientRect = () => ({ top: 0, bottom: 200, left: 0, right: 200, width: 200, height: 200, x: 0, y: 0, toJSON: () => ({}) });
    act(() => { window.dispatchEvent(new Event('resize')); });
    expect(menu.dataset.side).toBe('top');
  });

  // Ported from RecoMenu.test.tsx / the reco e2e specs.
  it('keeps both edges 8px inside the viewport at 375 px, the left edge included', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 375 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 812 });
    render(<Menu align="end" items={ITEMS} trigger={(props) => <button {...props} type="button">Dana, account menu</button>} />);
    // A first-column card: its button's right edge is at 285 px, so a 340 px list right-aligned to it would start at -55 px.
    trigger().getBoundingClientRect = () => box({ left: 261, right: 285, top: 100, bottom: 124 });
    await userEvent.click(trigger());
    const menu = screen.getByRole('menu');
    menu.getBoundingClientRect = () => box({ left: 0, right: 340, top: 0, bottom: 200 });
    act(() => { window.dispatchEvent(new Event('resize')); });
    expect(parseFloat(menu.style.left)).toBe(8);
    // A button at the far right edge: a 280 px list right-aligned to it (right edge 380 px) would overflow; it is pulled back inside.
    trigger().getBoundingClientRect = () => box({ left: 356, right: 380, top: 100, bottom: 124 });
    menu.getBoundingClientRect = () => box({ left: 0, right: 280, top: 0, bottom: 200 });
    act(() => { window.dispatchEvent(new Event('resize')); });
    expect(parseFloat(menu.style.left) + 280).toBe(375 - 8);
  });

  it('follows its trigger when anything scrolls', async () => {
    setup();
    trigger().getBoundingClientRect = () => box({ left: 10, right: 110, top: 100, bottom: 130 });
    await userEvent.click(trigger());
    const menu = screen.getByRole('menu');
    menu.getBoundingClientRect = () => box({ left: 0, right: 220, top: 0, bottom: 100 });
    act(() => { document.dispatchEvent(new Event('scroll')); });
    const before = menu.style.top;
    trigger().getBoundingClientRect = () => box({ left: 10, right: 110, top: 60, bottom: 90 }); // a rail scrolled it up
    act(() => { document.dispatchEvent(new Event('scroll')); });
    expect(menu.style.top).not.toBe(before);
  });

  it('keeps the keys it handles away from an ancestor\'s key handler (a surface\'s row navigation)', async () => {
    const surface = vi.fn();
    render(<div onKeyDown={surface}><Menu items={ITEMS} trigger={(props) => <button {...props} type="button">Dana, account menu</button>} /></div>);
    await userEvent.click(trigger());
    surface.mockClear();
    await userEvent.keyboard('{ArrowDown}{ArrowUp}{Home}{End}s{Escape}');
    expect(surface).not.toHaveBeenCalled();
  });

  it('arrowOpens={false} leaves the arrows to the surrounding grid and Enter still opens', async () => {
    const grid = vi.fn();
    render(<div onKeyDown={grid}><Menu arrowOpens={false} items={ITEMS} trigger={(props) => <button {...props} data-focus-item type="button">Dana, account menu</button>} /></div>);
    trigger().focus();
    await userEvent.keyboard('{ArrowDown}');
    expect(screen.queryByRole('menu')).toBeNull();
    expect(grid).toHaveBeenCalled();
    await userEvent.keyboard('{Enter}');
    expect(screen.getByRole('menu')).not.toBeNull();
  });

  it('a disclosure item expands its detail without closing or selecting, and collapses when the menu closes', async () => {
    const onSelect = vi.fn();
    render(<Menu items={[{ kind: 'item', label: 'Why this?', detail: 'Because you finished Harbor walk', onSelect }, { kind: 'item', label: 'Not interested', onSelect: vi.fn() }]} trigger={(props) => <button {...props} type="button">Dana, account menu</button>} />);
    await userEvent.click(trigger());
    const why = screen.getByRole('menuitem', { name: /Why this\?/ });
    expect(why.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByText('Because you finished Harbor walk')).toBeNull();
    await userEvent.click(why);
    expect(screen.getByText('Because you finished Harbor walk')).not.toBeNull();
    expect(why.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('menu')).not.toBeNull();
    expect(onSelect).not.toHaveBeenCalled();
    await userEvent.click(why);
    expect(screen.queryByText('Because you finished Harbor walk')).toBeNull();
    await userEvent.click(why);
    await userEvent.keyboard('{Escape}');
    await userEvent.click(trigger());
    expect(screen.queryByText('Because you finished Harbor walk')).toBeNull(); // reopening shows it collapsed
  });

  it('is a styled top-layer panel with no .gallery ancestor (it mounts wherever its trigger is)', async () => {
    setup();
    await userEvent.click(trigger());
    const menu = screen.getByRole('menu');
    expect(menu.closest('.gallery')).toBeNull();
    expect(menu.className).toContain('g-menu');
    expect(menu.getAttribute('popover')).toBe('manual');
  });

  it('a press on the open menu\'s trigger closes it and the click does not reopen it (Safari: blur has no relatedTarget)', async () => {
    setup();
    await userEvent.click(trigger());
    const item = screen.getByRole('menuitem', { name: 'Switch member…' });
    fireEvent.pointerDown(trigger());
    fireEvent.focusOut(item, { relatedTarget: null });
    fireEvent.click(trigger());
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('a press on the trigger released elsewhere does not leave the menu stuck open', async () => {
    setup();
    trigger().focus();
    await userEvent.keyboard('{Enter}');
    const item = screen.getByRole('menuitem', { name: 'Switch member…' });
    fireEvent.pointerDown(trigger());
    act(() => item.blur()); // WebKit: the item blurs at mousedown, before the release
    expect(screen.getByRole('menu')).toBeTruthy(); // the blur is left to the press
    fireEvent.pointerUp(document.body); // released off the trigger: no click
    expect(screen.queryByRole('menu')).toBeNull();
  });
});
