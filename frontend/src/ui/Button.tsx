import { type AnchorHTMLAttributes, type ButtonHTMLAttributes, type ReactNode, type Ref, useImperativeHandle, useLayoutEffect, useRef } from 'react';
import { cx } from './cx';

export type ButtonVariant = 'primary' | 'secondary' | 'quiet' | 'danger';
export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  icon?: ReactNode;
  iconEnd?: ReactNode;
  busy?: boolean;
  wide?: boolean;
  pressed?: boolean;
}

/** The one spinner: busy buttons and player buffering only. Static under reduced motion. */
export function Spinner() {
  return <span aria-hidden="true" className="g-spinner" />;
}

const glyph = (node: ReactNode) => (node ? <span aria-hidden="true" className="g-button-icon">{node}</span> : null);

export function Button({ variant = 'secondary', icon, iconEnd, busy = false, wide = false, pressed, type = 'button', className, style, children, onClick, ref, ...rest }: ButtonProps & { ref?: Ref<HTMLButtonElement> }) {
  const own = useRef<HTMLButtonElement>(null);
  const idleWidth = useRef<number | null>(null);
  useImperativeHandle(ref, () => own.current as HTMLButtonElement, []);
  // Busy keeps the width it had at rest, so a spinner never shifts the layout.
  useLayoutEffect(() => { if (!busy && own.current) idleWidth.current = own.current.offsetWidth || null; });
  return (
    <button
      {...rest}
      aria-busy={busy || undefined}
      aria-disabled={busy || rest['aria-disabled'] || undefined}
      aria-pressed={pressed}
      className={cx('g-button', `is-${variant}`, wide && 'is-wide', className)}
      onClick={(event) => { if (busy) { event.preventDefault(); return; } onClick?.(event); }}
      ref={own}
      style={busy && idleWidth.current ? { ...style, width: idleWidth.current } : style}
      type={type}
    >
      {busy ? <Spinner /> : glyph(icon)}
      <span className="g-button-text">{children}</span>
      {glyph(iconEnd)}
    </button>
  );
}

export interface ButtonLinkProps extends AnchorHTMLAttributes<HTMLAnchorElement> {
  href: string;
  variant?: ButtonVariant;
  icon?: ReactNode;
  iconEnd?: ReactNode;
  wide?: boolean;
}

export function ButtonLink({ variant = 'secondary', icon, iconEnd, wide = false, className, children, ref, ...rest }: ButtonLinkProps & { ref?: Ref<HTMLAnchorElement> }) {
  return (
    <a {...rest} className={cx('g-button', `is-${variant}`, wide && 'is-wide', className)} ref={ref}>
      {glyph(icon)}<span className="g-button-text">{children}</span>{glyph(iconEnd)}
    </a>
  );
}

export function TextButton({ icon, iconEnd, busy = false, pressed, type = 'button', className, children, onClick, ref, ...rest }: Omit<ButtonProps, 'variant' | 'wide'> & { ref?: Ref<HTMLButtonElement> }) {
  return (
    <button
      {...rest}
      aria-busy={busy || undefined}
      aria-disabled={busy || rest['aria-disabled'] || undefined}
      aria-pressed={pressed}
      className={cx('g-text-button', className)}
      onClick={(event) => { if (busy) { event.preventDefault(); return; } onClick?.(event); }}
      ref={ref}
      type={type}
    >
      {busy ? <Spinner /> : glyph(icon)}{children}{glyph(iconEnd)}
    </button>
  );
}
