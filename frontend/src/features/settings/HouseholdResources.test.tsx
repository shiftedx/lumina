import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getHouseholdResources: vi.fn(), getMemberResources: vi.fn(), updateHouseholdResources: vi.fn(), updateMemberResources: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));

import { HouseholdResourcesSettings, MemberResourcesSettings } from './HouseholdResources';

const effective = {
  remote_stream_bps: 0, remote_member_bps: 0, playback_sessions: 2, video_encodes: 1,
  download_running: 1, download_queued: 25, ai_running: 1, ai_queued: 10,
};

describe('MemberResourcesSettings', () => {
  beforeEach(() => { api.getHouseholdResources.mockReset(); api.getMemberResources.mockReset(); api.updateHouseholdResources.mockReset(); api.updateMemberResources.mockReset(); });

  it('shows inherited allowances, saves an override, and resets it to inherit', async () => {
    api.getMemberResources.mockResolvedValue({ overrides: {}, effective });
    api.updateMemberResources
      .mockResolvedValueOnce({ overrides: { remote_member_bps: 12_000_000 }, effective: { ...effective, remote_member_bps: 12_000_000 } })
      .mockResolvedValueOnce({ overrides: {}, effective });
    render(<MemberResourcesSettings userId="member-1" />);

    const bandwidth = await screen.findByRole('spinbutton', { name: 'Member remote bandwidth' });
    expect(bandwidth.getAttribute('placeholder')).toBe('Inherit');
    expect(screen.getAllByText('Inherits 0 Mbps Zero is unlimited.')).toHaveLength(2);
    await userEvent.type(bandwidth, '12.5');
    await userEvent.click(screen.getByRole('button', { name: 'Save resources' }));
    expect(api.updateMemberResources).toHaveBeenCalledWith('member-1', { remote_member_bps: 12_500_000 });

    await userEvent.clear(bandwidth);
    await userEvent.click(screen.getByRole('button', { name: 'Save resources' }));
    expect(api.updateMemberResources).toHaveBeenLastCalledWith('member-1', {});
  });

  it('shows a load failure and retries household resources', async () => {
    api.getHouseholdResources
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ upload_bps: null, lan_networks: [], defaults: effective });
    const user = userEvent.setup();
    render(<HouseholdResourcesSettings />);

    expect((await screen.findByRole('alert')).textContent).toContain('offline');
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    await screen.findByRole('spinbutton', { name: 'Remote upload budget' });
    await waitFor(() => expect(api.getHouseholdResources).toHaveBeenCalledTimes(2));
  });

  it('keeps a partially entered CIDR line while saving parsed LAN networks', async () => {
    api.getHouseholdResources.mockResolvedValue({ upload_bps: null, lan_networks: [], defaults: effective });
    api.updateHouseholdResources.mockResolvedValue({ upload_bps: null, lan_networks: ['192.168.1.0/24', '10.0.0.0/24'], defaults: effective });
    const user = userEvent.setup();
    render(<HouseholdResourcesSettings />);

    const networks = await screen.findByRole('textbox', { name: 'LAN networks' });
    await user.type(networks, '192.168.1.0/24\n10.0.0.0/24');
    expect((networks as HTMLTextAreaElement).value).toBe('192.168.1.0/24\n10.0.0.0/24');
    await user.click(screen.getByRole('button', { name: 'Save resources' }));
    expect(api.updateHouseholdResources).toHaveBeenCalledWith({ upload_bps: null, lan_networks: ['192.168.1.0/24', '10.0.0.0/24'], defaults: effective });
  });
});
