import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Avatar, EmptyState, ErrorState, Kbd, Masthead, ProgressBar, Skeleton, StatusText, VisuallyHidden, initials, modKeyLabel } from '.';

describe('state primitives', () => {
  it('EmptyState is plain content with no live region', () => {
    render(<EmptyState action={<button type="button">Add a link</button>} body="Links you add arrive here." title="Nothing is downloading." />);
    expect(document.body.contains(screen.getByText('Nothing is downloading.'))).toBe(true);
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('ErrorState alerts, truncates server text and retries busy', async () => {
    const onRetry = vi.fn();
    const { rerender } = render(<ErrorState body={'x'.repeat(300)} onRetry={onRetry} title="Lumina could not load your downloads." />);
    expect(screen.getByRole('alert').textContent).toContain('Lumina could not load your downloads.');
    expect(screen.getByText(/^x+…$/).textContent).toHaveLength(241);
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalledOnce();
    rerender(<ErrorState onRetry={onRetry} retrying title="Lumina could not load your downloads." />);
    expect(screen.getByRole('button', { name: 'Try again' }).getAttribute('aria-busy')).toBe('true');
  });

  it('Skeleton is busy with a visually hidden status label', () => {
    const { container } = render(<Skeleton count={3} label="Loading downloads…" shape="row" />);
    expect(screen.getByRole('status').textContent).toContain('Loading downloads…');
    expect(container.firstElementChild!.getAttribute('aria-busy')).toBe('true');
    expect(container.querySelectorAll('.g-skeleton-item')).toHaveLength(3);
  });

  it('ProgressBar exposes its value, or none when indeterminate', () => {
    const { rerender } = render(<ProgressBar label="45 percent downloaded" value={45.4} />);
    expect(screen.getByRole('progressbar', { name: '45 percent downloaded' }).getAttribute('aria-valuenow')).toBe('45');
    rerender(<ProgressBar label="Preparing your Home" value={null} />);
    expect(screen.getByRole('progressbar').hasAttribute('aria-valuenow')).toBe(false);
  });

  it('StatusText pairs an icon with the words', () => {
    render(<StatusText tone="attention">Activity may be out of date</StatusText>);
    const status = screen.getByText('Activity may be out of date').parentElement!;
    expect(status.className).toContain('is-attention');
    expect(status.querySelector('svg')!.getAttribute('aria-hidden')).toBe('true');
  });

  it('Avatar shows initials, never a per-member colour, and hides when decorative', () => {
    const { rerender } = render(<Avatar name="Dana Lee" size={34} />);
    expect(screen.getByRole('img', { name: 'Dana Lee' }).textContent).toContain('DL');
    expect(screen.getByRole('img').getAttribute('style')).toBeNull();
    rerender(<Avatar decorative name="Dana Lee" size={34} />);
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('Masthead renders the heading level, kicker, actions and forwards the heading ref', () => {
    const ref = { current: null as HTMLHeadingElement | null };
    render(<Masthead actions={<button type="button">Clear finished</button>} headingRef={ref} kicker="Vault activity" level={1} title="Downloads" />);
    expect(screen.getByRole('heading', { level: 1, name: 'Downloads' })).toBe(ref.current);
    expect(document.body.contains(screen.getByText('Vault activity'))).toBe(true);
  });

  it('initials, modKeyLabel, Kbd and VisuallyHidden', () => {
    expect(initials('Dana Lee')).toBe('DL');
    expect(initials('alexandria')).toBe('AL');
    expect(initials('  ')).toBe('?');
    expect(modKeyLabel({ platform: 'MacIntel' } as Navigator)).toBe('⌘K');
    expect(modKeyLabel({ platform: 'Win32' } as Navigator)).toBe('Ctrl K');
    render(<><Kbd>esc</Kbd><VisuallyHidden>12 results</VisuallyHidden></>);
    expect(screen.getByText('esc').tagName).toBe('KBD');
    expect(screen.getByText('12 results').className).toBe('sr-only');
  });
});
