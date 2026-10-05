import { cx } from './cx';

const RAYS = [-62, -32, 0, 32, 62];

/** The 1.9.0 hairline half-sun (horizon, circle, five rays, all 1px gold) beside an uppercase tracked serif word. The art is decorative. */
export function BrandMark({ size, compact = false }: { size: 'bar' | 'hero'; compact?: boolean }) {
  return (
    <span className={cx('g-brand', `is-${size}`, compact && 'is-compact')}>
      <span aria-hidden="true" className="g-brand-sun">{RAYS.map((deg) => <i key={deg} style={{ rotate: `${deg}deg` }} />)}</span>
      <span className={cx('g-brand-word', compact && 'sr-only')}>Lumina</span>
    </span>
  );
}
