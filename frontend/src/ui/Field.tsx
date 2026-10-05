import { CircleAlert, Eye } from 'lucide-react';
import { type InputHTMLAttributes, type ReactElement, type ReactNode, type Ref, type SelectHTMLAttributes, type TextareaHTMLAttributes, useEffect, useId, useRef, useState } from 'react';
import { cx } from './cx';
import { IconButton } from './IconButton';

export interface FieldIds { inputId: string; hintId?: string; errorId?: string; describedBy?: string; invalid: boolean }
export interface FieldProps {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  required?: boolean;
  hideLabel?: boolean;
  children: (ids: FieldIds) => ReactElement;
}

/** The three attributes every Field child needs. */
export const fieldProps = (ids: FieldIds) => ({ id: ids.inputId, 'aria-describedby': ids.describedBy, 'aria-invalid': ids.invalid || undefined });

export function Field({ label, hint, error, required = false, hideLabel = false, children }: FieldProps) {
  const base = useId();
  const ids: FieldIds = {
    inputId: `${base}input`,
    hintId: hint ? `${base}hint` : undefined,
    errorId: error ? `${base}error` : undefined,
    invalid: Boolean(error),
  };
  ids.describedBy = [ids.hintId, ids.errorId].filter(Boolean).join(' ') || undefined;
  // role="alert" only on the render where the error first appears, so re-renders never re-announce it.
  const shown = useRef(false);
  const firstAppearance = Boolean(error) && !shown.current;
  useEffect(() => { shown.current = Boolean(error); }, [error]);
  return (
    <div className={cx('g-field', error && 'is-invalid')}>
      <label className={cx('g-field-label', hideLabel && 'sr-only')} htmlFor={ids.inputId}>
        {label}{required ? <span aria-hidden="true"> *</span> : null}
      </label>
      {children(ids)}
      {hint ? <p className="g-field-hint" id={ids.hintId}>{hint}</p> : null}
      {error ? <p className="g-field-error" id={ids.errorId} role={firstAppearance ? 'alert' : undefined}><CircleAlert aria-hidden="true" />{error}</p> : null}
    </div>
  );
}

export function Input({ className, ref, ...rest }: InputHTMLAttributes<HTMLInputElement> & { ref?: Ref<HTMLInputElement> }) {
  return <input {...rest} className={cx('g-input', className)} ref={ref} />;
}

export function Textarea({ className, ref, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement> & { ref?: Ref<HTMLTextAreaElement> }) {
  return <textarea {...rest} className={cx('g-input', 'g-textarea', className)} ref={ref} />;
}

export function Select({ className, ref, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement> & { ref?: Ref<HTMLSelectElement> }) {
  return <span className={cx('g-select', className)}><select {...rest} ref={ref}>{children}</select></span>;
}

/** The toggle's name stays "Show password"; aria-pressed carries the state. It never submits. */
export function PasswordInput({ className, ref, ...rest }: Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & { ref?: Ref<HTMLInputElement> }) {
  const [shown, setShown] = useState(false);
  return (
    <span className="g-password">
      <input {...rest} className={cx('g-input', className)} ref={ref} type={shown ? 'text' : 'password'} />
      <IconButton icon={<Eye />} label="Show password" onClick={() => setShown((value) => !value)} pressed={shown} type="button" />
    </span>
  );
}
