import { type InputHTMLAttributes, type ReactNode, type Ref, useEffect, useId, useImperativeHandle, useRef } from 'react';
import { cx } from './cx';

export interface ChoiceProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> { label: ReactNode; hint?: ReactNode; indeterminate?: boolean }

function Choice({ type, label, hint, indeterminate = false, className, ref, ...rest }: ChoiceProps & { type: 'checkbox' | 'radio'; ref?: Ref<HTMLInputElement> }) {
  const input = useRef<HTMLInputElement>(null);
  const base = useId();
  const hintId = `${base}hint`;
  useImperativeHandle(ref, () => input.current as HTMLInputElement, []);
  useEffect(() => { if (input.current) input.current.indeterminate = indeterminate; }, [indeterminate]);
  // The whole row is the <label> (44px target); the name comes from the label text only, the hint is the description.
  return (
    <label className={cx('g-choice', className)}>
      <input {...rest} aria-describedby={hint ? hintId : undefined} aria-labelledby={`${base}label`} ref={input} type={type} />
      <span className="g-choice-copy">
        <span className="g-choice-label" id={`${base}label`}>{label}</span>
        {hint ? <span className="g-choice-hint" id={hintId}>{hint}</span> : null}
      </span>
    </label>
  );
}

export function Checkbox(props: ChoiceProps & { ref?: Ref<HTMLInputElement> }) {
  return <Choice {...props} type="checkbox" />;
}

export function Radio(props: Omit<ChoiceProps, 'indeterminate'> & { ref?: Ref<HTMLInputElement> }) {
  return <Choice {...props} type="radio" />;
}
