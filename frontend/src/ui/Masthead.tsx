import { type ReactNode, type Ref } from 'react';
import { cx } from './cx';

export interface MastheadProps {
  title: string;
  kicker?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  lede?: ReactNode;
  level?: 1 | 2;
  headingRef?: Ref<HTMLHeadingElement>;
  headingId?: string;
  align?: 'start' | 'center';
}

/** The editorial page header: kicker, serif title, hairline below, actions right (under the title on phone). */
export function Masthead({ title, kicker, meta, actions, lede, level = 1, headingRef, headingId, align = 'start' }: MastheadProps) {
  const Heading = level === 1 ? 'h1' : 'h2';
  return (
    <header className={cx('g-masthead', `is-level-${level}`, align === 'center' && 'is-centered')}>
      <div className="g-masthead-copy">
        {kicker ? <p className="g-kicker g-label">{kicker}</p> : null}
        <Heading className="g-masthead-title" id={headingId} ref={headingRef} tabIndex={-1}>{title}</Heading>
        {meta ? <p className="g-masthead-meta">{meta}</p> : null}
        {lede ? <p className="g-masthead-lede">{lede}</p> : null}
      </div>
      {actions ? <div className="g-masthead-actions">{actions}</div> : null}
    </header>
  );
}
