import { type ReactElement, type ReactNode, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { cx } from './cx';
import type { MenuTriggerProps } from './Menu';
import { place } from './placement';
import { isPopoverOpen } from './popoverOpen';

export interface PopoverProps {
  trigger: (props: Omit<MenuTriggerProps, 'aria-haspopup'> & { 'aria-haspopup': 'dialog' }) => ReactElement;
  label: string;
  children: ReactNode;
  align?: 'start' | 'end';
  openOnHover?: boolean;
}

export function Popover({ trigger, label, children, align = 'start', openOnHover = false }: PopoverProps) {
  const [open, setOpen] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const leave = useRef<number | undefined>(undefined);
  const id = useId();
  const show = () => { window.clearTimeout(leave.current); setOpen(true); };
  const hideSoon = () => { window.clearTimeout(leave.current); leave.current = window.setTimeout(() => setOpen(false), 150); };

  useLayoutEffect(() => {
    const element = panel.current;
    const anchor = triggerRef.current;
    if (!open || !element || !anchor) return undefined;
    element.showPopover?.();
    place(element, anchor, align);
    const onToggle = (event: Event) => { if ((event as ToggleEvent).newState === 'closed' || !isPopoverOpen(element)) setOpen(false); };
    const onKey = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') { event.preventDefault(); setOpen(false); anchor.focus(); } };
    // Light dismiss (the panel is popover="manual"): a press or a focus move (click, Tab, script) outside the trigger and
    // the panel closes it. A document listener, not blur, so a click on nothing focusable or a Safari click that never
    // focused the trigger still closes it.
    const onOutside = (event: Event) => {
      const target = event.target as Node | null;
      if (target && (element.contains(target) || anchor.parentElement?.contains(target))) return;
      setOpen(false);
    };
    // Re-anchored when anything scrolls or resizes, as Menu is: the fixed panel must not stay put while its trigger moves.
    const follow = () => place(element, anchor, align);
    element.addEventListener('toggle', onToggle);
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onOutside, true);
    document.addEventListener('focusin', onOutside, true);
    window.addEventListener('resize', follow);
    document.addEventListener('scroll', follow, true);
    return () => {
      element.removeEventListener('toggle', onToggle);
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onOutside, true);
      document.removeEventListener('focusin', onOutside, true);
      window.removeEventListener('resize', follow);
      document.removeEventListener('scroll', follow, true);
    };
  }, [open, align]);
  useEffect(() => () => window.clearTimeout(leave.current), []);

  const hover = openOnHover ? { onPointerEnter: show, onPointerLeave: hideSoon } : {};
  const triggerProps = { ref: triggerRef, id: `${id}trigger`, 'aria-haspopup': 'dialog' as const, 'aria-expanded': open, 'aria-controls': `${id}panel`, onClick: () => (openOnHover ? show() : setOpen((value) => !value)), onKeyDown: () => undefined };
  return (
    <>
      <span className="g-popover-anchor" onBlur={openOnHover ? hideSoon : undefined} onFocus={openOnHover ? show : undefined} {...hover}>
        {trigger(triggerProps)}
      </span>
      {open ? <div aria-label={label} className={cx('g-popover', `is-${align}`)} id={`${id}panel`} popover="manual" ref={panel} role="dialog" {...hover}>{children}</div> : null}
    </>
  );
}
