import { describe, expect, it } from 'vitest';

import { capabilityActionDisabled, capabilityActionMessage, capabilityLifecycleLabel } from './sourceCapabilities';
import type { MediaSourceCapabilities } from './types';

describe('source capability presentation', () => {
  it('uses stable reason codes for accessible action copy', () => {
    expect(capabilityLifecycleLabel('live')).toBe('Live');
    expect(capabilityLifecycleLabel('completed_live')).toBe('Recently live');
    expect(capabilityActionMessage('segmented_transport_not_supported', 'play')).toBe('Playback is not available for this segmented source yet.');
    expect(capabilityActionMessage('upcoming_not_started', 'acquire')).toBe('This source has not started yet.');
  });

  it('presents the scheduling action reason and gate (issue #98)', () => {
    expect(capabilityActionMessage('upcoming_schedule_not_supported', 'schedule')).toBe('Scheduling is not available for this upcoming source yet.');
    const schedulable: MediaSourceCapabilities = {
      provider: 'youtube', lifecycle: 'upcoming', can_play: false, can_acquire: false,
      can_schedule: true, chat: { live: 'available', replay: 'unavailable' },
    };
    expect(capabilityActionDisabled(schedulable, 'schedule')).toBe(false);
    expect(capabilityActionDisabled({ ...schedulable, can_schedule: false }, 'schedule')).toBe(true);
  });

  it('keeps a recognized-but-unsupported provider (Kick) honestly unavailable per action', () => {
    const kick: MediaSourceCapabilities = {
      provider: 'kick', lifecycle: 'live', can_play: false, play_reason: 'provider_not_supported',
      can_acquire: false, acquire_reason: 'provider_not_supported', record_reason: 'provider_not_supported',
      chat: { live: 'unavailable', replay: 'unavailable' },
    };
    for (const action of ['play', 'acquire', 'record', 'schedule'] as const) {
      expect(capabilityActionDisabled(kick, action)).toBe(true);
    }
    expect(capabilityActionMessage(kick.play_reason, 'play')).toBe('Playback is not available for this provider yet.');
    expect(capabilityActionMessage(kick.record_reason, 'record')).toBe('Recording is not available for this provider yet.');
  });
});
