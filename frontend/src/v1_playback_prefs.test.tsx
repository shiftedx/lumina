import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { cleanWordList, MUTE_COPY } from './features/settings/youCards';
import { SettingsSurface } from './features/settings/SettingsSurface';
import { TMDB_ATTRIBUTION } from './features/titles/titleModel';
import type { UserProfile } from './types';
import type { PlaybackPrefs } from './workspace';

const user = { id: 'u1', username: 'one', display_name: 'One', role: 'viewer', is_active: true } as UserProfile;
const prefs: PlaybackPrefs = { normalizeLoudness: true, autoSkip: { intro: false, credits: false, recap: false }, profanity: { enabled: true, words: ['heck'] } };

function settings(onPlaybackPrefsChange = vi.fn(), section: 'playback' | 'about' = 'playback') {
  render(<SettingsSurface formatPreset="best" onFormatChange={vi.fn()} onLogout={vi.fn()} onPlaybackPrefsChange={onPlaybackPrefsChange} playbackPrefs={prefs} section={section} user={user} />);
  return onPlaybackPrefsChange;
}

describe('playback preferences', () => {
  it('cleans a pasted word list and explains what cannot be saved', () => {
    expect(cleanWordList('  heck\n\nDarn, darn\nfrick*  ')).toEqual({ words: ['heck', 'Darn', 'frick*'], problem: null });
    expect(cleanWordList(`ok\n${'x'.repeat(41)}`)).toEqual({ words: ['ok'], problem: '1 entry is longer than 40 characters. Shorten it to save.' });
    const many = Array.from({ length: 501 }, (_, index) => `w${index}`).join('\n');
    expect(cleanWordList(many).problem).toBe('Keep the list to 500 words or fewer (501 now).');
  });

  it('offers loudness, skip and mute settings with the spec copy, and saves only a valid list', async () => {
    const onChange = settings();
    expect(screen.getByText(MUTE_COPY)).toBeTruthy();
    expect(MUTE_COPY).toBe("Applies in Lumina's web player only. Infuse and other apps play unfiltered. Accuracy depends on the transcript: generated transcripts can miss or mishear words.");
    await userEvent.click(screen.getByRole('switch', { name: 'Even out loudness' }));
    expect(onChange).toHaveBeenLastCalledWith({ normalizeLoudness: false });
    await userEvent.click(screen.getByRole('switch', { name: 'Skip intros' }));
    expect(onChange).toHaveBeenLastCalledWith({ autoSkip: { intro: true, credits: false, recap: false } });
    const words = screen.getByRole('textbox', { name: 'Your extra words' });
    await userEvent.type(words, `\n${'x'.repeat(41)}`);
    await userEvent.click(screen.getByRole('button', { name: 'Save words' }));
    expect(screen.getByText('1 entry is longer than 40 characters. Shorten it to save.')).toBeTruthy();
    expect(onChange).not.toHaveBeenLastCalledWith(expect.objectContaining({ profanity: expect.anything() }));
    await userEvent.clear(words);
    await userEvent.type(words, 'heck\nfrick*');
    await userEvent.click(screen.getByRole('button', { name: 'Save words' }));
    expect(onChange).toHaveBeenLastCalledWith({ profanity: { enabled: true, words: ['heck', 'frick*'] } });
  });

  it('credits TMDB, yt-dlp and the other upstreams in About', () => {
    settings(vi.fn(), 'about');
    expect(screen.getByRole('heading', { level: 2, name: 'About' })).toBeTruthy();
    expect(screen.getByText(TMDB_ATTRIBUTION)).toBeTruthy();
    expect(screen.getByText(/built on yt-dlp, FFmpeg/)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'All credits' }).getAttribute('href')).toBe('https://github.com/shiftedx/lumina/blob/main/CREDITS.md');
  });
});
