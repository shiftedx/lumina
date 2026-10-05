import { cx } from './cx';

/** `value` is a percentage (0–100), or null when indeterminate. */
export function ProgressBar({ value, label, tone = 'default', className }: { value: number | null; label: string; tone?: 'default' | 'danger'; className?: string }) {
  const now = value === null ? null : Math.max(0, Math.min(100, Math.round(value)));
  return (
    <div aria-label={label} aria-valuemax={100} aria-valuemin={0} aria-valuenow={now ?? undefined} className={cx('g-progress', tone === 'danger' && 'is-danger', now === null && 'is-indeterminate', className)} role="progressbar">
      <span className="g-progress-fill" style={now === null ? undefined : { inlineSize: `${now}%` }} />
    </div>
  );
}
