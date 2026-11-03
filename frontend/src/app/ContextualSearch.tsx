import { type ReactNode, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Search, X } from '../luminaIcons';

/** Keep each surface's real query state and form in the shared header position. */
export function ContextualSearch({ children, label, fallback = children }: { children: ReactNode; label: string; fallback?: ReactNode }) {
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const field = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => { setSlot(document.getElementById('g-header-search')); }, []);
  if (!slot) return fallback;
  return createPortal(<div className={`g-context-search${open ? ' is-open' : ''}`} onKeyDown={(event) => {
    if (event.key === 'Escape' && open) { setOpen(false); requestAnimationFrame(() => trigger.current?.focus()); }
  }}>
    <button aria-label={label} className="g-search-trigger g-context-search-open" onClick={() => { setOpen(true); requestAnimationFrame(() => field.current?.querySelector<HTMLInputElement>('input')?.focus()); }} ref={trigger} type="button"><Search aria-hidden="true" /></button>
    <div className="g-context-search-field" ref={field}>{children}</div>
    {open ? <button aria-label="Close search" className="g-icon-button g-context-search-close" onClick={() => { setOpen(false); requestAnimationFrame(() => trigger.current?.focus()); }} type="button"><X aria-hidden="true" /></button> : null}
  </div>, slot);
}
