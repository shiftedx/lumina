/** Places a top-layer panel under (or, without room, over) its trigger, aligned to its start or end edge. */
export function place(panel: HTMLElement, trigger: HTMLElement, align: 'start' | 'end'): void {
  const anchor = trigger.getBoundingClientRect();
  const size = panel.getBoundingClientRect();
  const gap = 4;
  const below = anchor.bottom + gap + size.height <= window.innerHeight - 8;
  const above = anchor.top - gap - size.height >= 8;
  const side = below || !above ? 'bottom' : 'top';
  const left = align === 'end' ? anchor.right - size.width : anchor.left;
  panel.style.position = 'fixed';
  panel.style.inset = 'auto';
  panel.style.margin = '0';
  panel.style.top = `${side === 'bottom' ? anchor.bottom + gap : anchor.top - gap - size.height}px`;
  panel.style.left = `${Math.max(8, Math.min(left, window.innerWidth - size.width - 8))}px`;
  panel.dataset.side = side;
}
