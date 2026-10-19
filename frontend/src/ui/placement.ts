/** Places a top-layer panel under (or, without room, over) its trigger, aligned to its start or end edge. */
export function place(panel: HTMLElement, trigger: HTMLElement, align: 'start' | 'end'): void {
  const anchor = trigger.getBoundingClientRect();
  const gap = 4;
  panel.style.position = 'fixed';
  panel.style.inset = 'auto';
  panel.style.margin = '0';
  // WebKit resolves a fit-content panel's width from its current fixed position.
  panel.style.left = '8px';
  const size = panel.getBoundingClientRect();
  const below = anchor.bottom + gap + size.height <= window.innerHeight - 8;
  const above = anchor.top - gap - size.height >= 8;
  const side = below || !above ? 'bottom' : 'top';
  const left = align === 'end' ? anchor.right - size.width : anchor.left;
  const top = side === 'bottom' ? anchor.bottom + gap : anchor.top - gap - size.height;
  panel.style.top = `${Math.max(8, Math.min(top, window.innerHeight - size.height - 8))}px`;
  panel.style.left = `${Math.max(8, Math.min(left, window.innerWidth - size.width - 8))}px`;
  panel.dataset.side = side;
}
