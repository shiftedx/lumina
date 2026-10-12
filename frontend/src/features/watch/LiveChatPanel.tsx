import { useEffect, useLayoutEffect, useRef, useState } from 'react';

import { getLiveChat } from '../../api';
import { ChatEventRow } from '../../chatReplayRail';
import type { TimedChatEvent } from '../../types';

const KEEP = 200;
const RETRY_MS = 5000;

type Status = 'connecting' | 'active' | 'ended' | 'unavailable';

/**
 * Current chat for a live stream. It asks the server for new messages on the server's cadence while
 * mounted; the server shares one upstream poll per stream. Chat failures never touch playback.
 */
export function LiveChatPanel({ sourceUrl }: { sourceUrl: string }) {
  const [events, setEvents] = useState<TimedChatEvent[]>([]);
  const [status, setStatus] = useState<Status>('connecting');
  const listRef = useRef<HTMLOListElement>(null);
  const pinned = useRef(true);

  useEffect(() => {
    const controller = new AbortController();
    let timer = 0;
    let cursor = 0;
    setEvents([]);
    setStatus('connecting');
    const poll = async () => {
      let wait = RETRY_MS;
      try {
        const read = await getLiveChat(sourceUrl, cursor, controller.signal);
        cursor = read.cursor;
        setStatus(read.status);
        if (read.events.length) setEvents((current) => [...current, ...read.events].slice(-KEEP));
        if (read.status !== 'active') return;
        wait = Math.max(1000, read.poll_after_ms);
      } catch {
        if (controller.signal.aborted) return;
      }
      timer = window.setTimeout(poll, wait);
    };
    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [sourceUrl]);

  // Stay at the newest message unless the member scrolled up to read.
  useLayoutEffect(() => {
    const list = listRef.current;
    if (list && pinned.current) list.scrollTop = list.scrollHeight;
  }, [events]);

  const note = status === 'connecting' ? 'Connecting to chat…'
    : status === 'unavailable' ? "Chat isn't available for this stream."
      : status === 'ended' ? 'Chat has ended.'
        : events.length ? null : 'Waiting for messages…';

  return (
    <div className="g-live-chat">
      <ol
        aria-label="Live chat messages"
        aria-live="off"
        className="chat-rail-events g-live-chat-events"
        onScroll={(event) => { const list = event.currentTarget; pinned.current = list.scrollHeight - list.scrollTop - list.clientHeight < 24; }}
        ref={listRef}
        tabIndex={0}
      >
        {events.map((event) => <ChatEventRow active={false} durationSeconds={null} event={event} key={event.id} onSeek={() => undefined} />)}
      </ol>
      {note ? <p className="g-chat-note" role="status">{note}</p> : null}
    </div>
  );
}
