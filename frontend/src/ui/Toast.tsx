import { X } from 'lucide-react';
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { TextButton } from './Button';
import { IconButton } from './IconButton';
import { cx } from './cx';
import { clampServerText } from './text';

export type ToastTone = 'success' | 'info' | 'error';
export interface ToastInput { tone: ToastTone; message: string; action?: { label: string; onAction: () => void } }
/** The in-flow sibling of a toast: one line that replaces a card for a while with one action (the recommender's "Hidden. Undo"). Absorbs `features/reco/UndoRow.tsx`. */
export interface InlineNoticeProps {
  message: string;
  action: { label: string; onAction: () => void };
  /** Called once when the time is up and the action was not taken; focus has been handed to the neighbouring `[data-focus-item]` first. */
  onExpire: () => void;
  timeoutMs?: number;
  /** Default true: focus moves to the action when the notice appears. */
  focusAction?: boolean;
}
export const INLINE_NOTICE_MS = 8_000;

/** `push` returns the toast's id; `remove(id)` takes one down; `clear()` drops every toast at once (a member switch or sign-out, so one member's Undo never reaches the next). */
export type Toaster = ((toast: ToastInput) => number) & { remove: (id: number) => void; clear: () => void };
const ToastContext = createContext<Toaster | null>(null);
const noop = Object.assign(() => 0, { remove: () => undefined, clear: () => undefined }) as Toaster;
let sequence = 0;

/** A no-op outside a provider, so isolated component tests need no wrapper. */
export function useToast(): Toaster {
  return useContext(ToastContext) ?? noop;
}

/** Focus the card control after the row (else before it) when focus is inside the row, which is about to go. */
function handFocusOn(row: HTMLElement | null): void {
  if (!row || !row.contains(document.activeElement)) return;
  const scope = row.closest('[data-focus-row]') ?? row.parentElement;
  const targets = Array.from(scope?.querySelectorAll<HTMLElement>('[data-focus-item]') ?? []).filter((element) => !row.contains(element));
  const after = targets.find((element) => row.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING);
  (after ?? targets[targets.length - 1])?.focus();
}

export function InlineNotice({ message, action, onExpire, timeoutMs = INLINE_NOTICE_MS, focusAction = true }: InlineNoticeProps) {
  const row = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const expire = useRef(onExpire);
  const closed = useRef(false);
  const left = useRef(timeoutMs);
  const arriving = useRef(false);
  const [paused, setPaused] = useState(false);
  expire.current = onExpire;
  useEffect(() => {
    if (!focusAction) return;
    arriving.current = true; // the arrival focus is ours, not the user's: it must not pause the timer
    button.current?.focus();
    arriving.current = false;
  }, [focusAction]);
  useEffect(() => {
    if (paused) return undefined;
    const started = Date.now();
    const timer = window.setTimeout(() => {
      if (closed.current) return;
      closed.current = true;
      handFocusOn(row.current);
      expire.current();
    }, left.current);
    return () => { window.clearTimeout(timer); left.current -= Date.now() - started; };
  }, [paused]);
  return (
    <div
      className="g-notice"
      onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setPaused(false); }}
      onFocus={() => { if (!arriving.current) setPaused(true); }}
      onPointerEnter={() => setPaused(true)}
      onPointerLeave={() => setPaused(false)}
      ref={row}
      role="status"
    >
      <span>{message}</span>{' '}
      <TextButton data-focus-item onClick={() => { closed.current = true; action.onAction(); }} ref={button}>{action.label}</TextButton>
    </div>
  );
}

const LIFETIME_MS = 5000;
const LEAVE_MS = 200;
const MAX = 3;
type Shown = ToastInput & { id: number; leaving: boolean };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Shown[]>([]);
  const timers = useRef(new Map<number, { handle?: number; startedAt: number; left: number }>());

  const remove = useCallback((id: number) => {
    const timer = timers.current.get(id);
    window.clearTimeout(timer?.handle);
    timers.current.delete(id);
    setToasts((list) => list.map((toast) => (toast.id === id ? { ...toast, leaving: true } : toast)));
    window.setTimeout(() => setToasts((list) => list.filter((toast) => toast.id !== id)), LEAVE_MS);
  }, []);

  const start = useCallback((id: number, ms: number) => {
    const handle = window.setTimeout(() => remove(id), ms);
    timers.current.set(id, { handle, startedAt: Date.now(), left: ms });
  }, [remove]);

  const pause = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (!timer?.handle) return;
    window.clearTimeout(timer.handle);
    timers.current.set(id, { startedAt: timer.startedAt, left: Math.max(0, timer.left - (Date.now() - timer.startedAt)) });
  }, []);

  const resume = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer && !timer.handle) start(id, timer.left);
  }, [start]);

  const push = useCallback((toast: ToastInput) => {
    sequence += 1;
    const id = sequence;
    setToasts((list) => {
      const next = [...list.filter((shown) => !shown.leaving), { ...toast, id, leaving: false }];
      while (next.length > MAX) {
        const drop = next.findIndex((shown) => shown.tone !== 'error');
        const [gone] = next.splice(drop === -1 ? 0 : drop, 1);
        window.clearTimeout(timers.current.get(gone.id)?.handle);
        timers.current.delete(gone.id);
      }
      return [...list.filter((shown) => shown.leaving), ...next];
    });
    if (toast.tone !== 'error') start(id, LIFETIME_MS);
    return id;
  }, [start]);

  const clear = useCallback(() => {
    for (const timer of timers.current.values()) window.clearTimeout(timer.handle);
    timers.current.clear();
    setToasts([]);
  }, []);
  const toaster = useMemo(() => Object.assign(push, { remove, clear }), [push, remove, clear]);

  useEffect(() => () => { for (const timer of timers.current.values()) window.clearTimeout(timer.handle); }, []);

  const row = (toast: Shown, role?: 'alert') => (
    <div
      className={cx('g-toast', `is-${toast.tone}`, toast.leaving && 'is-leaving')}
      key={toast.id}
      onBlur={() => resume(toast.id)}
      onFocus={() => pause(toast.id)}
      onPointerEnter={() => pause(toast.id)}
      onPointerLeave={() => resume(toast.id)}
      role={role}
    >
      <span className="g-toast-message">{clampServerText(toast.message)}</span>
      {toast.action ? <TextButton onClick={() => { toast.action?.onAction(); remove(toast.id); }}>{toast.action.label}</TextButton> : null}
      <IconButton icon={<X />} label={`Dismiss: ${toast.message}`} onClick={() => remove(toast.id)} />
    </div>
  );
  return (
    <ToastContext.Provider value={toaster}>
      {children}
      <div className="g-toast-region">
        <div aria-live="polite" className="g-toast-list">{toasts.filter((toast) => toast.tone !== 'error').map((toast) => row(toast))}</div>
        <div className="g-toast-list">{toasts.filter((toast) => toast.tone === 'error').map((toast) => row(toast, 'alert'))}</div>
      </div>
    </ToastContext.Provider>
  );
}
