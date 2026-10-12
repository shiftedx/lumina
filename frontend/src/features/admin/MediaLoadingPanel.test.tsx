import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { ArtworkProgress, MediaLoading } from '../../types';
import { MediaLoadingPanel } from './MediaLoadingPanel';

const loading: MediaLoading = {
  metrics: [
    { metric: 'ttff_ms', label: 'direct', today_count: 3, today_p50: 1113, today_p95: 1599, week_count: 12, week_p50: 1113, week_p95: 1599, budget_p50: 1500, budget_p95: 2000, within_budget: true },
    { metric: 'wall_first_screen_ms', label: 'movies', today_count: 0, today_p50: null, today_p95: null, week_count: 4, week_p50: 775, week_p95: 1599, budget_p50: 600, budget_p95: 1000, within_budget: false },
    { metric: 'image_failed', label: 'poster:transient', today_count: 1, today_p50: 1, today_p95: 1, week_count: 1, week_p50: 1, week_p95: 1, budget_p50: null, budget_p95: null, within_budget: null },
  ],
  image_failure_rate_today: 0.004,
  image_failure_rate_week: 0,
};
const artwork: ArtworkProgress = {
  total: 41200, prepared: 38112, failed: 12, unsupported: 30, cache_bytes: 2_147_483_648, running: true, paused_for_playback: true,
  failure_reasons: { timeout: 7, ffmpeg_failed: 5 }, updated_at: null,
  serving: { hits_memory: 900, hits_disk: 80, misses: 20, generated: 18, fallback_original: 1, fallback_unavailable: 1, failures: 0 },
};

describe('Diagnostics: Media loading', () => {
  it('shows each measure against its budget in words and colour', () => {
    render(<MediaLoadingPanel artwork={artwork} loading={loading} />);
    const [, ttff, wall, failed] = screen.getAllByRole('row');
    expect(within(ttff).getByRole('rowheader').textContent).toContain('Time to first frame');
    expect(within(ttff).getByRole('rowheader').textContent).toContain('direct play');
    expect(ttff.textContent).toContain('1.1 s / 1.6 s');
    expect(ttff.textContent).toContain('1.5 s / 2.0 s');
    expect(within(ttff).getByText('Within budget').closest('.g-status')?.querySelector('svg')).not.toBeNull();
    expect(within(wall).getByText('Over budget').closest('.g-status')?.querySelector('svg')).not.toBeNull();
    expect(document.querySelector('.state-completed, .state-failed')).toBeNull();
    expect(wall.textContent).toContain('— / —');
    expect(within(failed).getByText('No budget')).toBeTruthy();
    expect(screen.getByText('0.40 %')).toBeTruthy();
    expect(screen.getByText('0.0 %')).toBeTruthy();
    expect(screen.getByText('38,112 / 41,200')).toBeTruthy();
    expect(screen.getByText('Paused while a video is being converted')).toBeTruthy();
    expect(screen.getByText('Timed out')).toBeTruthy();
  });

  it('says plainly when nothing has been measured yet', () => {
    render(<MediaLoadingPanel artwork={null} loading={{ metrics: [], image_failure_rate_today: null, image_failure_rate_week: null }} />);
    expect(screen.getByText(/No loading measurements yet/)).toBeTruthy();
    expect(screen.getAllByText('No images yet')).toHaveLength(2);
    expect(screen.getByText('Artwork preparation is not running.')).toBeTruthy();
  });
});
