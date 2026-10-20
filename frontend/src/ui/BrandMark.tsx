import { cx } from './cx';

/** Flat sunrise with five detached rays, named by the accompanying word. */
export function BrandMark({ size, compact = false }: { size: 'bar' | 'hero'; compact?: boolean }) {
  return (
    <span className={cx('g-brand', `is-${size}`, compact && 'is-compact')}>
      <svg aria-hidden="true" className="g-brand-sun" viewBox="4 9 56 34" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
        <path d="M10 40H54" />
        <path d="M21 40A11 11 0 0 1 43 40" />
        <path d="M32 12V21" />
        <path d="M18 15.751L22.5 23.546" />
        <path d="M46 15.751L41.5 23.546" />
        <path d="M7.751 26L15.546 30.5" />
        <path d="M56.249 26L48.454 30.5" />
      </svg>
      <span className={cx('g-brand-word', compact && 'sr-only')}>Lumina</span>
    </span>
  );
}
