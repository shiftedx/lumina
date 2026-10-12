import { type ReactElement, type ReactNode, useId } from 'react';
import { cx } from './cx';

export interface SegmentedOption<T extends string> { value: T; label: string; icon?: ReactNode; disabled?: boolean }
export interface SegmentedControlProps<T extends string> {
  legend: string;
  options: readonly SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  hideLegend?: boolean;
  name?: string;
}

/** Native radios drawn as joined hairline segments; the arrow keys are the browser's. */
export function SegmentedControl<T extends string>({ legend, options, value, onChange, hideLegend = false, name }: SegmentedControlProps<T>): ReactElement {
  const group = useId();
  return (
    <fieldset className="g-segmented">
      <legend className={cx('g-label', hideLegend && 'sr-only')}>{legend}</legend>
      <div className="g-segmented-options">
        {options.map((option) => (
          <label className="g-segment" key={option.value}>
            <input checked={option.value === value} disabled={option.disabled} name={name ?? group} onChange={() => onChange(option.value)} type="radio" value={option.value} />
            {option.icon ? <span aria-hidden="true" className="g-segment-icon">{option.icon}</span> : null}
            <span>{option.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
