// Named popoverOpen.ts, not popover.ts: on a case-insensitive disk "./Popover" would resolve to popover.ts.
/** True when a [popover] element is shown; jsdom has no :popover-open, so the test shim's data flag counts too. */
export function isPopoverOpen(element: HTMLElement): boolean {
  try { if (element.matches(':popover-open')) return true; } catch { /* selector unsupported */ }
  return element.dataset.popoverOpen === 'true';
}
