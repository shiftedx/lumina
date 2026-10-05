import { Maximize2, Pause, Play, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { IconButton } from '../../ui';

const LINE_INTERVAL_MS = 250; // the progress line updates at most 4 times a second
const TOAST_GAP_PX = 12;

/**
 * Controls for the one Watch media element while it is docked. It drives the existing
 * <video>/<audio> directly, so no second stream is ever opened; the card is the same Watch surface placed by CSS.
 */
export function MiniPlayer({ title, subtitle, onExpand, onClose }: { title: string; subtitle?: string; onExpand: () => void; onClose: () => void }) {
  const barRef = useRef<HTMLDivElement>(null);
  const lineRef = useRef<HTMLSpanElement>(null);
  const [paused, setPaused] = useState(true);
  const host = () => barRef.current?.parentElement ?? null;
  const media = () => host()?.querySelector<HTMLMediaElement>('video, audio') ?? null;

  useEffect(() => {
    const root = host();
    if (!root) return undefined;
    // Media events do not bubble, but they do capture, so this follows a replaced element without re-subscribing.
    const sync = (event: Event) => setPaused((event.target as HTMLMediaElement).paused);
    let last = 0;
    const progress = (event: Event) => {
      const now = Date.now();
      if (now - last < LINE_INTERVAL_MS) return;
      last = now;
      const element = event.target as HTMLMediaElement;
      const ratio = element.duration > 0 ? Math.min(1, element.currentTime / element.duration) : 0;
      lineRef.current?.style.setProperty('--progress', String(ratio));
    };
    setPaused(media()?.paused ?? true);
    for (const type of ['play', 'pause', 'ended', 'emptied']) root.addEventListener(type, sync, true);
    root.addEventListener('timeupdate', progress, true);
    return () => {
      for (const type of ['play', 'pause', 'ended', 'emptied']) root.removeEventListener(type, sync, true);
      root.removeEventListener('timeupdate', progress, true);
    };
  }, []);

  // The toast region sits above the card while it shows, so the two never overlap.
  useEffect(() => {
    const root = host();
    if (!root || typeof ResizeObserver === 'undefined') return undefined;
    const style = document.documentElement.style;
    const observer = new ResizeObserver(([entry]) => {
      const height = entry.borderBoxSize?.[0]?.blockSize ?? entry.contentRect.height;
      style.setProperty('--g-toast-offset', `${Math.round(height) + TOAST_GAP_PX}px`);
    });
    observer.observe(root);
    return () => { observer.disconnect(); style.removeProperty('--g-toast-offset'); };
  }, []);

  function toggle() {
    const element = media();
    if (!element) return;
    if (element.paused) void element.play().catch(() => undefined);
    else element.pause();
  }

  return (
    <div aria-label={`Mini player: ${title}`} className="mini-player-bar" ref={barRef} role="region">
      <span aria-hidden="true" className="mini-player-progress" ref={lineRef} />
      <button className="mini-player-copy" onClick={onExpand} tabIndex={-1} type="button">
        <span className="mini-player-title">{title}</span>
        <span className="g-label mini-player-sub">{paused ? 'Paused' : subtitle ?? ''}</span>
      </button>
      <IconButton icon={paused ? <Play fill="currentColor" /> : <Pause fill="currentColor" />} label={paused ? 'Play' : 'Pause'} onClick={toggle} variant="overlay" />
      <IconButton className="mini-player-expand" icon={<Maximize2 />} label="Expand player" onClick={onExpand} variant="overlay" />
      <IconButton icon={<X />} label="Close player" onClick={() => { media()?.pause(); onClose(); }} variant="overlay" />
    </div>
  );
}
