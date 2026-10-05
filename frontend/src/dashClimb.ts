import type { MediaPlayerClass } from 'dashjs';

/** Only whole segments this big are throughput samples: tiny index/init ranges measure latency, not bandwidth. */
const MIN_SAMPLE_BYTES = 100 * 1024;
const SAMPLES = 3;
const HEADROOM = 0.8;
const MIN_BUFFER_SECONDS = 10;
const CHECK_MS = 2000;
const SAMPLE_MAX_AGE_MS = 30_000;

type Rung = { id: string; bandwidth: number };

/**
 * Owner rule: start at the top, step down only on real trouble, and return to the top when it passes. dash.js's own
 * estimate is unusable through the relay (it read 3-9 Mb/s against 80-950 Mb/s deliveries), so its throughput and BOLA
 * rules are off; this climbs instead, to the highest rung the slowest of the last three whole segments carries with
 * headroom, and only with a healthy buffer. It never steps down: abandon, buffer and dropped-frame rules do that.
 */
export function climbTarget(rungs: Rung[], currentId: string, recentBps: number[], bufferSeconds: number): string | null {
  if (recentBps.length < SAMPLES || !(bufferSeconds >= MIN_BUFFER_SECONDS)) return null; // NaN: no buffer yet
  const sustained = Math.min(...recentBps.slice(-SAMPLES)) * HEADROOM;
  const ordered = [...rungs].sort((a, b) => a.bandwidth - b.bandwidth);
  const current = ordered.findIndex((rung) => rung.id === currentId);
  if (current < 0) return null;
  let target = current;
  for (let index = current + 1; index < ordered.length; index += 1) if (ordered[index].bandwidth <= sustained) target = index;
  return target > current ? ordered[target].id : null;
}

type HttpRequest = { type?: string; trace?: { b?: number[] }[]; trequest?: Date; _tfinish?: Date };

/** Throughput of each whole media segment requested since ``sinceMs`` (wall clock), request to last byte, oldest first. */
function segmentBps(requests: HttpRequest[], sinceMs: number): number[] {
  const finished = [...requests].sort((a, b) => (a._tfinish?.getTime() ?? 0) - (b._tfinish?.getTime() ?? 0));
  return finished.flatMap((request) => {
    const bytes = (request.trace || []).reduce((sum, chunk) => sum + (chunk.b?.[0] || 0), 0);
    if (request.type !== 'MediaSegment' || bytes < MIN_SAMPLE_BYTES || !request._tfinish || !request.trequest) return [];
    if (request.trequest.getTime() < sinceMs || request._tfinish.getTime() < Date.now() - SAMPLE_MAX_AGE_MS) return [];
    const ms = request._tfinish.getTime() - request.trequest.getTime();
    return ms > 0 ? [(bytes * 8 * 1000) / ms] : [];
  });
}

/**
 * Check every 2 s whether Auto can climb; returns a stop function. Only segments fetched since the rung last changed
 * (either way) and in the last 30 s count: a full buffer fetches nothing, and old fast samples must not outvote a
 * network that has since slowed.
 */
export function startDashClimb(player: MediaPlayerClass): () => void {
  let rungId: string | null = null;
  let rungSince = Date.now();
  let asked: string | null = null; // one request per climb; dash.js takes a segment or two to switch
  const timer = globalThis.setInterval(() => {
    try {
      const current = player.getCurrentRepresentationForType('video');
      if (!current) return;
      if (rungId !== null && current.id !== rungId) {
        rungSince = Date.now();
        asked = null;
      }
      rungId = current.id;
      const requests = player.getDashMetrics().getHttpRequests('video') as unknown as HttpRequest[];
      const target = climbTarget(player.getRepresentationsByType('video'), current.id, segmentBps(requests, rungSince), player.getBufferLength('video'));
      if (target && target !== asked) {
        asked = target;
        player.setRepresentationForTypeById('video', target);
      }
    } catch {
      // Not ready yet (no stream) or reset: try again next tick.
    }
  }, CHECK_MS);
  return () => globalThis.clearInterval(timer);
}
