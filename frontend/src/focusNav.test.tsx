import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { moveFocus, nearestVertical } from './features/media/focusNav';

const box = (left: number, top: number) => ({ left, top, width: 90, height: 120 });

describe('10-foot focus navigation', () => {
  it('moves up and down to the nearest card in the next visual line', () => {
    const grid = [box(0, 0), box(100, 0), box(200, 0), box(0, 150), box(100, 150)];
    expect(nearestVertical(grid, 2, 1)).toBe(4);
    expect(nearestVertical(grid, 4, -1)).toBe(1);
    expect(nearestVertical(grid, 0, -1)).toBeNull();
    // A shelf further down is only reached once the nearer line is used up.
    expect(nearestVertical([box(0, 0), box(300, 150), box(0, 400)], 0, 1)).toBe(1);
  });

  it('keeps Left/Right inside the focused row and leaves text fields alone', () => {
    render(
      <div onKeyDown={moveFocus}>
        <div data-focus-row><button className="thumbnail-play-target" type="button">A</button><button className="thumbnail-play-target" type="button">B</button></div>
        <div data-focus-row><button data-focus-item type="button">C</button><input aria-label="Search" /></div>
      </div>,
    );
    screen.getByText('A').focus();
    fireEvent.keyDown(screen.getByText('A'), { key: 'ArrowRight' });
    expect(document.activeElement).toBe(screen.getByText('B'));
    fireEvent.keyDown(screen.getByText('B'), { key: 'ArrowRight' });
    expect(document.activeElement).toBe(screen.getByText('B'));
    const search = screen.getByRole('textbox', { name: 'Search' });
    search.focus();
    fireEvent.keyDown(search, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(search);
  });

  it('lets Up/Down leave a tablist or radiogroup while Left/Right stays inside', () => {
    render(
      <div onKeyDown={moveFocus}>
        <div role="tablist"><button type="button">Tab A</button><button type="button">Tab B</button></div>
        <div data-focus-row><button className="thumbnail-play-target" type="button">Play</button></div>
      </div>,
    );
    const tabA = screen.getByText('Tab A');
    tabA.focus();
    fireEvent.keyDown(tabA, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(tabA);
    fireEvent.keyDown(tabA, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByText('Play'));
  });

  it('focuses the nearest target when an arrow fires from a non-target', () => {
    render(
      <div onKeyDown={moveFocus}>
        <h1 tabIndex={-1}>Title</h1>
        <div data-focus-row><button className="thumbnail-play-target" type="button">Play</button></div>
      </div>,
    );
    const heading = screen.getByText('Title');
    heading.focus();
    fireEvent.keyDown(heading, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByText('Play'));
  });
});

const at = (element: HTMLElement, left: number, top: number, width = 200, height = 48) => Object.defineProperty(element, 'getBoundingClientRect', {
  configurable: true,
  value: () => ({ left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON: () => ({}) }),
});

describe('Settings focus rules', () => {
  it('lets arrow keys leave a checkbox, which has no arrow behaviour of its own', () => {
    render(<div onKeyDown={moveFocus}><div data-focus-row><input aria-label="Autoplay" data-focus-item type="checkbox" /><button data-focus-item type="button">After</button></div></div>);
    const box = screen.getByRole('checkbox', { name: 'Autoplay' });
    box.focus();
    fireEvent.keyDown(box, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(screen.getByText('After'));
  });

  it('with verticalExit, Up/Down leave a number field or select while Left/Right stay inside', () => {
    render(
      <div onKeyDown={(event) => moveFocus(event, { targets: 'input, select, button', verticalExit: 'input[type="number"], select' })}>
        <div data-focus-row><input aria-label="Limit" type="number" /></div>
        <div data-focus-row><select aria-label="Quality"><option>Best</option></select></div>
        <div data-focus-row><button type="button">Save</button></div>
      </div>,
    );
    const limit = screen.getByRole('spinbutton', { name: 'Limit' });
    const quality = screen.getByRole('combobox', { name: 'Quality' });
    const save = screen.getByText('Save');
    at(limit, 0, 0); at(quality, 0, 100); at(save, 0, 200);
    limit.focus();
    fireEvent.keyDown(limit, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(limit);
    fireEvent.keyDown(limit, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(quality);
    fireEvent.keyDown(quality, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(save);
    fireEvent.keyDown(save, { key: 'ArrowUp' });
    expect(document.activeElement).toBe(quality);
  });

  it('skips controls inside a closed <details> or a hidden subtree', () => {
    render(
      <div onKeyDown={(event) => moveFocus(event, { targets: 'button, input' })}>
        <div data-focus-row><button type="button">First</button><details><summary>More</summary><input aria-label="Folder" /></details><span hidden><button type="button">Ghost</button></span><button type="button">Last</button></div>
      </div>,
    );
    screen.getByText('First').focus();
    fireEvent.keyDown(screen.getByText('First'), { key: 'ArrowRight' });
    expect(document.activeElement).toBe(screen.getByText('Last'));
  });

  it('skips a control hidden by visibility, which cannot take focus (a hover-revealed Remove button)', () => {
    render(
      <div onKeyDown={(event) => moveFocus(event, { targets: 'button' })}>
        <button type="button">Resume</button>
        <button type="button" style={{ visibility: 'hidden' }}>Remove</button>
        <button type="button">Next shelf</button>
      </div>,
    );
    at(screen.getByText('Resume'), 0, 0);
    at(screen.getByText('Remove'), 0, 60);
    at(screen.getByText('Next shelf'), 0, 200);
    screen.getByText('Resume').focus();
    fireEvent.keyDown(screen.getByText('Resume'), { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByText('Next shelf'));
  });
});
