import { OctagonX } from 'lucide-react';
import { type ReactNode } from 'react';
import { Button } from './Button';
import { cx } from './cx';
import { clampServerText } from './text';

export function EmptyState({ title, body, action, size = 'inline' }: { title: string; body?: ReactNode; action?: ReactNode; size?: 'page' | 'inline' }) {
  return (
    <div className={cx('g-empty', `is-${size}`)}>
      {size === 'page' ? <h2 className="g-empty-title">{title}</h2> : <p className="g-empty-title">{title}</p>}
      {body ? <div className="g-empty-body">{body}</div> : null}
      {action ? <div className="g-empty-action">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ title, body, onRetry, retrying = false, retryLabel = 'Try again' }: { title: string; body?: string; onRetry?: () => void; retrying?: boolean; retryLabel?: string }) {
  return (
    <div className="g-error-state" role="alert">
      <OctagonX aria-hidden="true" className="g-error-icon" />
      <div className="g-error-copy">
        <p className="g-error-title">{title}</p>
        {body ? <p className="g-error-body">{clampServerText(body)}</p> : null}
        {onRetry ? <Button busy={retrying} onClick={onRetry}>{retryLabel}</Button> : null}
      </div>
    </div>
  );
}

export type SkeletonShape = 'line' | 'block' | 'poster' | 'still' | 'square' | 'row' | 'avatar';

/** Hairline frames, no fill, no shimmer (foundation 1.6); the label is the only thing read. */
export function Skeleton({ shape, count = 1, label }: { shape: SkeletonShape; count?: number; label: string }) {
  return (
    <div aria-busy="true" className={cx('g-skeleton', `is-${shape}`)}>
      <span className="sr-only" role="status">{label}</span>
      {Array.from({ length: count }, (_, index) => <span aria-hidden="true" className="g-skeleton-item" key={index} />)}
    </div>
  );
}
