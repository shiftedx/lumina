import type { MediaPlayerClass } from 'dashjs';

type Request = {
  type?: string;
  responsecode?: number;
  _mediaduration?: number;
  trace?: { b?: number[] }[];
  tresponse?: Date;
  _tfinish?: Date;
  _resourceTimingValues?: { transferSize: number; decodedBodySize: number; responseStart: number; responseEnd: number };
};

type Parent = { addEntry: (mediaType: string, request: Request) => void };

/** SegmentBase reuses a URL for index and media ranges. dash.js 5.2's Resource Timing lookup takes the first
 * matching URL, so later segments inherit the index's byte count and duration. Use this request's transfer
 * instead, and leave index probes (also labelled MediaSegment, but without a duration) out of ABR entirely.
 */
export function configureDashThroughput(player: Pick<MediaPlayerClass, 'extend'>): void {
  player.extend('ThroughputModel', function (this: { parent: Parent }) {
    const addEntry = this.parent.addEntry.bind(this.parent);
    return {
      addEntry(mediaType: string, request: Request) {
        if (request.type !== 'MediaSegment') return addEntry(mediaType, request);
        if (request.responsecode !== 200 && request.responsecode !== 206) return;
        if (request._mediaduration === undefined || !Number.isFinite(request._mediaduration) || request._mediaduration <= 0) return;
        const bytes = (request.trace || []).reduce((sum, chunk) => sum + (chunk.b?.[0] || 0), 0);
        const start = request.tresponse?.getTime();
        const end = request._tfinish?.getTime();
        if (!(bytes > 0) || !Number.isFinite(bytes) || start === undefined || end === undefined
          || !Number.isFinite(start) || !Number.isFinite(end) || end < start) return;
        // Keep the original HTTP metric intact for diagnostics. Only the model sees the corrected timing.
        addEntry(mediaType, {
          ...request,
          _resourceTimingValues: { transferSize: bytes, decodedBodySize: bytes, responseStart: start, responseEnd: Math.max(start + 1, end) },
        });
      },
    };
  }, true);
}
