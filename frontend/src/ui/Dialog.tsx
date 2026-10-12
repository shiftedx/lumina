import { X } from 'lucide-react';
import { type ReactNode, type RefObject, useId, useLayoutEffect, useRef } from 'react';
import { Button } from './Button';
import { cx } from './cx';
import { IconButton } from './IconButton';

export type DialogSize = 'sm' | 'md' | 'lg' | 'sheet';
export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  size?: DialogSize;
  footer?: ReactNode;
  dismissible?: boolean;
  busy?: boolean;
  initialFocus?: RefObject<HTMLElement | null>;
  children?: ReactNode;
  className?: string;
  hideTitle?: boolean;
}

let modalCount = 0; // nested dialogs share one html class

export function Dialog({ open, onClose, title, description, size = 'md', footer, dismissible = true, busy = false, initialFocus, children, className, hideTitle = false }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const closing = useRef(false);
  const titleId = useId();
  const descriptionId = useId();
  const latest = useRef({ onClose, dismissible, busy, initialFocus });
  latest.current = { onClose, dismissible, busy, initialFocus };

  useLayoutEffect(() => {
    const dialog = ref.current;
    if (!open || !dialog) return undefined;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    modalCount += 1;
    document.documentElement.classList.add('g-modal-open'); // scrollbar-gutter: stable, so the page never jumps
    if (!dialog.open) dialog.showModal();
    const target = latest.current.initialFocus?.current
      ?? dialog.querySelector<HTMLElement>('.g-dialog-body input:not([disabled]):not([type="hidden"]), .g-dialog-body select:not([disabled]), .g-dialog-body textarea:not([disabled])')
      ?? closeButton.current
      ?? dialog.querySelector<HTMLElement>('button:not([disabled])');
    target?.focus();
    return () => {
      // Our own close (unmount, StrictMode re-run) is not the user's. Its close event is queued, so it lands after a re-run's
      // showModal: the flag outlives this call and a timer, not the next effect, clears it.
      if (dialog.open) { closing.current = true; dialog.close(); window.setTimeout(() => { closing.current = false; }, 100); }
      modalCount -= 1;
      if (modalCount === 0) document.documentElement.classList.remove('g-modal-open');
      if (opener?.isConnected) opener.focus();
    };
  }, [open]);

  const requestClose = () => { if (latest.current.dismissible && !latest.current.busy) latest.current.onClose(); };
  if (!open) return null;
  return (
    <dialog
      aria-describedby={description ? descriptionId : undefined}
      aria-labelledby={titleId}
      className={cx('g-dialog', `is-${size}`, className)}
      onCancel={(event) => { event.preventDefault(); requestClose(); }}
      onClick={(event) => { if (event.target === event.currentTarget) requestClose(); }}
      onClose={(event) => { if (!closing.current && !event.currentTarget.open) latest.current.onClose(); }} /* a browser queues the close event: StrictMode's own close lands after the re-open, when the dialog is open again */
      ref={ref}
    >
      <div className="g-dialog-frame">
        <div className="g-dialog-head">
          <h2 className={cx('g-dialog-title', hideTitle && 'sr-only')} id={titleId}>{title}</h2>
          {dismissible ? <IconButton icon={<X />} label="Close" onClick={requestClose} ref={closeButton} /> : null}
        </div>
        {description ? <p className="g-dialog-description" id={descriptionId}>{description}</p> : null}
        <div className="g-dialog-body">{children}</div>
        {footer ? <div className="g-dialog-foot">{footer}</div> : null}
      </div>
    </dialog>
  );
}

export interface ConfirmDialogProps {
  open: boolean;
  title: ReactNode;
  body: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  cancelLabel?: string;
}

/** Replaces the native confirm for destructive actions; Cancel has initial focus when `danger`. */
export function ConfirmDialog({ open, title, body, confirmLabel, danger = false, busy = false, onConfirm, onCancel, cancelLabel = 'Cancel' }: ConfirmDialogProps) {
  const cancel = useRef<HTMLButtonElement>(null);
  return (
    <Dialog
      busy={busy}
      footer={<><Button onClick={onCancel} ref={cancel} variant="quiet">{cancelLabel}</Button><Button busy={busy} onClick={onConfirm} variant={danger ? 'danger' : 'primary'}>{confirmLabel}</Button></>}
      initialFocus={danger ? cancel : undefined}
      onClose={onCancel}
      open={open}
      size="sm"
      title={title}
    >
      <div className="g-confirm-body">{body}</div>
    </Dialog>
  );
}
