import { cleanup, configure } from '@testing-library/react';
import { afterEach, beforeEach } from 'vitest';

afterEach(cleanup);

// findBy*/waitFor default to 1s. Home's start card, for one, needs ~0.1s of CPU across several async hops when idle and
// ~0.4s at load average 160; a gate on a saturated machine overran 1s. Passing waits return at once, so only failures cost.
// Under vitest's 5s test timeout, so a genuine failure still reports the missing element.
configure({ asyncUtilTimeout: 4000 });

// Node's own experimental localStorage global shadows jsdom's, leaving window.localStorage
// undefined; give every jsdom test a fresh in-memory one.
beforeEach(() => {
  if (typeof window === 'undefined') return;
  const values = new Map<string, string>();
  const storage: Storage = {
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => Array.from(values.keys())[index] ?? null,
    get length() { return values.size; },
    removeItem: (key) => { values.delete(key); },
    setItem: (key, value) => { values.set(key, String(value)); },
  };
  Object.defineProperty(window, 'localStorage', { configurable: true, value: storage });
});

// jsdom has no modal dialogs or popovers (app polish: enough of each for role- and focus-based tests.
if (typeof HTMLDialogElement !== 'undefined') {
  const dialog = HTMLDialogElement.prototype as HTMLDialogElement & Record<string, unknown>;
  if (typeof dialog.showModal !== 'function') {
    dialog.showModal = function showModal(this: HTMLDialogElement) { this.setAttribute('open', ''); this.dataset.modal = 'true'; };
  }
  if (typeof dialog.close !== 'function') {
    dialog.close = function close(this: HTMLDialogElement) {
      if (!this.hasAttribute('open')) return;
      this.removeAttribute('open'); delete this.dataset.modal;
      this.dispatchEvent(new Event('close'));
    };
  }
}
if (typeof HTMLElement !== 'undefined' && typeof HTMLElement.prototype.showPopover !== 'function') {
  HTMLElement.prototype.showPopover = function showPopover(this: HTMLElement) { this.dataset.popoverOpen = 'true'; this.dispatchEvent(new Event('toggle')); };
  HTMLElement.prototype.hidePopover = function hidePopover(this: HTMLElement) { delete this.dataset.popoverOpen; this.dispatchEvent(new Event('toggle')); };
  HTMLElement.prototype.togglePopover = function togglePopover(this: HTMLElement) {
    if (this.dataset.popoverOpen) this.hidePopover(); else this.showPopover();
    return Boolean(this.dataset.popoverOpen);
  };
}
// jsdom 29's UA sheet hides `[popover]:not(:popover-open)` and never matches :popover-open, so a shown popover would stay
// display:none and out of the accessibility tree; the shim's data flag shows it (!important: jsdom ranks the UA rule by
// specificity, not origin).
if (typeof document !== 'undefined') {
  const shown = document.createElement('style');
  shown.textContent = '[popover][data-popover-open="true"] { display: block !important; }';
  document.head.append(shown);
}
// jsdom has no CSS.escape; user-event's arrow-key walk through a named radio group (SegmentedControl) needs it.
if (typeof window !== 'undefined' && typeof window.CSS?.escape !== 'function') {
  const escape = (value: string) => value.replace(/[^a-zA-Z0-9_ -￿-]/g, (char) => `\\${char}`);
  Object.defineProperty(window, 'CSS', { configurable: true, value: { ...window.CSS, escape } });
}
