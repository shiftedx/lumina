import { describe, expect, it } from 'vitest';

import { jobProgress, withHistoryEntry, youtubeVideoId } from './luminaModel';

describe('Lumina discovery model', () => {
  it('moves a re-searched history entry to the front, deduped case-insensitively', () => {
    const current = [
      { id: 'a', query: 'cats', searched_at: '2026-01-01T00:00:00Z' },
      { id: 'b', query: 'dogs', searched_at: '2026-01-01T00:00:00Z' },
    ];
    const touched = { id: 'c', query: 'Cats', searched_at: '2026-01-02T00:00:00Z' };
    expect(withHistoryEntry(touched, current)).toEqual([touched, current[1]]);
  });

  it('recognizes common YouTube URLs and normalized progress', () => {
    expect(youtubeVideoId({ webpage_url: 'https://youtu.be/abc12345' })).toBe('abc12345');
    expect(jobProgress({ id: '1', source_url: 'x', status: 'running', progress: 42 })).toBe(42);
  });
});
