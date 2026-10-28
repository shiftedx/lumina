import { ChevronLeft, ChevronRight } from './icons';
import { type KeyboardEventHandler, type ReactNode, useCallback, useLayoutEffect, useRef, useState } from 'react';

import { IconButton } from './IconButton';
import { cx } from './cx';

export type TabScrollerProps = {
  activeKey: string;
  ariaLabel: string;
  as?: 'div' | 'nav';
  children: ReactNode;
  className?: string;
  focusRow?: boolean;
  onKeyDown?: KeyboardEventHandler<HTMLElement>;
  role?: 'tablist';
  rowClassName: string;
};

/** A tab row that reveals its active item and adds truthful scroll controls only when its full width overflows. */
export function TabScroller({ activeKey, ariaLabel, as = 'div', children, className, focusRow = false, onKeyDown, role, rowClassName }: TabScrollerProps) {
  const frame = useRef<HTMLDivElement>(null);
  const row = useRef<HTMLElement>(null);
  const setRow = useCallback((element: HTMLElement | null) => { row.current = element; }, []);
  const [ends, setEnds] = useState({ overflow: false, left: false, right: false });
  const measure = useCallback(() => {
    const container = frame.current;
    const element = row.current;
    if (!container || !element) return;
    const next = {
      overflow: element.scrollWidth > container.clientWidth + 1,
      left: element.scrollLeft > 1,
      right: element.scrollLeft + element.clientWidth < element.scrollWidth - 1,
    };
    setEnds((current) => current.overflow === next.overflow && current.left === next.left && current.right === next.right ? current : next);
  }, []);
  const revealActive = useCallback(() => {
    const element = row.current;
    const active = element?.querySelector<HTMLElement>('[aria-current="page"], [aria-selected="true"]');
    if (!element || !active) return;
    const start = active.offsetLeft - element.offsetLeft;
    const end = start + active.offsetWidth;
    if (start < element.scrollLeft) element.scrollLeft = start;
    else if (end > element.scrollLeft + element.clientWidth) element.scrollLeft = end - element.clientWidth;
  }, []);
  useLayoutEffect(() => {
    const container = frame.current;
    const element = row.current;
    if (!container || !element) return undefined;
    const resize = () => { revealActive(); measure(); };
    revealActive();
    measure();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', resize);
      return () => window.removeEventListener('resize', resize);
    }
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    observer.observe(element);
    return () => observer.disconnect();
  }, [activeKey, measure, revealActive]);
  // End controls reduce the row after overflow is first detected. Reveal the
  // selected tab again in that narrower viewport so it does not sit behind a
  // still-enabled scroll affordance.
  useLayoutEffect(() => {
    if (!ends.overflow) return;
    revealActive();
    measure();
  }, [ends.overflow, measure, revealActive]);
  const scroll = (direction: -1 | 1) => {
    const element = row.current;
    if (!element) return;
    element.scrollBy({ left: direction * element.clientWidth * .8, behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  };
  const rowProps = {
    'aria-label': ariaLabel,
    className: rowClassName,
    'data-focus-row': focusRow ? '' : undefined,
    onKeyDown,
    onScroll: measure,
    ref: setRow,
    role,
  };
  return (
    <div className={cx('g-tab-scroller', className)} ref={frame}>
      {as === 'nav' ? <nav {...rowProps}>{children}</nav> : <div {...rowProps}>{children}</div>}
      {ends.overflow ? (
        <span className="g-tab-scroller-controls">
          <IconButton disabled={!ends.left} icon={<ChevronLeft />} label={`Scroll ${ariaLabel} tabs left`} onClick={() => scroll(-1)} />
          <IconButton disabled={!ends.right} icon={<ChevronRight />} label={`Scroll ${ariaLabel} tabs right`} onClick={() => scroll(1)} />
        </span>
      ) : null}
    </div>
  );
}
