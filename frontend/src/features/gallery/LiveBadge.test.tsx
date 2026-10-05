import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { LiveBadge, liveBadgeState, upcomingLabel } from './LiveBadge';

const css = readFileSync(join(__dirname, 'liveBadge.css'), 'utf8');
const time = (at: Date) => at.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }).toUpperCase();

describe('LiveBadge', () => {
  it('maps a lifecycle to its state, and ENDED wins when the relay said so', () => {
    expect(liveBadgeState('live')).toBe('live');
    expect(liveBadgeState('upcoming')).toBe('upcoming');
    expect(liveBadgeState('post_live')).toBe('ended');
    expect(liveBadgeState('completed_live')).toBe('ended');
    expect(liveBadgeState('vod')).toBeNull();
    expect(liveBadgeState(null)).toBeNull();
    expect(liveBadgeState('live', true)).toBe('ended');
  });

  it('labels an upcoming stream by its start: today by time, later by weekday and time, unknown as UPCOMING', () => {
    const now = new Date(2026, 8, 29, 18, 0);
    const tonight = new Date(2026, 8, 29, 20, 30);
    const tomorrow = new Date(2026, 8, 30, 20, 30);
    expect(upcomingLabel(tonight.toISOString(), now)).toBe(time(tonight));
    expect(upcomingLabel(tomorrow.toISOString(), now)).toBe(`${tomorrow.toLocaleDateString(undefined, { weekday: 'short' }).toUpperCase()} ${time(tomorrow)}`);
    expect(upcomingLabel(null, now)).toBe('UPCOMING');
    expect(upcomingLabel('not a date', now)).toBe('UPCOMING');
  });

  it('draws a dot for live, a ring for upcoming and nothing for ended, always hidden from assistive tech', () => {
    const live = render(<LiveBadge state="live" surface="art" />).container.firstElementChild as HTMLElement;
    expect(live.getAttribute('aria-hidden')).toBe('true');
    expect(live.textContent).toBe('LIVE');
    expect(live.className).toBe('g-live is-live on-art');
    expect(live.querySelector('.g-live-dot')).not.toBeNull();
    const ended = render(<LiveBadge state="ended" surface="paper" />).container.firstElementChild as HTMLElement;
    expect(ended.textContent).toBe('ENDED');
    expect(ended.querySelector('.g-live-dot')).toBeNull();
    const upcoming = render(<LiveBadge className="is-corner" state="upcoming" surface="bar" />).container.firstElementChild as HTMLElement;
    expect(upcoming.className).toBe('g-live is-upcoming on-bar is-corner');
    expect(upcoming.textContent).toBe('UPCOMING');
  });

  it('pulses only the live dot, and never under reduced motion', () => {
    const animated = [...css.matchAll(/([^{}]+)\{[^{}]*\banimation:(?!\s*none)[^;}]+/g)].map(([, selector]) => selector.trim());
    expect(animated).toEqual(['.g-live.is-live .g-live-dot']);
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{\s*\.g-live\.is-live \.g-live-dot \{ animation: none; \}/);
    expect(css).toContain('@keyframes g-live { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }');
  });

  it('keeps its only colour literals in one :root block, and has no red', () => {
    const outside = css.replace(/:root \{[^}]*\}/, '').replace(/var\([^)]*\)/g, '');
    expect(outside).not.toMatch(/#[0-9a-f]{3,8}\b|\brgba?\(/i);
    expect(css).not.toMatch(/#c0264a|rgba\(105, 30, 22|rgba\(255, 132, 107/i);
  });
});
