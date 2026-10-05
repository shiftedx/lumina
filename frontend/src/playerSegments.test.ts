import { describe, expect, it } from 'vitest';

import { activeSegment, showTextTrack, upNextAt } from './features/watch/playerSegments';
import type { MediaSegment } from './types';

const segment = (type: MediaSegment['type'], start: number, end: number): MediaSegment => ({ type, start_seconds: start, end_seconds: end, source: 'fingerprint', confidence: 0.9 });

describe('player segments', () => {
  it('finds the skippable segment at a time: start inclusive, end exclusive, latest start wins an overlap', () => {
    const segments = [segment('recap', 0, 60), segment('intro', 50, 90), segment('preview', 1300, 1320), segment('credits', 1250, 1320)];
    expect(activeSegment(0, segments)?.type).toBe('recap');
    expect(activeSegment(55, segments)?.type).toBe('intro');
    expect(activeSegment(90, segments)).toBeNull();
    expect(activeSegment(49.99, segments)?.type).toBe('recap');
    expect(activeSegment(1310, segments)?.type).toBe('credits');
    expect(activeSegment(5, [segment('commercial', 0, 10)])).toBeNull();
  });

  it('places the up-next card at credits, else 30 s before the end, and never on short media', () => {
    expect(upNextAt(1320, [segment('credits', 1250, 1320)])).toBe(1250);
    expect(upNextAt(1320, [])).toBe(1290);
    expect(upNextAt(45, [])).toBeNull();
    expect(upNextAt(null, [])).toBeNull();
  });

  it('shows one text track and disables the others', () => {
    const tracks = [{ id: 's:0', mode: 'showing' as TextTrackMode }, { id: 't:abc', mode: 'disabled' as TextTrackMode }];
    showTextTrack(tracks, 't:abc');
    expect(tracks.map((track) => track.mode)).toEqual(['disabled', 'showing']);
    showTextTrack(tracks, null);
    expect(tracks.map((track) => track.mode)).toEqual(['disabled', 'disabled']);
    showTextTrack(undefined, 's:0');
  });
});
