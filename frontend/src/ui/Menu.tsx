import { Check } from 'lucide-react';
import { type FocusEvent, type KeyboardEvent, type ReactElement, type ReactNode, type Ref, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { cx } from './cx';
import { place } from './placement';
import { isPopoverOpen } from './popoverOpen';

export type MenuEntry =
  | { kind: 'item'; label: string; icon?: ReactNode; onSelect?: () => void; danger?: boolean; disabled?: boolean; href?: string; hint?: string; detail?: string }
  | { kind: 'radio'; label: string; checked: boolean; onSelect: () => void; hint?: string }
  | { kind: 'separator' }
  | { kind: 'group'; label: string };
export interface MenuTriggerProps {
  ref: Ref<HTMLButtonElement>;
  id: string;
  'aria-haspopup': 'menu';
  'aria-expanded': boolean;
  'aria-controls': string;
  onClick: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
}
export interface MenuProps {
  trigger: (props: MenuTriggerProps) => ReactElement;
  items: readonly MenuEntry[];
  align?: 'start' | 'end';
  label?: string;
  variant?: 'paper' | 'overlay';
  /** Default true: ArrowDown/ArrowUp on the trigger open the menu (APG). A trigger inside a surface's own arrow/TV-remote grid (the recommendation `…` button) passes false, so it stays a plain focus stop that Enter or OK opens. */
  arrowOpens?: boolean;
}

type Section = { label?: string; entries: MenuEntry[] };
export function sections(items: readonly MenuEntry[]): Section[] {
  return items.reduce<Section[]>((out, entry) => {
    if (entry.kind === 'group') out.push({ label: entry.label, entries: [] });
    else out[out.length - 1].entries.push(entry);
    return out;
  }, [{ entries: [] }]).filter((section) => section.label || section.entries.length);
}

export function Menu({ trigger, items, align = 'start', label, variant = 'paper', arrowOpens = true }: MenuProps) {
  const [open, setOpen] = useState(false);
  const [focusOn, setFocusOn] = useState<'first' | 'last'>('first');
  const [expanded, setExpanded] = useState<string | null>(null); // the one `detail` item that is showing its text
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const typed = useRef({ text: '', at: 0 });
  const trigDown = useRef(false); // a pointer is down on the trigger: its click, not the blur, decides open or closed
  const id = useId();

  const rows = () => Array.from(listRef.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? []);
  const close = (refocus: boolean) => { setOpen(false); setExpanded(null); if (refocus) triggerRef.current?.focus(); };
  const openAt = (where: 'first' | 'last') => { setFocusOn(where); setOpen(true); };

  // Placement: under (or over) the trigger, 8px inside both viewport edges, re-anchored when anything scrolls or resizes
  // (the recommendation menu sits in horizontal rails). The panel is a top-layer popover, so no overflow ancestor clips it.
  useLayoutEffect(() => {
    const list = listRef.current;
    const anchor = triggerRef.current;
    if (!open || !list || !anchor) return undefined;
    if (!isPopoverOpen(list)) list.showPopover?.();
    place(list, anchor, align);
    const follow = () => place(list, anchor, align);
    window.addEventListener('resize', follow);
    document.addEventListener('scroll', follow, true);
    return () => { window.removeEventListener('resize', follow); document.removeEventListener('scroll', follow, true); };
  }, [open, align, expanded]);
  useLayoutEffect(() => {
    if (!open) return;
    const all = rows();
    (focusOn === 'first' ? all[0] : all[all.length - 1])?.focus({ preventScroll: true });
  }, [open, focusOn]);

  // Safari does not focus a button on click, so the blur of the focused item has relatedTarget null; note the press first (capture) so onMenuBlur can leave the trigger's click to its own onClick.
  useEffect(() => {
    const down = (event: Event) => { trigDown.current = Boolean(triggerRef.current?.contains(event.target as Node)); };
    // A press released off the trigger never clicks it, and WebKit already blurred the item at mousedown (skipped above), so close here when focus is not in the list.
    const up = (event: Event) => {
      const pressedTrigger = trigDown.current;
      trigDown.current = false;
      if (pressedTrigger && !triggerRef.current?.contains(event.target as Node) && !listRef.current?.contains(document.activeElement)) close(false);
    };
    document.addEventListener('pointerdown', down, true);
    document.addEventListener('pointerup', up, true);
    document.addEventListener('pointercancel', up, true);
    return () => { document.removeEventListener('pointerdown', down, true); document.removeEventListener('pointerup', up, true); document.removeEventListener('pointercancel', up, true); };
  }, []);

  // Enter and Space reach onClick natively (Enter on keydown, Space on keyup), so only the arrows are handled here.
  function onTriggerKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (!arrowOpens) return; // a trigger inside a surface's own arrow / TV-remote grid is a plain focus stop
    if (event.key === 'ArrowDown') { event.preventDefault(); openAt('first'); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); openAt('last'); }
  }

  function onMenuKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const all = rows();
    const index = all.indexOf(document.activeElement as HTMLElement);
    // Every key handled here stops here: the menu renders inside its surface's row, and the surface's own key handlers
    // (grid navigation, shortcuts) must never move focus away from an open menu.
    const handled = () => { event.preventDefault(); event.stopPropagation(); };
    const move = (next: number) => { handled(); all[(next + all.length) % all.length]?.focus(); };
    if (event.key === 'ArrowDown') move(index + 1);
    else if (event.key === 'ArrowUp') move(index - 1);
    else if (event.key === 'Home') move(0);
    else if (event.key === 'End') move(all.length - 1);
    else if (event.key === 'Escape') { handled(); close(true); }
    // Tab: close and put focus on the trigger, and do NOT prevent the key: the browser's own Tab then goes on from the
    // trigger to the next stop, so the menu never sends focus out of a rail or off the page (shipped RecoMenu behaviour).
    else if (event.key === 'Tab') close(true);
    else if (event.key === ' ' && (document.activeElement as HTMLElement | null)?.tagName === 'A') { handled(); (document.activeElement as HTMLElement).click(); }
    else if (event.key.length === 1 && /\S/.test(event.key) && !event.metaKey && !event.ctrlKey) {
      event.stopPropagation(); // typing letters must not trigger the surface's shortcuts
      const now = Date.now();
      const text = now - typed.current.at < 500 ? typed.current.text + event.key.toLowerCase() : event.key.toLowerCase();
      typed.current = { text, at: now };
      // Repeating one letter cycles through the rows that start with it (APG menu type-ahead).
      const probe = /^(.)\1+$/.test(text) ? text[0] : text;
      const start = probe.length === 1 ? index + 1 : index;
      const ordered = [...all.slice(start), ...all.slice(0, start)];
      ordered.find((row) => row.textContent?.trim().toLowerCase().startsWith(probe))?.focus();
    }
  }

  // Focus leaving the menu closes it: a click elsewhere. Tab refocuses the trigger first, so it is not a leave. A click on the trigger is left to its own onClick.
  const onMenuBlur = (event: FocusEvent<HTMLDivElement>) => {
    const next = event.relatedTarget as Node | null;
    if (trigDown.current) return;
    if (next && (event.currentTarget.contains(next) || next === triggerRef.current)) return;
    close(false);
  };

  const choose = (onSelect?: () => void) => { close(true); onSelect?.(); };
  return (
    <>
      {trigger({ ref: triggerRef, id: `${id}trigger`, 'aria-haspopup': 'menu', 'aria-expanded': open, 'aria-controls': `${id}menu`, onClick: () => { trigDown.current = false; if (open) close(false); else openAt('first'); }, onKeyDown: onTriggerKeyDown })}
      {open ? (
        <div aria-label={label} aria-labelledby={label ? undefined : `${id}trigger`} className={cx('g-menu', `is-${variant}`, `is-${align}`)} id={`${id}menu`} onBlur={onMenuBlur} onKeyDown={onMenuKeyDown} popover="manual" ref={listRef} role="menu">
          {sections(items).map((section, index) => {
            const rowsOf = section.entries.map((entry, row) => {
              if (entry.kind === 'separator') return <div className="g-menu-separator" key={`s${row}`} role="separator" />;
              if (entry.kind === 'radio') {
                return <button aria-checked={entry.checked} className="g-menu-item" key={entry.label} onClick={() => choose(entry.onSelect)} role="menuitemradio" tabIndex={-1} type="button"><span aria-hidden="true" className="g-menu-check">{entry.checked ? <Check /> : null}</span>{entry.label}{entry.hint ? <span className="g-menu-hint">{entry.hint}</span> : null}</button>;
              }
              if (entry.kind !== 'item') return null;
              const content = <>{entry.icon ? <span aria-hidden="true" className="g-menu-icon">{entry.icon}</span> : null}{entry.label}{entry.hint ? <span className="g-menu-hint">{entry.hint}</span> : null}{entry.detail && expanded === entry.label ? <span className="g-menu-detail">{entry.detail}</span> : null}</>;
              if (entry.href) {
                return <a aria-disabled={entry.disabled || undefined} className={cx('g-menu-item', entry.danger && 'is-danger')} href={entry.href} key={entry.label} onClick={(event) => { if (entry.disabled) event.preventDefault(); else close(false); }} role="menuitem" tabIndex={-1}>{content}</a>;
              }
              // A `detail` item is a disclosure: it toggles its text under the label and never closes the menu or selects.
              const onClick = () => {
                if (entry.disabled) return;
                if (entry.detail) setExpanded((current) => (current === entry.label ? null : entry.label));
                else choose(entry.onSelect);
              };
              return <button aria-disabled={entry.disabled || undefined} aria-expanded={entry.detail ? expanded === entry.label : undefined} className={cx('g-menu-item', entry.danger && 'is-danger')} key={entry.label} onClick={onClick} role="menuitem" tabIndex={-1} type="button">{content}</button>;
            });
            return section.label
              ? <div aria-label={section.label} className="g-menu-group" key={section.label} role="group"><div aria-hidden="true" className="g-menu-group-label g-label">{section.label}</div>{rowsOf}</div>
              : <div className="g-menu-group" key={`g${index}`} role="none">{rowsOf}</div>;
          })}
        </div>
      ) : null}
    </>
  );
}
