import { cx } from './cx';
import { initials } from './initials';

/** Serif initials on paper-2 with a soft ring; never colour-coded per member (brand rule). */
export function Avatar({ name, size, imageUrl, decorative = false }: { name: string; size: 24 | 34 | 48 | 96; imageUrl?: string; decorative?: boolean }) {
  return (
    <span aria-hidden={decorative || undefined} aria-label={decorative ? undefined : name} className={cx('g-avatar', `is-${size}`)} role={decorative ? undefined : 'img'}>
      {imageUrl ? <img alt="" src={imageUrl} /> : <span className="g-avatar-initials">{initials(name)}</span>}
    </span>
  );
}
