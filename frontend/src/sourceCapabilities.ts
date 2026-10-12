import type { MediaCapabilityReason, MediaLifecycle, MediaSourceCapabilities } from './types';

export function capabilityLifecycleLabel(lifecycle: MediaLifecycle | null | undefined): string | null {
  switch (lifecycle) {
    case 'live': return 'Live';
    case 'upcoming': return 'Upcoming';
    case 'post_live': return 'Recently live';
    case 'completed_live': return 'Recently live';
    default: return null;
  }
}

export type CapabilityAction = 'play' | 'acquire' | 'record' | 'schedule';

function actionNoun(action: CapabilityAction): string {
  switch (action) {
    case 'play': return 'Playback';
    case 'acquire': return 'Acquisition';
    case 'record': return 'Recording';
    case 'schedule': return 'Scheduling';
  }
}

export function capabilityActionMessage(reason: MediaCapabilityReason | null | undefined, action: CapabilityAction): string | null {
  switch (reason) {
    case 'live_playback_not_supported': return 'Live playback is not available yet.';
    case 'subscriber_only': return 'This source is limited to the channel’s subscribers. Open the original link to watch it there.';
    case 'sign_in_required': return 'This source needs a provider sign-in (age, membership or private). Lumina only plays public media; open the original link to watch it there.';
    case 'live_acquisition_not_supported': return 'Live acquisition is not available yet.';
    case 'live_record_not_supported': return 'Recording is not available for this source yet.';
    case 'upcoming_not_started': return 'This source has not started yet.';
    case 'upcoming_schedule_not_supported': return 'Scheduling is not available for this upcoming source yet.';
    case 'post_live_processing': return 'This source is processing after a live broadcast.';
    case 'segmented_transport_not_supported': return `${actionNoun(action)} is not available for this segmented source yet.`;
    case 'provider_not_supported': return `${actionNoun(action)} is not available for this provider yet.`;
    case 'no_supported_transport': return `Lumina cannot prepare ${actionNoun(action).toLowerCase()} for this source yet.`;
    default: return null;
  }
}

export function capabilityActionDisabled(capabilities: MediaSourceCapabilities | null | undefined, action: CapabilityAction): boolean {
  if (!capabilities) return false;
  switch (action) {
    case 'play': return !capabilities.can_play;
    case 'acquire': return !capabilities.can_acquire;
    case 'record': return capabilities.can_record !== true;
    case 'schedule': return capabilities.can_schedule !== true;
  }
}
