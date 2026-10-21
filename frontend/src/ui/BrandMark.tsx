import { cx } from './cx';

/** Flat sunrise with five detached rays, named by the accompanying word. */
export function BrandMark({ size, compact = false }: { size: 'bar' | 'hero'; compact?: boolean }) {
  return (
    <span className={cx('g-brand', `is-${size}`, compact && 'is-compact')}>
      <img aria-hidden="true" alt="" className="g-brand-sun" src="/media-mark.svg?v=lumina-5" width="56" height="34" />
      <span className={cx('g-brand-word', compact && 'sr-only')}>Lumina</span>
    </span>
  );
}
