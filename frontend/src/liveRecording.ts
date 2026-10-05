import type { LiveRecording, LiveRecordingEndReason, LiveRecordingStatus, MediaSourceCapabilities } from './types';

// The overall states that mean the recording is done, one way or another.
const TERMINAL_STATUSES: ReadonlySet<LiveRecordingStatus> = new Set([
  'completed',
  'partial',
  'failed',
  'cancelled',
]);

export function canRecordFromNow(capabilities: MediaSourceCapabilities | null | undefined): boolean {
  return capabilities?.can_record === true;
}

/**
 * The forward-only boundary the UI must present BEFORE recording starts. The
 * honest distinction is MEDIA vs CHAT:
 *
 * - CHAT is ALWAYS forward-only where it is offered at all — captured only from
 *   the moment Lumina connects, and earlier chat is never imported (no past
 *   broadcast).
 * - MEDIA is forward-only ONLY when recording from the live edge. When the member
 *   has selected the best-effort from-start intent (`fromStartSelected`), media
 *   is captured best-effort from the broadcast beginning, so the blanket "nothing
 *   from earlier is imported" media claim would be dishonest.
 *
 * from-start applies to a currently-live source too (from_start_available is
 * computed for lifecycle in {live, upcoming}), so the from-start wording is not
 * scheduling-only. Twitch never advertises from-start, so its strict identity
 * boundary is unchanged.
 */
export function forwardOnlyRecordingBoundary(
  capabilities: MediaSourceCapabilities | null | undefined,
  options: { fromStartSelected?: boolean } = {},
): string {
  if (capabilities?.provider === 'twitch') {
    // Provider-native live chat needs a third-party identity, which is
    // unavailable in 1.0, so a Twitch recording captures media only.
    return 'Recording starts from now: the broadcast is captured from the moment you connect. Live chat is not captured for this source, and nothing from earlier is imported.';
  }
  if (options.fromStartSelected) {
    return 'Recording from the beginning (experimental): Lumina will try to capture this broadcast’s video from its start, on a best-effort basis. Chat is still captured only from the moment Lumina connects — earlier chat is never imported.';
  }
  return 'Recording starts from now: only the broadcast and its chat from the moment Lumina connects are captured. Nothing from earlier is imported.';
}

/** Whether an upcoming broadcast can be scheduled (issue #98). */
export function canScheduleUpcoming(capabilities: MediaSourceCapabilities | null | undefined): boolean {
  return capabilities?.can_schedule === true;
}

/** Whether the best-effort/experimental from-start intent can be offered (issue #98). */
export function isFromStartAvailable(capabilities: MediaSourceCapabilities | null | undefined): boolean {
  return capabilities?.from_start_available === true;
}

export function isLiveRecordingTerminal(status: LiveRecordingStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}

export function liveRecordingStatusLabel(status: LiveRecordingStatus): string {
  switch (status) {
    case 'waiting': return 'Waiting for the broadcast';
    case 'queued': return 'Connecting';
    case 'live': return 'Recording';
    case 'stopping': return 'Stopping';
    case 'finalizing': return 'Finalizing';
    case 'completed': return 'Recorded';
    case 'partial': return 'Recorded with a partial outcome';
    case 'failed': return 'Recording failed';
    case 'cancelled': return 'Recording cancelled';
    default: return status;
  }
}

export type LiveRecordingTone = 'progress' | 'waiting' | 'success' | 'partial' | 'failure' | 'cancelled';

export interface LiveRecordingSummary {
  tone: LiveRecordingTone;
  headline: string;
  mediaSummary: string;
  chatSummary: string;
  /** The completed media output's Library item, when one was published. */
  libraryItemId: string | null;
  /** The captured chat asset, when one was published. */
  chatAssetId: string | null;
  /** Where capture began (issue #98), when it has been determined. */
  originSummary: string | null;
  /** The explicit partial-history disclosure (issue #98), when history is incomplete. */
  historyNote: string | null;
  /** The waiting/scheduled explanation (issue #98): what Lumina is waiting for, or why a schedule ended. */
  waitingNote: string | null;
  /** A from-start-unavailable result the member can resolve by recording from the edge. */
  awaitingFallbackChoice: boolean;
  /** Why capture ended, in plain words, once it has. */
  endNote: string | null;
  /** The restart gap disclosure. */
  restartNote: string | null;
  /** While active: the ceilings that will stop it and where the file goes. */
  limitsNote: string | null;
}

/**
 * Describe a recording's two sibling outputs honestly.
 *
 * This mirrors the backend's partial-outcome behaviour: the recording is only a
 * success when both outputs reached a documented terminal state, a single usable
 * output is an explicit partial (never false success or false total failure),
 * and a deliberate cancellation is distinct from a failure.
 */
export function summarizeLiveRecording(recording: LiveRecording): LiveRecordingSummary {
  const mediaSummary = describeMedia(recording);
  const chatSummary = describeChat(recording);
  const libraryItemId = recording.media?.library_item_id ?? null;
  const chatAssetId = recording.chat?.chat_asset_id ?? null;

  let tone: LiveRecordingTone;
  switch (recording.status) {
    case 'completed': tone = 'success'; break;
    case 'partial': tone = 'partial'; break;
    case 'failed': tone = 'failure'; break;
    case 'cancelled': tone = 'cancelled'; break;
    case 'waiting': tone = 'waiting'; break;
    default: tone = 'progress'; break;
  }

  return {
    tone,
    headline: liveRecordingStatusLabel(recording.status),
    mediaSummary,
    chatSummary,
    libraryItemId,
    chatAssetId,
    originSummary: describeCaptureOrigin(recording),
    historyNote: describeHistory(recording),
    waitingNote: describeWaiting(recording),
    awaitingFallbackChoice: recording.awaiting_fallback_choice === true,
    endNote: describeEnd(recording),
    restartNote: recording.resumed_after_restart
      ? 'Lumina restarted during this recording. It picked up again afterwards, so the part broadcast while it was down is missing.'
      : null,
    limitsNote: describeLimits(recording),
  };
}

const END_NOTES: Record<LiveRecordingEndReason, string> = {
  source_ended: 'Stopped when the broadcast ended.',
  stopped: 'Stopped when you asked.',
  time_limit: 'Stopped at the recording time limit.',
  size_limit: 'Stopped at the recording size limit.',
  disk_low: 'Stopped because the server was running out of disk space.',
  owner_disabled: 'Stopped because the account that started it was disabled.',
  shutdown: 'Stopped because Lumina was shutting down.',
  interrupted: 'Stopped because the broadcast could no longer be reached. What was recorded before that was kept.',
};

function describeEnd(recording: LiveRecording): string | null {
  const reason = recording.media?.end_reason;
  return reason ? END_NOTES[reason] ?? null : null;
}

function describeLimits(recording: LiveRecording): string | null {
  if (isLiveRecordingTerminal(recording.status) || recording.status === 'waiting') return null;
  const parts: string[] = [];
  if (recording.max_runtime_seconds) parts.push(`${Math.round(recording.max_runtime_seconds / 3600)} h`);
  if (recording.max_bytes) parts.push(`${Math.round(recording.max_bytes / 1024 ** 3)} GB`);
  const limits = parts.length ? `Stops automatically after ${parts.join(' or ')}. ` : '';
  return `${limits}Saved privately to your Library as a recording.`;
}

function describeMedia(recording: LiveRecording): string {
  // Optional chaining is defensive: a malformed response missing a sibling must
  // degrade to a neutral line, never white-screen the Watch surface.
  if (recording.status === 'waiting') return 'Waiting for the broadcast to begin before recording the video.';
  switch (recording.media?.status) {
    case 'pending': return 'Waiting to record the video.';
    case 'recording':
      return recording.capture_origin === 'source_beginning'
        ? 'Recording the video from the beginning of the broadcast.'
        : 'Recording the video from the live edge.';
    case 'finalizing': return 'Finishing the recorded video.';
    case 'completed':
      return recording.capture_origin === 'source_beginning'
        ? 'Video recorded from the beginning to its end and saved to your library.'
        : 'Video recorded to its end and saved to your library.';
    case 'partial': return 'A usable partial recording was saved (it did not capture the whole broadcast).';
    case 'failed': return 'The video could not be recorded.';
    default: return '';
  }
}

function describeChat(recording: LiveRecording): string {
  if (recording.status === 'waiting') return 'Waiting for the broadcast to begin before capturing chat.';
  switch (recording.chat?.status) {
    case 'pending': return 'Waiting to capture chat.';
    case 'capturing': return 'Capturing chat as it happens.';
    case 'completed': return 'Chat captured as a synchronized timed chat asset.';
    case 'unavailable': return 'This broadcast had no chat to capture.';
    case 'failed': return 'Chat could not be captured.';
    default: return '';
  }
}

/** Where capture began — the honest answer for the final result (issue #98). */
function describeCaptureOrigin(recording: LiveRecording): string | null {
  switch (recording.capture_origin) {
    case 'source_beginning': return 'Capture began at the beginning of the broadcast.';
    case 'live_edge': return 'Capture began at the live edge.';
    default: return null;
  }
}

/**
 * The explicit partial-history disclosure (issue #98). From-start is best-effort,
 * so an incomplete beginning — a missing earlier media window OR missing earlier
 * chat — is surfaced plainly, never hidden behind a false "complete recording".
 */
function describeHistory(recording: LiveRecording): string | null {
  if (recording.history === 'partial') {
    return 'Earlier history is incomplete: some of the broadcast before this recording connected is missing. From-start recording is best-effort and experimental.';
  }
  return null;
}

/** What Lumina is waiting for, or why a scheduled acquisition ended (issue #98). */
function describeWaiting(recording: LiveRecording): string | null {
  if (recording.status === 'waiting') {
    return recording.scheduled_start_at
      ? `Waiting for this broadcast to begin (scheduled for ${formatScheduledStart(recording.scheduled_start_at)}). Lumina will connect near the scheduled time and survives a restart.`
      : 'Waiting for this broadcast to begin. Lumina will connect when it goes live and survives a restart.';
  }
  switch (recording.waiting_reason) {
    case 'source_cancelled': return 'The broadcast was cancelled before it started.';
    case 'excessive_delay': return 'The broadcast was delayed too long, so Lumina stopped waiting.';
    case 'auth_expired': return 'This broadcast requires a sign-in, so Lumina stopped waiting for it to start.';
    case 'from_start_unavailable': return 'Recording from the beginning was not available for this broadcast.';
    default: return null;
  }
}

function formatScheduledStart(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString();
}
