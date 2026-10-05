import { useEffect, useMemo, useRef, useState } from 'react';
import { LoaderCircle, MessagesSquare, RotateCcw, Search } from 'lucide-react';

import { getChatReplay, loadChatReplay } from './api';
import {
  activeEventIndex,
  chatReplayStatusView,
  filterChatEvents,
  isSeekableOffset,
  visibleEventText,
  type ChatEventFilter,
} from './chatReplay';
import { capturedChatIdentity, type CapturedChatIdentityInput } from './playbackModel';
import type { ChatReplayAsset, TimedChatEvent, TimedChatEventKind } from './types';

/**
 * Synchronized, searchable replay chat rail. It is a deliberate member action:
 * chat is only fetched from the provider when the member presses Load. Every
 * failure path renders a degraded state and never touches the media element, so
 * playback stays fully usable whatever chat does.
 */

const MAX_FOLLOW_EVENTS = 200;
const MAX_BROWSE_RESULTS = 300;

const KIND_FILTERS: { value: TimedChatEventKind | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'message', label: 'Messages' },
  { value: 'paid_message', label: 'Paid' },
  { value: 'membership', label: 'Members' },
];

interface ChatReplayRailProps {
  sourceIdentity: string;
  sourceUrl: string;
  currentTimeSeconds: number;
  durationSeconds: number | null;
  onSeek: (seconds: number) => void;
  /** The chat was captured by the member's own live recording, not saved by the provider. */
  captured?: boolean;
}

const PROVIDER_NAMES: Record<string, string> = { youtube: 'YouTube', twitch: 'Twitch', kick: 'Kick' };

/** "YouTube" for `youtube:<id>`; source chat is always attributed to where it came from. */
export function chatProviderName(sourceIdentity: string): string {
  return PROVIDER_NAMES[sourceIdentity.split(':', 1)[0]] ?? 'the source';
}

/**
 * Explains why a live source has no chat rail when its provider only serves chat
 * to a signed-in account. There is deliberately no connect/sign-in action.
 */
export function SourceChatUnavailableNote({ provider }: { provider: 'twitch' | 'kick' | string }) {
  const name = PROVIDER_NAMES[provider] ?? 'This source';
  return (
    <p className="chat-rail-unavailable" role="note">
      <MessagesSquare aria-hidden /> {name} live chat needs a {name} account, which Lumina never asks for. The stream plays without it.
    </p>
  );
}

function failedAsset(sourceIdentity: string): ChatReplayAsset {
  return { source_identity: sourceIdentity, status: 'failed', event_count: 0, truncated: false, dropped_malformed: 0, events: [] };
}

export function ChatReplayRail({
  sourceIdentity,
  sourceUrl,
  currentTimeSeconds,
  durationSeconds,
  onSeek,
  captured = false,
}: ChatReplayRailProps) {
  const provider = chatProviderName(sourceIdentity);
  const [asset, setAsset] = useState<ChatReplayAsset | null>(null);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<TimedChatEventKind | 'all'>('all');
  const [hideModerated, setHideModerated] = useState(false);
  const [follow, setFollow] = useState(true);
  const listRef = useRef<HTMLOListElement | null>(null);
  const requestRef = useRef(0);

  // Restore any asset this member already built for this source, without
  // triggering a provider fetch. Identity changes reset the rail completely.
  useEffect(() => {
    const generation = ++requestRef.current;
    setAsset(null);
    setLoading(false);
    setQuery('');
    setKind('all');
    setHideModerated(false);
    setFollow(true);
    const controller = new AbortController();
    getChatReplay(sourceIdentity, controller.signal)
      .then((existing) => {
        if (generation === requestRef.current && existing) setAsset(existing);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [sourceIdentity]);

  async function load(refresh = false) {
    const generation = requestRef.current;
    setLoading(true);
    try {
      const result = await loadChatReplay(sourceIdentity, { source_url: sourceUrl, refresh });
      if (generation === requestRef.current) setAsset(result);
    } catch {
      // A failed load must never break Watch; show the degraded state instead.
      if (generation === requestRef.current) setAsset(failedAsset(sourceIdentity));
    } finally {
      if (generation === requestRef.current) setLoading(false);
    }
  }

  const events = asset?.events ?? [];
  const currentMs = Number.isFinite(currentTimeSeconds) ? Math.max(0, Math.round(currentTimeSeconds * 1000)) : 0;
  const searching = Boolean(query.trim()) || kind !== 'all' || hideModerated;

  const activeIndex = useMemo(() => activeEventIndex(events, currentMs), [events, currentMs]);

  const displayed = useMemo(() => {
    if (searching) {
      const filter: ChatEventFilter = { query, kind, hideModerated };
      return filterChatEvents(events, filter).slice(0, MAX_BROWSE_RESULTS);
    }
    const revealed = activeIndex >= 0 ? events.slice(0, activeIndex + 1) : [];
    return revealed.slice(-MAX_FOLLOW_EVENTS);
  }, [events, searching, query, kind, hideModerated, activeIndex]);

  // Keep the newest revealed message in view while following playback.
  useEffect(() => {
    if (searching || !follow) return;
    const node = listRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [displayed, follow, searching]);

  const view = loading ? chatReplayStatusView('building') : asset ? chatReplayStatusView(asset.status) : null;

  return (
    <aside aria-label={`Replay chat from ${provider}`} className="chat-rail g-chat-rail">
      <header className="chat-rail-header">
        <h2><MessagesSquare aria-hidden /> {view?.showsEvents || !view ? `Replay chat from ${provider}` : view.label}</h2>
        {asset && !loading ? (
          <button aria-label="Reload replay chat" className="g-text-button" onClick={() => void load(true)} type="button">
            <RotateCcw aria-hidden /> Reload
          </button>
        ) : null}
      </header>
      {/* Source chat is public and read-only; household conversation lives in Notes. */}
      <p className="chat-rail-source">
        {captured ? `Captured by Lumina while recording from ${provider}.` : `Public chat saved by ${provider}.`} Read-only — Lumina never posts to it. Talk with your household in Notes.
      </p>

      {!asset && !loading ? (
        <div className="chat-rail-cta">
          <p>Load the chat that was recorded with this broadcast.</p>
          <button className="g-button" onClick={() => void load(false)} type="button">
            <MessagesSquare aria-hidden /> Load replay chat
          </button>
        </div>
      ) : null}

      {loading ? (
        <div aria-live="polite" className="chat-rail-status">
          <LoaderCircle aria-hidden className="spin" /> <span>Loading replay chat…</span>
        </div>
      ) : null}

      {view && !loading && !view.showsEvents && asset ? (
        <div className="chat-rail-status" role="status">
          <p>{view.note || view.label}</p>
          {asset.status === 'failed' || asset.status === 'unavailable' ? (
            <button className="g-text-button" onClick={() => void load(true)} type="button">Try again</button>
          ) : null}
        </div>
      ) : null}

      {view && !loading && view.showsEvents && asset ? (
        <>
          {view.note ? <p className="chat-rail-note" role="status">{view.note}</p> : null}
          <div className="chat-rail-controls">
            <label className="chat-rail-search">
              <Search aria-hidden />
              <input
                aria-label="Search replay chat"
                onChange={(event) => setQuery(event.currentTarget.value)}
                placeholder="Search chat"
                type="search"
                value={query}
              />
            </label>
            <div aria-label="Filter chat" className="chat-rail-filters" role="group">
              {KIND_FILTERS.map((option) => (
                <button
                  aria-pressed={kind === option.value}
                  className="g-chip"
                  key={option.value}
                  onClick={() => setKind(option.value)}
                  type="button"
                >
                  {option.label}
                </button>
              ))}
            </div>
            <label className="g-check">
              <input checked={hideModerated} onChange={(event) => setHideModerated(event.currentTarget.checked)} type="checkbox" />
              <span>Hide removed</span>
            </label>
            <label className="g-check">
              <input checked={follow} disabled={searching} onChange={(event) => setFollow(event.currentTarget.checked)} type="checkbox" />
              <span>Follow playback</span>
            </label>
          </div>
          <ol className="chat-rail-events" ref={listRef}>
            {displayed.length === 0 ? (
              <li className="chat-rail-empty">{searching ? 'No matching messages.' : 'Chat will appear as playback advances.'}</li>
            ) : (
              displayed.map((event) => (
                <ChatEventRow
                  active={!searching && event.id === events[activeIndex]?.id}
                  durationSeconds={durationSeconds}
                  event={event}
                  key={event.id}
                  onSeek={onSeek}
                />
              ))
            )}
          </ol>
        </>
      ) : null}
    </aside>
  );
}

interface LibraryCapturedChatRailProps {
  item: CapturedChatIdentityInput;
  currentTimeSeconds: number;
  durationSeconds: number | null;
  onSeek: (seconds: number) => void;
}

/**
 * Replay chat for a local Library item's OWN captured recording (#100 AC6,
 * issue #110). A live recording publishes its Library item under the same
 * provider-scoped identity as its captured timed chat asset (`youtube:<id>` /
 * `twitch:<stream_id>`), so completed playback probes the member-scoped store
 * for that identity and mounts the synchronized rail only when this member's
 * captured chat actually exists. The probe is a read of the member's own
 * durable asset — never a provider fetch, never a historical backfill — and
 * any failure renders nothing so playback is never interrupted.
 */
export function LibraryCapturedChatRail({ item, currentTimeSeconds, durationSeconds, onSeek }: LibraryCapturedChatRailProps) {
  const identity = useMemo(
    () => capturedChatIdentity(item),
    [item.extractor, item.remote_id, item.webpage_url],
  );
  const [captured, setCaptured] = useState(false);
  useEffect(() => {
    setCaptured(false);
    if (!identity) return undefined;
    const controller = new AbortController();
    getChatReplay(identity, controller.signal)
      .then((existing) => {
        if (controller.signal.aborted || !existing) return;
        // Only a member-owned asset with showable events earns a rail on local
        // playback; a degraded terminal state stays silent here rather than
        // adding noise to an ordinary Library item.
        if (chatReplayStatusView(existing.status).showsEvents) setCaptured(true);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [identity]);
  if (!identity || !captured) return null;
  return (
    <ChatReplayRail
      captured
      currentTimeSeconds={currentTimeSeconds}
      durationSeconds={durationSeconds}
      onSeek={onSeek}
      sourceIdentity={identity}
      sourceUrl={item.webpage_url || ''}
    />
  );
}

export function ChatEventRow({
  event,
  active,
  durationSeconds,
  onSeek,
}: {
  event: TimedChatEvent;
  active: boolean;
  durationSeconds: number | null;
  onSeek: (seconds: number) => void;
}) {
  const seekable = isSeekableOffset(event.offset_ms, durationSeconds);
  const moderated = event.moderation !== 'visible';
  const badges = event.author?.badges ?? [];
  const timestamp = event.offset_ms !== null && event.offset_ms !== undefined ? formatOffset(event.offset_ms) : null;
  return (
    <li className={`chat-event kind-${event.kind}${moderated ? ' moderated' : ''}${active ? ' active' : ''}`}>
      {timestamp ? (
        <button
          aria-label={seekable ? `Jump to ${timestamp}` : `Chat time ${timestamp}`}
          className="chat-event-time"
          disabled={!seekable}
          onClick={() => seekable && event.offset_ms !== null && event.offset_ms !== undefined && onSeek(event.offset_ms / 1000)}
          type="button"
        >
          {timestamp}
        </button>
      ) : null}
      <div className="chat-event-body">
        <span className="chat-event-author">
          {event.author?.name || 'Viewer'}
          {badges.map((badge) => (
            <span className={`chat-badge badge-${badge}`} key={badge}>{badge}</span>
          ))}
          {event.amount ? <span className="chat-amount">{event.amount}</span> : null}
        </span>
        <span className="chat-event-text">{visibleEventText(event)}</span>
      </div>
    </li>
  );
}

function formatOffset(offsetMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(offsetMs / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const pad = (value: number) => value.toString().padStart(2, '0');
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${minutes}:${pad(seconds)}`;
}
