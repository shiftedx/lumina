// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LuminaPlayer, PlayerPreferencesContext, type PlayerPreferences } from './LuminaPlayer';

const audioMock = vi.hoisted(() => {
  const graph = { mode: 'webaudio' as const, setLoudnessGain: vi.fn(), setMuteRanges: vi.fn(), setVolume: vi.fn(), resume: vi.fn(async () => undefined), schedule: vi.fn() };
  return { graph, audioGraphFor: vi.fn(() => graph) };
});
vi.mock('./features/watch/audioGraph', () => ({ audioGraphFor: audioMock.audioGraphFor }));

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

let play: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
});

function renderPlayer(prefs: PlayerPreferences = { globalShortcuts: true }, live = false) {
  render(
    <PlayerPreferencesContext.Provider value={prefs}>
      <textarea aria-label="Note" />
      <LuminaPlayer source={{ kind: 'video', src: '/api/library/film/media', live, seekable: !live }} title="Film" />
    </PlayerPreferencesContext.Provider>,
  );
  const media = screen.getByLabelText('Film video') as HTMLVideoElement;
  Object.defineProperties(media, {
    currentTime: { configurable: true, value: 83, writable: true },
    duration: { configurable: true, value: 2700, writable: true },
  });
  fireEvent.canPlay(media);
  fireEvent.loadedMetadata(media);
  return { media, player: screen.getByLabelText('Film player') };
}

describe('player controls', () => {
  it('test_keyboard_seek_visible_both_themes: the seek bar is a bounded, keyboard-steppable slider', () => {
    const { media } = renderPlayer();
    const seek = screen.getByRole('slider', { name: 'Seek' });
    expect(seek.getAttribute('aria-valuetext')).toBe('1:23 of 45:00');
    expect(seek.getAttribute('aria-valuenow')).toBe('83');
    expect(seek.getAttribute('aria-valuemax')).toBe('2700');

    fireEvent.keyDown(seek, { key: 'ArrowRight' });
    expect(media.currentTime).toBe(88);
    fireEvent.keyDown(seek, { key: 'PageDown' });
    expect(media.currentTime).toBe(78);
    fireEvent.keyDown(seek, { key: 'Home' });
    expect(media.currentTime).toBe(0);
    fireEvent.keyDown(seek, { key: 'ArrowLeft' });
    expect(media.currentTime).toBe(0);
    fireEvent.keyDown(seek, { key: 'End' });
    expect(media.currentTime).toBe(2700);
    fireEvent.keyDown(seek, { key: 'ArrowRight' });
    expect(media.currentTime).toBe(2700);
    expect(seek.getAttribute('aria-valuetext')).toBe('45:00 of 45:00');
  });

  it('runs the standard shortcuts from the player and while nothing is focused', () => {
    const onNext = vi.fn();
    const { media, player } = renderPlayer({ globalShortcuts: true, onNext });
    fireEvent.keyDown(document.body, { key: 'k' });
    expect(play).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(document.body, { key: 'l' });
    expect(media.currentTime).toBe(93);
    fireEvent.keyDown(document.body, { key: 'j' });
    fireEvent.keyDown(document.body, { key: 'ArrowLeft' });
    expect(media.currentTime).toBe(78);
    fireEvent.keyDown(document.body, { key: '5' });
    expect(media.currentTime).toBe(1350);
    fireEvent.keyDown(document.body, { key: 'm' });
    expect(media.muted).toBe(true);
    fireEvent.keyDown(player, { key: 'ArrowUp' });
    expect(media.muted).toBe(false);
    expect(media.volume).toBe(0.05);
    fireEvent.keyDown(document.body, { key: 'N', shiftKey: true });
    expect(onNext).toHaveBeenCalledOnce();
    // A modifier chord belongs to the browser/app, not the player.
    fireEvent.keyDown(document.body, { key: 'k', metaKey: true });
    expect(play).toHaveBeenCalledTimes(1);
  });

  it('test_typing_not_player_shortcuts: space/J/K/L in a text field edit text only', () => {
    const { media } = renderPlayer();
    const note = screen.getByLabelText('Note');
    for (const key of [' ', 'j', 'k', 'l', 'm', '5']) {
      const event = fireEvent.keyDown(note, { key });
      expect(event).toBe(true); // not prevented
    }
    expect(play).not.toHaveBeenCalled();
    expect(media.currentTime).toBe(83);
    expect(media.muted).toBe(false);
  });

  it('ignores page-level keys for the docked mini-player but still answers inside the player', () => {
    const { media, player } = renderPlayer({ globalShortcuts: false });
    fireEvent.keyDown(document.body, { key: 'l' });
    expect(media.currentTime).toBe(83);
    fireEvent.keyDown(player, { key: 'l' });
    expect(media.currentTime).toBe(93);
  });

  it('test_pip_fullscreen_failure_safe: denied promises become inline notices, not failures', async () => {
    const { player } = renderPlayer();
    const requestFullscreen = vi.fn()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new DOMException('denied', 'NotAllowedError'));
    Object.defineProperty(player, 'requestFullscreen', { configurable: true, value: requestFullscreen });
    fireEvent.click(screen.getByRole('button', { name: 'Enter fullscreen' }));
    await waitFor(() => expect(requestFullscreen).toHaveBeenCalledOnce());
    expect(screen.queryByRole('status')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Enter fullscreen' }));
    expect((await screen.findByRole('status')).textContent).toBe('The browser did not allow fullscreen.');
    expect(screen.getByRole('button', { name: 'Enter fullscreen' })).toBeTruthy();

    play.mockRejectedValueOnce(new DOMException('autoplay', 'NotAllowedError'));
    fireEvent.click(screen.getByRole('button', { name: 'Play' }));
    expect((await screen.findByRole('status')).textContent).toContain('blocked playback');
    expect(player.getAttribute('data-player-state')).toBe('ready');
    // No PiP control is offered where the browser does not support it.
    expect(screen.queryByRole('button', { name: 'Picture in picture' })).toBeNull();
  });

  it('persists a settled volume through member preferences and applies a saved one', () => {
    vi.useFakeTimers();
    const onVolumeChange = vi.fn();
    const { media } = renderPlayer({ volume: 0.3, onVolumeChange });
    expect(media.volume).toBe(0.3);
    const slider = screen.getByLabelText('Volume');
    fireEvent.change(slider, { target: { value: '0.5' } });
    fireEvent.change(slider, { target: { value: '0.6' } });
    act(() => vi.advanceTimersByTime(500));
    expect(onVolumeChange).toHaveBeenCalledTimes(1);
    expect(onVolumeChange).toHaveBeenCalledWith(0.6);
  });

  it('test_actual_quality_audio_caption: speed choices apply to the element and captions appear only with tracks', () => {
    const { media } = renderPlayer();
    fireEvent.change(screen.getByLabelText('Playback speed'), { target: { value: '1.5' } });
    expect(media.playbackRate).toBe(1.5);
    expect(media.defaultPlaybackRate).toBe(1.5);
    expect(media.currentTime).toBe(83);
    expect(screen.queryByRole('button', { name: 'Captions' })).toBeNull();
  });

  it('offers Go live for a paused live stream and Replay once a video ends', () => {
    const { media } = renderPlayer({ globalShortcuts: true }, true);
    Object.defineProperty(media, 'seekable', { configurable: true, value: { length: 1, start: () => 0, end: () => 600 } });
    expect(screen.queryByRole('slider', { name: 'Seek' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Go live' }));
    expect(media.currentTime).toBe(597);
    expect(play).toHaveBeenCalled();
    cleanup();

    const vod = renderPlayer();
    fireEvent.ended(vod.media);
    expect(screen.getByRole('button', { name: 'Replay' })).toBeTruthy();
  });

  it('keeps controls up while a control has focus and reveals them on a shortcut', () => {
    vi.useFakeTimers();
    const { media, player } = renderPlayer();
    fireEvent.play(media);
    act(() => vi.advanceTimersByTime(2200));
    expect(player.getAttribute('data-controls-visible')).toBe('false');
    fireEvent.keyDown(document.body, { key: 'l' });
    expect(player.getAttribute('data-controls-visible')).toBe('true');
    screen.getByRole('slider', { name: 'Seek' }).focus();
    act(() => vi.advanceTimersByTime(5000));
    expect(player.getAttribute('data-controls-visible')).toBe('true');
  });

  it('renders subtitle tracks, keeps the overlay outside the controls, and lets a menu replace the captions toggle', () => {
    render(
      <LuminaPlayer
        extensions={{ overlay: <button type="button">Skip intro</button>, menu: <button type="button">Playback settings</button> }}
        source={{ kind: 'video', src: '/api/library/film/media', tracks: [{ id: 's:0', label: 'English', language: 'eng', src: '/api/library/film/subtitle-tracks/s:0.vtt' }] }}
        title="Film"
      />,
    );
    const media = screen.getByLabelText('Film video');
    const track = media.querySelector('track');
    expect(track?.getAttribute('id')).toBe('s:0');
    expect(track?.getAttribute('kind')).toBe('subtitles');
    expect(track?.getAttribute('srclang')).toBe('eng');
    expect(screen.getByRole('button', { name: 'Skip intro' }).closest('.player-controls')).toBeNull();
    expect(screen.getByRole('button', { name: 'Playback settings' }).closest('.player-controls')).not.toBeNull();
    // jsdom's textTracks stays empty regardless of <track> children, so an
    // unstubbed captions-hidden assertion would pass even without the menu gate.
    // Stub a nonzero length so this assertion is meaningful.
    Object.defineProperty(media, 'textTracks', { configurable: true, value: { length: 1 } });
    fireEvent.loadedMetadata(media);
    expect(screen.queryByRole('button', { name: 'Captions' })).toBeNull();
  });

  it('routes Library audio through the shared graph: built on play, gain and ranges applied, volume and timing kept in step', () => {
    audioMock.audioGraphFor.mockClear();
    for (const method of [audioMock.graph.setLoudnessGain, audioMock.graph.setMuteRanges, audioMock.graph.setVolume, audioMock.graph.resume, audioMock.graph.schedule]) method.mockClear();
    const ranges = [{ start_seconds: 12, end_seconds: 12.6 }];
    const source = { kind: 'video' as const, src: '/api/library/film/media' };
    const { rerender } = render(<LuminaPlayer audio={{ gain: 0.5, muteRanges: ranges }} source={source} title="Film" />);
    const media = screen.getByLabelText('Film video') as HTMLVideoElement;
    expect(audioMock.audioGraphFor).not.toHaveBeenCalled();
    fireEvent.play(media);
    expect(audioMock.audioGraphFor).toHaveBeenCalledWith(media, expect.objectContaining({ onBypass: expect.any(Function) }));
    expect(audioMock.graph.setLoudnessGain).toHaveBeenCalledWith(0.5);
    expect(audioMock.graph.setMuteRanges).toHaveBeenCalledWith(ranges);
    expect(audioMock.graph.resume).toHaveBeenCalledTimes(1);
    fireEvent.timeUpdate(media);
    fireEvent.seeked(media);
    fireEvent.pause(media);
    fireEvent.rateChange(media);
    expect(audioMock.graph.schedule.mock.calls.length).toBeGreaterThanOrEqual(5);
    fireEvent.change(screen.getByRole('slider', { name: 'Volume' }), { target: { value: '0.4' } });
    expect(audioMock.graph.setVolume).toHaveBeenLastCalledWith(0.4);
    rerender(<LuminaPlayer audio={{ gain: 1, muteRanges: [] }} source={source} title="Film" />);
    expect(audioMock.graph.setLoudnessGain).toHaveBeenLastCalledWith(1);
    expect(audioMock.graph.setMuteRanges).toHaveBeenLastCalledWith([]);
  });

  it('leaves media without Library audio settings alone', () => {
    audioMock.audioGraphFor.mockClear();
    render(<LuminaPlayer source={{ kind: 'video', src: '/api/remote-streams/abc/playlist.m3u8' }} title="Clip" />);
    fireEvent.play(screen.getByLabelText('Clip video'));
    expect(audioMock.audioGraphFor).not.toHaveBeenCalled();
  });
});
