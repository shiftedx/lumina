/* @vitest-environment jsdom */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { automaticSavingSummary, SubscriptionControls } from './SubscriptionControls';
import type { SourceAutomation } from './types';

const follow = {
  id: 'f1', user_id: 'm', label: 'Creator', source_url: 'https://www.youtube.com/@creator', source_type: 'channel',
  cron_expression: '*/30 * * * *', active: true, auto_download: false, format_selection: { preset: 'best_1080p' },
  output_profile: {}, rules: {}, duplicate_policy: 'skip_same_source', max_items_per_run: 5, max_items_per_day: 20,
  last_run_summary: {}, created_at: '', updated_at: '',
} as unknown as SourceAutomation;

describe('opt-in automatic saving', () => {
  it('previews quality and limits before the member opts in', () => {
    expect(automaticSavingSummary(follow)).toBe(
      'When on, saves Video · 1080p, up to 5 per check, 20 per day. Each video is queued at most once and counts toward your download queue limit.',
    );
    expect(automaticSavingSummary({ ...follow, auto_download: true, max_items_per_run: null, max_items_per_day: null })).toMatch(/^Saving Video · 1080p, every new video\./);
  });

  it('keeps following separate: pausing and enabling saving are distinct explicit commands', async () => {
    const browser = userEvent.setup();
    const commands = {
      pause: vi.fn().mockResolvedValue({ ...follow, active: false }), resume: vi.fn(),
      setAutomaticAcquisition: vi.fn().mockResolvedValue({ ...follow, auto_download: true }),
      prepareUnfollow: vi.fn(), confirmUnfollow: vi.fn(),
    };
    render(<SubscriptionControls automation={follow} commands={commands} onChange={vi.fn()} onRecover={vi.fn()} onRemoved={vi.fn()} />);
    const toggle = screen.getByRole('checkbox', { name: 'Download new videos automatically' }) as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    await browser.click(toggle);
    expect(commands.setAutomaticAcquisition).toHaveBeenCalledWith(follow, true);
    await browser.click(screen.getByRole('button', { name: 'Pause checks for Creator' }));
    expect(commands.pause).toHaveBeenCalledWith(follow);
    expect(commands.confirmUnfollow).not.toHaveBeenCalled();
  });
});
