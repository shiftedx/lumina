/* @vitest-environment jsdom */

import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { normalizeChannelAddress, resolveChannelAddressCandidate } from './channelSubscriptions';
import { liveNotices } from './liveRails';
import { SubscriptionControls } from './SubscriptionControls';
import type { LiveSnapshot, SourceAutomation } from './types';

describe('Kick follows', () => {
  it('mirrors the backend canonical Kick channel identity', () => {
    for (const variant of ['https://WWW.kick.com/XQC/', 'kick.com/xqc/videos/5c697a87-afce-4256-b01f-3c8fe71ef5cb', 'https://kick.com/xqc?clip=clip_1']) {
      expect(normalizeChannelAddress(variant)).toBe('https://kick.com/xqc');
    }
    expect(normalizeChannelAddress('https://kick.com:8443/xqc')).toBeNull();
    expect(normalizeChannelAddress('https://kick.com/categories/games')).toBeNull();
    const candidate = resolveChannelAddressCandidate('https://kick.com/XQC', new Set(['https://kick.com/xqc']));
    expect(candidate).toMatchObject({ channel_key: 'https://kick.com/xqc', source: 'kick', source_label: 'Kick', following: true });
  });

  it('labels an unavailable followed source with its last check, keeping other sources quiet', () => {
    const snapshot = { twitch_available: true, followed_unavailable: [{ source: 'kick', checked_at: '2026-01-01T15:04:00Z' }] } as LiveSnapshot;
    const [notice, ...rest] = liveNotices(snapshot);
    expect(notice).toMatch(/^Live status for followed Kick channels is unavailable \(last tried .+\)\.$/);
    expect(rest).toEqual([]);
    expect(liveNotices({ ...snapshot, followed_unavailable: [] })).toEqual([]);
  });

  it('keeps automatic saving off for a Kick follow and says why', () => {
    const automation = {
      id: 'kick-1', user_id: 'm', label: 'xQc', source_url: 'https://kick.com/xqc', source_type: 'channel',
      cron_expression: '*/30 * * * *', active: true, auto_download: false, format_selection: {}, output_profile: {},
      rules: {}, duplicate_policy: 'skip_same_source', last_run_summary: {}, created_at: '', updated_at: '',
    } as unknown as SourceAutomation;
    render(<SubscriptionControls automation={automation} commands={{
      pause: vi.fn(), resume: vi.fn(), setAutomaticAcquisition: vi.fn(), prepareUnfollow: vi.fn(), confirmUnfollow: vi.fn(),
    }} onChange={vi.fn()} onRecover={vi.fn()} onRemoved={vi.fn()} />);
    expect((screen.getByRole('checkbox', { name: /download new videos automatically/i }) as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(/Kick downloads are not supported yet/)).toBeTruthy();
  });
});
