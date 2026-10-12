import { type ButtonHTMLAttributes, type ReactNode, type Ref } from 'react';
import { Spinner } from './Button';
import { cx } from './cx';

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  label: string;
  icon: ReactNode;
  variant?: 'plain' | 'framed' | 'overlay';
  pressed?: boolean;
  busy?: boolean;
}

/** A 44px (48px at 10 feet) square control whose only name is `label`. */
export function IconButton({ label, icon, variant = 'plain', pressed, busy = false, type = 'button', className, title, onClick, ref, ...rest }: IconButtonProps & { ref?: Ref<HTMLButtonElement> }) {
  return (
    <button
      {...rest}
      aria-busy={busy || undefined}
      aria-disabled={busy || rest['aria-disabled'] || undefined}
      aria-label={label}
      aria-pressed={pressed}
      className={cx('g-icon-button', `is-${variant}`, className)}
      onClick={(event) => { if (busy) { event.preventDefault(); return; } onClick?.(event); }}
      ref={ref}
      title={title ?? label}
      type={type}
    >
      {busy ? <Spinner /> : <span aria-hidden="true" className="g-icon-button-glyph">{icon}</span>}
    </button>
  );
}
