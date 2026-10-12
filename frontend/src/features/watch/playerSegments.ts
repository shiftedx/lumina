import type { MediaSegment, SegmentType } from '../../types';

/** Segment types the player offers to skip; preview and commercial are not offered. */
export const SKIPPABLE: ReadonlySet<SegmentType> = new Set(['intro', 'recap', 'credits']);
export const UP_NEXT_LEAD_SECONDS = 30;

/** The skippable segment playing at `t` (start inclusive, end exclusive); in an overlap the latest-starting one wins. */
export function activeSegment(t: number, segments: readonly MediaSegment[]): MediaSegment | null {
  let active: MediaSegment | null = null;
  for (const segment of segments) {
    if (!SKIPPABLE.has(segment.type) || t < segment.start_seconds || t >= segment.end_seconds) continue;
    if (!active || segment.start_seconds > active.start_seconds) active = segment;
  }
  return active;
}

/** When the up-next card appears: at credits, else 30 s before the end; null for short or unknown media. */
export function upNextAt(duration: number | null, segments: readonly MediaSegment[]): number | null {
  const credits = segments.find((segment) => segment.type === 'credits');
  if (credits) return credits.start_seconds;
  return duration && duration > UP_NEXT_LEAD_SECONDS * 2 ? duration - UP_NEXT_LEAD_SECONDS : null;
}

/** Shows the text track whose id (a subtitle track id such as `s:0`) matches, and disables the rest; null turns subtitles off. */
export function showTextTrack(tracks: ArrayLike<{ id: string; mode: TextTrackMode }> | null | undefined, id: string | null): void {
  if (!tracks) return;
  for (let index = 0; index < tracks.length; index += 1) tracks[index].mode = tracks[index].id === id ? 'showing' : 'disabled';
}
