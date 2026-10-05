import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ createLibraryNote: vi.fn(), deleteLibraryNote: vi.fn() }));
vi.mock('./api', () => api);

import { buildMoments, MomentsPanel, type Moment } from './features/watch/MomentsPanel';
import type { Summary } from './api';
import type { LibraryNote } from './types';

afterEach(() => { vi.clearAllMocks(); });

const summary = {
  id: 's', library_item_id: 'film', transcript_id: 't', transcript_revision: 1, model_id: 'm', state: 'succeeded', overview: 'o',
  key_points: [{ text: 'Bridge is rebuilt', cue_ordinals: [4, 5], start_ms: 30_000 }], chapters: [{ title: 'Intro', cue_ordinal: 0, start_ms: 0 }],
  dropped_points: 0, error: null, created_at: '', completed_at: '',
} as Summary;
const note = (patch: Partial<LibraryNote>): LibraryNote => ({ id: 'n', item_id: 'film', visibility: 'private', body: 'Mine', is_owner: true, can_delete: true, ...patch });

describe('Moments', () => {
  it('test_moment_origins_distinct: source, member and AI moments stay separate and ordered', () => {
    const moments = buildMoments(
      [{ start_time: 10, title: 'Source chapter' }],
      summary,
      [note({ id: 'mine', timestamp_ms: 12_300 }), note({ id: 'theirs', is_owner: false, can_delete: false, visibility: 'household', body: 'Look\nmore', author_display_name: 'Ana', timestamp_ms: 20_000 }), note({ id: 'untimed', timestamp_ms: null })],
    );
    expect(moments.map((m) => [m.seconds, m.origin, m.title])).toEqual([
      [0, 'suggested', 'Intro'], [10, 'source', 'Source chapter'], [12.3, 'you', 'Mine'], [20, 'household', 'Look'], [30, 'suggested', 'Bridge is rebuilt'],
    ]);
    expect(moments.find((m) => m.origin === 'household')).toMatchObject({ noteId: undefined, detail: 'Bookmark by Ana · shared with household' });
    expect(moments.at(-1)?.detail).toBe('AI-suggested key point · grounded in 2 transcript lines');
  });

  it('test_moment_create_seek_roundtrip: bookmarks the current time, seeks, hides AI suggestions on request', async () => {
    const browser = userEvent.setup();
    const onSeek = vi.fn();
    const onBookmarksChanged = vi.fn();
    const onShowSuggested = vi.fn();
    api.createLibraryNote.mockResolvedValue(note({}));
    const moments: Moment[] = buildMoments([{ start_time: 10, title: 'Source chapter' }], summary, [note({ id: 'mine', timestamp_ms: 12_300 })]);
    const { rerender } = render(<MomentsPanel currentTime={12.3} getTime={() => 12.3} itemId="film" moments={moments} onBookmarksChanged={onBookmarksChanged} onSeek={onSeek} onShowSuggested={onShowSuggested} showSuggested />);

    await browser.type(screen.getByRole('textbox', { name: 'Bookmark name (optional)' }), 'Great line');
    await browser.click(screen.getByRole('button', { name: 'Bookmark 0:12' }));
    expect(api.createLibraryNote).toHaveBeenCalledWith('film', { body: 'Great line', visibility: 'private', timestamp_ms: 12_300 });
    expect(onBookmarksChanged).toHaveBeenCalled();

    const list = screen.getByRole('list', { name: 'Moments' });
    const current = within(list).getByRole('button', { name: /^Seek to 0:12: Mine/ });
    expect(current.getAttribute('aria-current')).toBe('true');
    await browser.click(current);
    expect(onSeek).toHaveBeenCalledWith(12.3);
    await browser.click(screen.getByRole('button', { name: 'Remove bookmark Mine' }));
    expect(api.deleteLibraryNote).toHaveBeenCalledWith('mine');

    await browser.click(screen.getByRole('switch', { name: 'Show AI suggestions' }));
    expect(onShowSuggested).toHaveBeenCalledWith(false);
    rerender(<MomentsPanel currentTime={0} getTime={() => 0} moments={moments} onBookmarksChanged={onBookmarksChanged} onSeek={onSeek} onShowSuggested={onShowSuggested} showSuggested={false} />);
    expect(within(screen.getByRole('list', { name: 'Moments' })).getAllByRole('button').map((b) => b.textContent?.slice(0, 4))).toEqual(['0:10', '0:12', 'Remo']);
    expect(screen.queryByRole('textbox')).toBeNull(); // streamed media has no owned record to bookmark
  });
});
