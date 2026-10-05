import { type KeyboardEvent, useEffect, useId, useRef, useState } from 'react';
import { Info } from 'lucide-react';

/** Closes whichever explanation is open, so only one shows at a time. */
let closeOpen: (() => void) | null = null;
/** Esc on keyboards; TV remotes send GoBack or BrowserBack for Back. */
const CLOSE_KEYS = new Set(['Escape', 'GoBack', 'BrowserBack']);

/**
 * The ⓘ beside a setting: a real button that shows 2–3 plain sentences under the label.
 * The text stays in the DOM (hidden) so aria-controls always resolves. No hover behaviour.
 */
export function InfoButton({ label, text, id }: { label: string; text: string; id?: string }) {
  const generated = useId();
  const popoverId = id ?? `info-${generated.replace(/:/g, '')}`;
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const wrapRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const close = () => setOpen(false);
    closeOpen = close;
    // Selecting outside closes it; the tapped control keeps focus.
    const onPointerDown = (event: Event) => { if (!wrapRef.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener('pointerdown', onPointerDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      if (closeOpen === close) closeOpen = null;
    };
  }, [open]);

  function toggle() {
    if (open) { setOpen(false); return; }
    closeOpen?.();
    setOpen(true);
  }

  function onKeyDown(event: KeyboardEvent<HTMLSpanElement>) {
    if (!open || !CLOSE_KEYS.has(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    setOpen(false);
    buttonRef.current?.focus();
  }

  return (
    <span className="g-info" onKeyDown={onKeyDown} ref={wrapRef}>
      <button aria-controls={popoverId} aria-describedby={open ? popoverId : undefined} aria-expanded={open} aria-label={`About ${label}`} className="g-icon-button is-plain g-info-button" data-focus-item onClick={toggle} ref={buttonRef} type="button">
        <Info aria-hidden="true" />
      </button>
      <span className="g-popover g-info-note" hidden={!open} id={popoverId} role="note">{text}</span>
    </span>
  );
}
