import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { LocalPlaybackOptions, SubtitleTrack } from './types';
import type { PlaybackPrefs } from './workspace';

const api = vi.hoisted(() => ({ requestSubtitleJob: vi.fn(), getEnrichmentJob: vi.fn(), listSubtitleTracks: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const { PlaybackMenu, subtitleTrackSources } = await import('./features/watch/PlaybackMenu');

afterEach(() => { vi.resetAllMocks(); vi.useRealTimers(); });

const options: LocalPlaybackOptions = {
  mode: 'direct', reason: null, facts: null, quality_heights: [1080, 720], loudness_gain_db: -2,
  audio_tracks: [{ index: 1, language: 'eng', label: 'English 5.1', default: true }, { index: 2, language: 'fra', label: 'Français stéréo', default: false }],
};
const track = (id: string, patch: Partial<SubtitleTrack> = {}): SubtitleTrack => ({ id, label: 'English', origin: 'sidecar', format: 'text', forced: false, default: false, hearing_impaired: false, url: `/api/library/i1/subtitle-tracks/${id}.vtt`, ...patch });
const prefs: PlaybackPrefs = { normalizeLoudness: true, autoSkip: { intro: false, credits: false, recap: false }, profanity: { enabled: false, words: [] } };

function menu(overrides: Partial<Parameters<typeof PlaybackMenu>[0]> = {}) {
  const props = {
    itemId: 'i1', options, versions: [], currentVersionId: 'i1', tracks: [track('s:0'), track('i:3', { label: 'English', format: 'image', origin: 'embedded', url: null })],
    subtitle: null, onSubtitle: vi.fn(), request: {}, onRequestChange: vi.fn(), prefs, onPrefs: vi.fn(), capabilities: { ai_summaries: false, asr: false }, onTracksChanged: vi.fn(), ...overrides,
  };
  render(<PlaybackMenu {...props} />);
  return props;
}

describe('PlaybackMenu', () => {
  it('lists quality rungs, audio tracks and subtitles with their origin, and reports choices', async () => {
    const props = menu({ versions: [{ item_id: 'i1', height: 2160, hdr: true }, { item_id: 'i9', height: 1080, hdr: false }] });
    await userEvent.click(screen.getByRole('button', { name: 'Playback settings' }));
    expect(screen.getAllByRole('radio').map((radio) => radio.closest('label')?.textContent?.trim())).toEqual([
      'Original', '1080p', '720p', '4K HDR', '1080p', 'English 5.1', 'Français stéréo', 'Off', 'EnglishSubtitle file', 'English (burned in)In the file',
    ]);
    await userEvent.click(screen.getByRole('radio', { name: '720p' }));
    expect(props.onRequestChange).toHaveBeenLastCalledWith({ max_height: 720 });
    await userEvent.click(screen.getByRole('radio', { name: 'Français stéréo' }));
    expect(props.onRequestChange).toHaveBeenLastCalledWith({ audio_index: 2 });
    await userEvent.click(screen.getByRole('radio', { name: /burned in/ }));
    expect(props.onSubtitle).toHaveBeenLastCalledWith(props.tracks[1]);
    // Quality and Version both offer "1080p"; the Version group's radio is the second.
    await userEvent.click(screen.getAllByRole('radio', { name: '1080p' })[1]);
    expect(props.onRequestChange).toHaveBeenLastCalledWith({ version_id: 'i9' });
    await userEvent.click(screen.getByRole('checkbox', { name: 'Even out loudness' }));
    expect(props.onPrefs).toHaveBeenLastCalledWith({ normalizeLoudness: false });
    await userEvent.click(screen.getByRole('checkbox', { name: /Mute strong language/ }));
    expect(props.onPrefs).toHaveBeenLastCalledWith({ profanity: { enabled: true, words: [] } });
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('group', { name: 'Playback settings' })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Playback settings' }));
  });

  it('shows unavailable subtitle actions disabled with the reason', async () => {
    menu();
    await userEvent.click(screen.getByRole('button', { name: 'Playback settings' }));
    const generate = screen.getByRole('button', { name: 'Generate subtitles' });
    expect((generate as HTMLButtonElement).disabled).toBe(true);
    expect(document.getElementById(generate.getAttribute('aria-describedby') as string)?.textContent).toBe('Needs speech recognition. Ask an admin to turn this on.');
    const translate = screen.getByRole('button', { name: 'Translate' });
    expect(document.getElementById(translate.getAttribute('aria-describedby') as string)?.textContent).toBe('Needs the assistant server. Ask an admin to turn this on.');
    const sync = screen.getByRole('button', { name: 'Sync to audio' });
    expect(document.getElementById(sync.getAttribute('aria-describedby') as string)?.textContent).toBe('Choose a subtitle track to sync it.');
  });

  it('shows actions the vault owner switched off as turned off', async () => {
    menu({ capabilities: { ai_summaries: true, asr: true, disabled_features: ['subtitles_from_speech', 'sync', 'translate'] }, subtitle: 's:0' });
    await userEvent.click(screen.getByRole('button', { name: 'Playback settings' }));
    for (const name of ['Generate subtitles', 'Sync to audio', 'Translate']) {
      const button = screen.getByRole('button', { name });
      expect((button as HTMLButtonElement).disabled).toBe(true);
      expect(document.getElementById(button.getAttribute('aria-describedby') as string)?.textContent).toBe('Turned off by the vault owner.');
    }
  });

  it('explains a 409 from a feature switched off after the menu loaded', async () => {
    const { ApiRequestError } = await import('./api');
    api.requestSubtitleJob.mockRejectedValue(new ApiRequestError('ai_feature_disabled', 409));
    menu({ capabilities: { ai_summaries: true, asr: true }, subtitle: 's:0' });
    await userEvent.click(screen.getByRole('button', { name: 'Playback settings' }));
    await userEvent.click(screen.getByRole('button', { name: 'Generate subtitles' }));
    expect(await screen.findByText('Turned off by the vault owner.')).toBeTruthy();
  });

  it('starts a subtitle job and polls it until the new track arrives', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.requestSubtitleJob.mockResolvedValue({ id: 'job-1', library_item_id: 'i1', kind: 'asr', state: 'queued', created_at: '2026-09-25T00:00:00Z' });
    api.getEnrichmentJob.mockResolvedValueOnce({ id: 'job-1', library_item_id: 'i1', kind: 'asr', state: 'running', created_at: '2026-09-25T00:00:00Z' })
      .mockResolvedValue({ id: 'job-1', library_item_id: 'i1', kind: 'asr', state: 'succeeded', created_at: '2026-09-25T00:00:00Z' });
    const props = menu({ capabilities: { ai_summaries: true, asr: true }, subtitle: 's:0' });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await user.click(screen.getByRole('button', { name: 'Playback settings' }));
    await user.click(screen.getByRole('button', { name: 'Generate subtitles' }));
    expect(api.requestSubtitleJob).toHaveBeenCalledWith('i1', { kind: 'generate' });
    expect(await screen.findByText('Waiting to start…')).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(await screen.findByText('Working… this can take a few minutes.')).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    await waitFor(() => expect(props.onTracksChanged).toHaveBeenCalledTimes(1));
    expect(screen.getByText('Done. The new subtitles are in the list.')).toBeTruthy();
    await user.selectOptions(screen.getByRole('combobox', { name: /Translate to/ }), 'es');
    await user.click(screen.getByRole('button', { name: 'Translate' }));
    expect(api.requestSubtitleJob).toHaveBeenLastCalledWith('i1', { kind: 'translate', trackId: 's:0', targetLanguage: 'es' });
  });

  it('styles the panel as an overlay panel with label group heads and a Caption style sub-panel', async () => {
    const onCaptionsChange = vi.fn();
    menu({ captions: { size: 'medium', background: 'shadow' }, onCaptionsChange });
    await userEvent.click(screen.getByRole('button', { name: 'Playback settings' }));
    const panel = screen.getByRole('group', { name: 'Playback settings' });
    expect(panel.classList.contains('player-panel')).toBe(true);
    expect([...panel.querySelectorAll('.g-label')].map((node) => node.textContent)).toEqual(expect.arrayContaining(['Quality', 'Audio', 'Subtitles']));
    expect((screen.getByRole('radio', { name: 'Original' }) as HTMLInputElement).checked).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: 'Caption style…' }));
    await userEvent.click(screen.getByRole('radio', { name: 'Large' }));
    expect(onCaptionsChange).toHaveBeenLastCalledWith({ size: 'large', background: 'shadow' });
    await userEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByRole('button', { name: 'Caption style…' })).toBeTruthy();
  });

  it('turns text tracks into player tracks and leaves image tracks to the session', () => {
    expect(subtitleTrackSources([track('s:0'), track('i:3', { format: 'image', url: null })])).toEqual([{ id: 's:0', label: 'English', language: null, src: '/api/library/i1/subtitle-tracks/s:0.vtt' }]);
  });
});
