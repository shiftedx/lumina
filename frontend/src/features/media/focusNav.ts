import type { KeyboardEvent } from 'react';

export type Box = { left: number; top: number; width: number; height: number };

/** Cards, episode rows and title-page actions that arrow keys move between. */
export const FOCUS_TARGETS = '.thumbnail-play-target:not(:disabled), [data-focus-item]:not(:disabled)';
/** Text-entry widgets: own every arrow key. Checkboxes have no arrow behaviour, so arrows leave them. */
const OWNS_ALL_ARROWS = 'input:not([type="radio"]):not([type="checkbox"]), textarea, select, [role="menu"], [contenteditable="true"]';
/** Single-axis widgets: own Left/Right only, so Up/Down still leave the tablist or radio group. */
const OWNS_HORIZONTAL_ARROWS = '[role="tablist"], [role="radiogroup"], input[type="radio"]';
/** Closed <details> bodies and hidden subtrees cannot take focus, so arrows skip them (as they skip visibility: hidden). */
// The art menu's ⋯ is a Tab stop only: arrows move between cards, never onto the corner of one.
const UNREACHABLE = '[hidden], details:not([open]) > :not(summary), .g-art-menu';

/** Escape or Backspace outside a text field or dialog: the TV remote's Back. Callers do the leaving. */
export function isBackKey(event: KeyboardEvent<HTMLElement>) {
  return (event.key === 'Escape' || event.key === 'Backspace') && !event.defaultPrevented && !(event.target as HTMLElement).closest('input, textarea, select, dialog, [role="dialog"], [contenteditable="true"]');
}

export type MoveFocusOptions = {
  /** What arrow keys move between; defaults to FOCUS_TARGETS (cards, rows, title actions). */
  targets?: string;
  /** Fields matching this give Up/Down back to navigation while Left/Right keep editing (Settings on a TV remote). */
  verticalExit?: string;
  /** Fields matching this give Left/Right back to navigation (a toolbar's <select> on a TV remote). */
  horizontalExit?: string;
};

/** When focus sits outside every target (e.g. a page's h1), an arrow key still reaches the closest one. */
function nearestTarget(targets: HTMLElement[], active: HTMLElement): HTMLElement | undefined {
  const from = active.getBoundingClientRect();
  const cx = from.left + from.width / 2;
  const cy = from.top + from.height / 2;
  let best: { target: HTMLElement; distance: number } | null = null;
  for (const target of targets) {
    const box = target.getBoundingClientRect();
    const dx = box.left + box.width / 2 - cx;
    const dy = box.top + box.height / 2 - cy;
    const distance = dx * dx + dy * dy;
    if (best === null || distance < best.distance) best = { target, distance };
  }
  return best?.target;
}

/** Up/Down: the closest line above/below, then the nearest horizontal centre within it (half a card of slack). */
export function nearestVertical(boxes: Box[], from: number, direction: 1 | -1): number | null {
  const current = boxes[from];
  const slack = current.height / 2;
  const cx = current.left + current.width / 2;
  const cy = current.top + current.height / 2;
  let best: { index: number; dy: number; dx: number } | null = null;
  for (let index = 0; index < boxes.length; index += 1) {
    const box = boxes[index];
    const dy = (box.top + box.height / 2 - cy) * direction;
    if (index === from || dy <= slack) continue;
    const dx = Math.abs(box.left + box.width / 2 - cx);
    if (best === null || dy < best.dy - slack || (Math.abs(dy - best.dy) <= slack && dx < best.dx)) best = { index, dy, dx };
  }
  return best?.index ?? null;
}

/**
 * Roving arrow-key focus for TV remotes and keyboards. Left/Right step through the
 * focused item's `[data-focus-row]` (else the whole container) in DOM order; Up/Down jump to the
 * nearest item in the next visual line; from a non-target, either direction reaches the nearest
 * target. Enter activates the focused button natively.
 *
 * A shelf scroller (`[data-focus-row]`) is itself focusable so a sighted mouse/keyboard user can
 * scroll it directly; when it holds focus, Left/Right keep their native browser scroll instead of
 * jumping to the nearest card (that jump still happens from every other non-target position).
 */
export function moveFocus(event: KeyboardEvent<HTMLElement>, options: MoveFocusOptions = {}) {
  const horizontal = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0;
  const vertical = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
  if ((!horizontal && !vertical) || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
  const active = document.activeElement as HTMLElement | null;
  if (!active) return;
  const exit = vertical ? options.verticalExit : options.horizontalExit;
  const exits = exit !== undefined && active.matches(exit);
  if (active.closest(OWNS_ALL_ARROWS) && !exits) return;
  if (horizontal && active.closest(OWNS_HORIZONTAL_ARROWS)) return;
  if (horizontal && active.matches('[data-focus-row]')) return;
  const targets = [...event.currentTarget.querySelectorAll<HTMLElement>(options.targets ?? FOCUS_TARGETS)].filter((target) => !target.closest(UNREACHABLE) && getComputedStyle(target).visibility !== 'hidden');
  const from = targets.findIndex((target) => target === active || target.contains(active));
  let next: HTMLElement | undefined;
  if (from < 0) {
    next = nearestTarget(targets, active);
  } else if (horizontal) {
    const row = targets[from].closest('[data-focus-row]') ?? event.currentTarget;
    const peers = targets.filter((target) => row.contains(target));
    next = peers[peers.indexOf(targets[from]) + horizontal];
  } else {
    const index = nearestVertical(targets.map((target) => target.getBoundingClientRect()), from, vertical as 1 | -1);
    next = index === null ? undefined : targets[index];
  }
  if (!next) return;
  event.preventDefault();
  next.focus();
  next.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
}
