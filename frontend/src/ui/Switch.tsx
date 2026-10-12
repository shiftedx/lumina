import { type InputHTMLAttributes, type ReactNode, type Ref, useId } from 'react';
import { cx } from './cx';

export interface SwitchProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'onChange' | 'checked'> {
  label: ReactNode;
  hint?: ReactNode;
  checked: boolean;
  onChange: (next: boolean) => void;
  busy?: boolean;
}

/** A native checkbox with role="switch", so Settings' Enter-to-toggle and TV handling keep working. */
export function Switch({ label, hint, checked, onChange, busy = false, className, ref, ...rest }: SwitchProps & { ref?: Ref<HTMLInputElement> }) {
  const base = useId();
  const hintId = `${base}hint`;
  return (
    <label className={cx('g-switch', className)}>
      <span className="g-switch-copy">
        <span className="g-switch-label" id={`${base}label`}>{label}</span>
        <span className="g-switch-hint" id={hintId}>{hint}{hint && checked ? ' ' : null}{checked ? 'On' : null}</span>
      </span>
      <input
        {...rest}
        aria-busy={busy || undefined}
        aria-describedby={hint || checked ? hintId : undefined}
        aria-labelledby={`${base}label`}
        checked={checked}
        onChange={(event) => { if (!busy) onChange(event.target.checked); }}
        ref={ref}
        role="switch"
        type="checkbox"
      />
      <span aria-hidden="true" className="g-switch-track" />
    </label>
  );
}
