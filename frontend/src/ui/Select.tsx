import { ChevronDown } from './icons';
import { type ChangeEvent, type KeyboardEvent, type Ref, type SelectHTMLAttributes, useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { cx } from './cx';
import { place } from './placement';
import { isPopoverOpen } from './popoverOpen';

type Option = { index: number; value: string; label: string; disabled: boolean; group?: string };
type Snapshot = { options: Option[]; selectedIndex: number };

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'ref'> {
  /** The visible combobox is the interactive control; the native select remains an internal form bridge. */
  ref?: Ref<HTMLButtonElement>;
}

const sameSnapshot = (left: Snapshot, right: Snapshot) => left.selectedIndex === right.selectedIndex
  && left.options.length === right.options.length
  && left.options.every((option, index) => {
    const other = right.options[index];
    return option.value === other.value && option.label === other.label && option.disabled === other.disabled && option.group === other.group;
  });

const grouped = (options: Option[]) => options.reduce<Array<{ label?: string; options: Option[] }>>((groups, option) => {
  const last = groups[groups.length - 1];
  if (!last || last.label !== option.group) groups.push({ label: option.group, options: [option] });
  else last.options.push(option);
  return groups;
}, []);

export function Select({
  'aria-describedby': describedBy,
  'aria-invalid': invalid,
  'aria-label': ariaLabel,
  'aria-labelledby': labelledBy,
  autoFocus,
  children,
  className,
  defaultValue,
  disabled = false,
  form,
  id,
  name,
  onChange,
  onInvalid,
  ref,
  required,
  tabIndex,
  title,
  value,
  ...nativeProps
}: SelectProps) {
  const generatedId = useId();
  const controlId = id ?? `${generatedId}select`;
  const listboxId = `${controlId}-listbox`;
  const nativeRef = useRef<HTMLSelectElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const typed = useRef({ text: '', at: 0 });
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [validationInvalid, setValidationInvalid] = useState(false);
  const [snapshot, setSnapshot] = useState<Snapshot>({ options: [], selectedIndex: -1 });

  const setTriggerRef = useCallback((node: HTMLButtonElement | null) => {
    triggerRef.current = node;
    if (typeof ref === 'function') ref(node);
    else if (ref) ref.current = node;
  }, [ref]);

  const syncFromNative = useCallback(() => {
    const select = nativeRef.current;
    if (!select) return;
    const options = Array.from(select.options).map<Option>((option, index) => {
      const parent = option.parentElement;
      const group = parent instanceof HTMLOptGroupElement ? parent : null;
      return { index, value: option.value, label: option.label, disabled: option.disabled || Boolean(group?.disabled), group: group?.label };
    });
    const next = { options, selectedIndex: select.selectedIndex };
    setSnapshot((current) => sameSnapshot(current, next) ? current : next);
  }, []);

  useLayoutEffect(syncFromNative, [children, syncFromNative, value]);

  useEffect(() => {
    const formElement = nativeRef.current?.form;
    if (!formElement) return undefined;
    let timer: number | undefined;
    const reset = () => { timer = window.setTimeout(syncFromNative); };
    formElement.addEventListener('reset', reset);
    return () => { window.clearTimeout(timer); formElement.removeEventListener('reset', reset); };
  }, [form, syncFromNative]);

  const controlledIndex = value === undefined ? -1 : snapshot.options.findIndex((option) => option.value === String(value));
  const selectedIndex = controlledIndex >= 0 ? controlledIndex : snapshot.selectedIndex;
  const selected = snapshot.options[selectedIndex];
  const enabled = snapshot.options.filter((option) => !option.disabled);
  const optionId = (index: number) => `${listboxId}-option-${index}`;

  const openList = () => {
    if (disabled || !enabled.length) return;
    const first = selected && !selected.disabled ? selected.index : enabled[0].index;
    setActiveIndex(first);
    setOpen(true);
  };
  const closeList = () => setOpen(false);

  const choose = (index: number) => {
    const option = snapshot.options[index];
    const select = nativeRef.current;
    if (!option || option.disabled || !select) return;
    select.selectedIndex = option.index;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    syncFromNative();
    closeList();
    triggerRef.current?.focus();
  };

  const move = (by: number) => {
    if (!enabled.length) return;
    const current = enabled.findIndex((option) => option.index === activeIndex);
    const start = current < 0 ? 0 : current;
    const next = (start + by + enabled.length) % enabled.length;
    setActiveIndex(enabled[next].index);
  };

  const typeAhead = (key: string) => {
    const now = Date.now();
    const text = now - typed.current.at < 500 ? typed.current.text + key.toLowerCase() : key.toLowerCase();
    typed.current = { text, at: now };
    const probe = /^(.)\1+$/.test(text) ? text[0] : text;
    const current = enabled.findIndex((option) => option.index === (open ? activeIndex : selectedIndex));
    const start = probe.length === 1 ? current + 1 : Math.max(current, 0);
    const ordered = [...enabled.slice(start), ...enabled.slice(0, start)];
    const match = ordered.find((option) => option.label.trim().toLowerCase().startsWith(probe));
    if (!match) return;
    if (open) setActiveIndex(match.index);
    else choose(match.index);
  };

  function onTriggerKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const handled = () => { event.preventDefault(); event.stopPropagation(); };
    if (event.key === 'Tab') { closeList(); return; }
    if (event.key === 'Escape' && open) { handled(); closeList(); return; }
    if (event.key === 'Enter' || event.key === ' ') {
      handled();
      if (open) choose(activeIndex);
      else openList();
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      handled();
      if (!open) openList();
      else move(event.key === 'ArrowDown' ? 1 : -1);
      return;
    }
    if (event.key === 'Home' || event.key === 'End') {
      handled();
      if (!open) openList();
      setActiveIndex((event.key === 'Home' ? enabled[0] : enabled[enabled.length - 1])?.index ?? -1);
      return;
    }
    if (event.key.length === 1 && /\S/.test(event.key) && !event.metaKey && !event.ctrlKey && !event.altKey) {
      handled();
      typeAhead(event.key);
    }
  }

  useLayoutEffect(() => {
    const list = listRef.current;
    const anchor = triggerRef.current;
    if (!open || !list || !anchor) return undefined;
    if (!isPopoverOpen(list)) list.showPopover?.();
    const follow = () => {
      const box = anchor.getBoundingClientRect();
      list.style.minWidth = `${Math.min(box.width, window.innerWidth - 16)}px`;
      list.style.maxHeight = `${Math.max(44, Math.min(360, Math.max(box.top - 12, window.innerHeight - box.bottom - 12)))}px`;
      place(list, anchor, 'start');
    };
    follow();
    const active = document.getElementById(optionId(activeIndex));
    active?.scrollIntoView?.({ block: 'nearest' });
    const outside = (event: Event) => {
      const target = event.target as Node | null;
      if (target && (list.contains(target) || anchor.contains(target))) return;
      setOpen(false);
    };
    const toggled = () => { if (!isPopoverOpen(list)) setOpen(false); };
    list.addEventListener('toggle', toggled);
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('focusin', outside, true);
    window.addEventListener('resize', follow);
    document.addEventListener('scroll', follow, true);
    return () => {
      list.removeEventListener('toggle', toggled);
      document.removeEventListener('pointerdown', outside, true);
      document.removeEventListener('focusin', outside, true);
      window.removeEventListener('resize', follow);
      document.removeEventListener('scroll', follow, true);
    };
  }, [activeIndex, open, snapshot]);

  const changed = (event: ChangeEvent<HTMLSelectElement>) => {
    setValidationInvalid(!event.currentTarget.validity.valid);
    syncFromNative();
    onChange?.(event);
  };

  return (
    <span className={cx('g-select', open && 'is-open', className)}>
      <button
        aria-activedescendant={open && activeIndex >= 0 ? optionId(activeIndex) : undefined}
        aria-controls={listboxId}
        aria-describedby={describedBy}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-invalid={invalid ?? (validationInvalid || undefined)}
        aria-label={ariaLabel}
        aria-labelledby={labelledBy}
        aria-required={required || undefined}
        autoFocus={autoFocus}
        className="g-select-trigger"
        disabled={disabled}
        id={controlId}
        onClick={() => { triggerRef.current?.focus(); if (open) closeList(); else openList(); }}
        onKeyDown={onTriggerKeyDown}
        ref={setTriggerRef}
        role="combobox"
        tabIndex={tabIndex}
        title={title}
        type="button"
      >
        <span className="g-select-value">{selected?.label ?? ''}</span>
        <ChevronDown aria-hidden="true" size={16} />
      </button>
      <select
        {...nativeProps}
        aria-hidden="true"
        className="g-select-native"
        defaultValue={defaultValue}
        disabled={disabled}
        form={form}
        hidden
        name={name}
        onChange={changed}
        onFocus={() => { if (!disabled) triggerRef.current?.focus(); }}
        onInvalid={(event) => {
          onInvalid?.(event);
          setValidationInvalid(true);
          if (!event.defaultPrevented) event.preventDefault();
          triggerRef.current?.focus();
        }}
        ref={nativeRef}
        required={required}
        tabIndex={-1}
        value={value}
      >
        {children}
      </select>
      {open ? (
        <div
          aria-label={ariaLabel}
          aria-labelledby={labelledBy ?? (ariaLabel ? undefined : controlId)}
          className="g-select-listbox"
          id={listboxId}
          onKeyDown={(event) => {
            if (event.key !== 'Escape') return;
            event.preventDefault();
            event.stopPropagation();
            closeList();
            triggerRef.current?.focus();
          }}
          popover="manual"
          ref={listRef}
          role="listbox"
        >
          {grouped(snapshot.options).map((group, groupIndex) => (
            <div aria-label={group.label} className="g-select-group" key={`${group.label ?? 'options'}-${groupIndex}`} role={group.label ? 'group' : 'presentation'}>
              {group.label ? <div aria-hidden="true" className="g-select-group-label g-label">{group.label}</div> : null}
              {group.options.map((option) => (
                <div
                  aria-disabled={option.disabled || undefined}
                  aria-selected={option.index === selectedIndex}
                  className={cx('g-select-option', option.index === selectedIndex && 'is-selected', option.index === activeIndex && 'is-active')}
                  id={optionId(option.index)}
                  key={`${option.value}-${option.index}`}
                  onClick={(event) => { event.preventDefault(); choose(option.index); }}
                  onPointerDown={(event) => event.preventDefault()}
                  onPointerMove={() => { if (!option.disabled) setActiveIndex(option.index); }}
                  role="option"
                >
                  {option.label}
                </div>
              ))}
            </div>
          ))}
        </div>
      ) : null}
    </span>
  );
}
