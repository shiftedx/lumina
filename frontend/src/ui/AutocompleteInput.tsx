import { type InputHTMLAttributes, type KeyboardEvent, type Ref, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { Input } from './Field';
import { isPopoverOpen } from './popoverOpen';
import { place } from './placement';
import './autocomplete.css';

export type AutocompleteOption = string | { value: string; label: string };

/** An editable Lumina choice: suggestions never prevent a member entering a new value. */
export function AutocompleteInput({ options, ref, onChange, onKeyDown, onFocus, onBlur, disabled, ...props }: Omit<InputHTMLAttributes<HTMLInputElement>, 'list'> & { options: readonly AutocompleteOption[]; ref?: Ref<HTMLInputElement> }) {
  const input = useRef<HTMLInputElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(String(props.value ?? props.defaultValue ?? ''));
  const [active, setActive] = useState(-1);
  const choices = [...new Map(options.map((option) => { const entry = typeof option === 'string' ? { value: option, label: option } : option; return [entry.value, entry] as const; })).values()].filter((option) => `${option.label} ${option.value}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  const shown = open && !disabled && choices.length > 0;
  const activeIndex = active < choices.length ? active : -1;

  useEffect(() => { if (props.value !== undefined) setQuery(String(props.value)); }, [props.value]);
  useLayoutEffect(() => {
    const list = panel.current, anchor = input.current;
    if (!shown || !list || !anchor) return;
    if (!isPopoverOpen(list)) list.showPopover?.();
    const follow = () => {
      const box = anchor.getBoundingClientRect();
      list.style.minWidth = `${Math.min(box.width, innerWidth - 16)}px`;
      list.style.maxHeight = `${Math.max(44, Math.min(280, Math.max(box.top - 12, innerHeight - box.bottom - 12)))}px`;
      place(list, anchor, 'start');
    };
    follow();
    window.addEventListener('resize', follow);
    document.addEventListener('scroll', follow, true);
    const outside = (event: Event) => {
      if (!list.contains(event.target as Node) && event.target !== anchor) setOpen(false);
    };
    document.addEventListener('pointerdown', outside, true);
    return () => {
      window.removeEventListener('resize', follow);
      document.removeEventListener('scroll', follow, true);
      document.removeEventListener('pointerdown', outside, true);
    };
  }, [shown, choices.length]);
  useLayoutEffect(() => { if (activeIndex >= 0) panel.current?.querySelector(`[data-choice="${activeIndex}"]`)?.scrollIntoView?.({ block: 'nearest' }); }, [activeIndex]);

  function choose(value: string) {
    const control = input.current;
    if (!control) return;
    // A real input event preserves every existing controlled-input handler and form contract.
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(control, value);
    control.dispatchEvent(new Event('input', { bubbles: true }));
    control.focus(); setQuery(value); setActive(-1); setOpen(false);
  }
  function keyDown(event: KeyboardEvent<HTMLInputElement>) {
    const handled = () => { event.preventDefault(); event.stopPropagation(); };
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && choices.length) {
      handled(); setOpen(true);
      setActive((previous) => event.key === 'ArrowDown' ? (previous + 1) % choices.length : previous <= 0 ? choices.length - 1 : previous - 1);
    } else if (shown && activeIndex >= 0 && (event.key === 'Home' || event.key === 'End')) {
      handled(); setActive(event.key === 'Home' ? 0 : choices.length - 1);
    } else if (shown && activeIndex >= 0 && event.key === 'Enter') {
      handled(); choose(choices[activeIndex].value);
    } else if (shown && event.key === 'Escape') {
      handled(); setOpen(false); setActive(-1);
    } else {
      if (event.key === 'Tab') setOpen(false);
      onKeyDown?.(event);
    }
  }
  return <span className="g-autocomplete">
    <Input {...props} aria-activedescendant={shown && activeIndex >= 0 ? `${id}-${activeIndex}` : undefined} aria-autocomplete="list" aria-controls={`${id}-list`} aria-expanded={shown} aria-haspopup="listbox" autoComplete="off" disabled={disabled}
      onBlur={(event) => { setOpen(false); setActive(-1); onBlur?.(event); }}
      onChange={(event) => { setQuery(event.currentTarget.value); setActive(-1); setOpen(true); onChange?.(event); }}
      onFocus={(event) => { setOpen(true); onFocus?.(event); }} onKeyDown={keyDown}
      ref={(element) => { input.current = element; if (typeof ref === 'function') ref(element); else if (ref) ref.current = element; }} role="combobox" />
    {shown ? <div aria-label="Suggestions" className="g-select-listbox g-autocomplete-list" id={`${id}-list`} popover="manual" ref={panel} role="listbox">
      {choices.map((option, index) => <div aria-selected={index === activeIndex} className={`g-select-option${index === activeIndex ? ' is-active' : ''}`} data-choice={index} id={`${id}-${index}`} key={option.value} onClick={(event) => { event.preventDefault(); choose(option.value); }} onPointerDown={(event) => event.preventDefault()} role="option">{option.label}</div>)}
    </div> : null}
  </span>;
}
