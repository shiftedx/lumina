/**
 * The chat seam: a fixed place in the side column where chat belongs, so a future
 * live-chat viewer mounts here without touching the layout. A live stream whose chat Lumina can read (YouTube, Twitch)
 * shows it (2.2.0: one shared server reader per stream, only while someone reads it); a replay uses the existing rail;
 * Kick explains it needs an account.
 */
import type { ReactNode } from 'react';

import { ChatReplayRail, SourceChatUnavailableNote } from '../../chatReplayRail';
import type { MediaLifecycle, MediaSourceCapabilities } from '../../types';
import { LiveChatPanel } from './LiveChatPanel';

export type WatchChatSeamProps = {
  provider: string;
  capabilities: MediaSourceCapabilities | null | undefined;
  sourceIdentity: string | null;
  sourceUrl: string | null;
  /** The relay reported the end: no live chat line any more. */
  ended: boolean;
  currentTime: number;
  duration: number | null;
  onSeek: (seconds: number) => void;
  /** Phone: the seam collapses to a <details> "Chat". */
  phone: boolean;
};

const LIVE: ReadonlySet<MediaLifecycle> = new Set(['live']);

export function WatchChatSeam({ provider, capabilities, sourceIdentity, sourceUrl, ended, currentTime, duration, onSeek, phone }: WatchChatSeamProps) {
  let content: ReactNode = null;
  if (sourceIdentity && sourceUrl && capabilities?.chat?.replay === 'available') {
    content = <ChatReplayRail currentTimeSeconds={currentTime} durationSeconds={duration} onSeek={onSeek} sourceIdentity={sourceIdentity} sourceUrl={sourceUrl} />;
  } else if (!ended && capabilities?.chat?.live_reason === 'authentication_required') {
    content = <SourceChatUnavailableNote provider={capabilities.provider || provider} />;
  } else if (!ended && sourceUrl && capabilities && LIVE.has(capabilities.lifecycle) && capabilities.chat?.live === 'available') {
    content = <LiveChatPanel sourceUrl={sourceUrl} />;
  }
  if (!content) return null;
  if (phone) return <details className="g-chat-seam"><summary>Chat</summary>{content}</details>;
  return <section aria-labelledby="g-chat-seam-title" className="g-chat-seam"><h2 className="g-label" id="g-chat-seam-title">Chat</h2>{content}</section>;
}
