import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { LuminaPlayer, PlayerPreferencesContext, type PlayerPreferences } from './LuminaPlayer';

describe('LuminaPlayer', () => {
  it('renders a saved video with an accessible play control and its native media ref', () => {
    const mediaRef = createRef<HTMLMediaElement>();
    const onTimeUpdate = vi.fn();

    render(
      <LuminaPlayer
        mediaRef={mediaRef}
        onTimeUpdate={onTimeUpdate}
        source={{ kind: 'video', poster: '/posters/forest.jpg', src: '/api/library/forest/media' }}
        title="Forest walk"
      />,
    );

    expect(screen.getByLabelText('Forest walk video').getAttribute('src')).toBe('/api/library/forest/media');
    expect(screen.getByRole('button', { name: 'Play' })).toBeTruthy();
    expect(mediaRef.current).toBeInstanceOf(HTMLVideoElement);
    fireEvent.timeUpdate(mediaRef.current!);
    expect(onTimeUpdate).toHaveBeenCalledWith(mediaRef.current);
  });

  it('shows a Live state and omits VOD seeking while keeping pause, mute, and fullscreen', () => {
    render(
      <LuminaPlayer
        source={{ kind: 'video', src: null, state: 'loading', live: true, seekable: false, nativeTitle: 'HLS remote stream' }}
        title="Rocket launch"
      />,
    );
    // A clear Live state is presented.
    expect(screen.getByRole('status', { name: 'Watching live' })).toBeTruthy();
    // VOD seeking and duration are gone (the live control set also hides Playback speed on purpose).
    expect(screen.queryByLabelText('Seek')).toBeNull();
    expect(screen.queryByLabelText('Playback time')).toBeNull();
    // The meaningful live controls remain.
    expect(screen.getByRole('button', { name: 'Play' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Mute' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Enter fullscreen' })).toBeTruthy();
  });

  it('a live player that is ready can flip to Go live and its dot is the still .g-live-dot', () => {
    render(<LuminaPlayer source={{ kind: 'video', src: '/api/live/stream', live: true, seekable: false }} title="Rocket launch" />);
    fireEvent.canPlay(screen.getByLabelText('Rocket launch video'));
    const goLive = screen.getByRole('button', { name: 'Go live' });
    expect(goLive.querySelector('.g-live-dot')).not.toBeNull();
    expect(goLive.querySelector('.is-live')).toBeNull();
  });

  it('provides native play, pause, seek, volume, and mute controls for audio', async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
    render(<LuminaPlayer source={{ kind: 'audio', src: '/api/library/episode/media' }} title="Night radio" />);
    const media = screen.getByLabelText('Night radio audio') as HTMLAudioElement;
    Object.defineProperties(media, {
      currentTime: { configurable: true, value: 12, writable: true },
      duration: { configurable: true, value: 90, writable: true },
    });
    fireEvent.canPlay(media);
    fireEvent.loadedMetadata(media);

    fireEvent.click(screen.getByRole('button', { name: 'Play' }));
    expect(play).toHaveBeenCalled();
    fireEvent.play(media);
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    expect(pause).toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Seek'), { target: { value: '45' } });
    fireEvent.change(screen.getByLabelText('Volume'), { target: { value: '0.4' } });
    expect(media.currentTime).toBe(45);
    expect(media.volume).toBe(0.4);
    fireEvent.click(screen.getByRole('button', { name: 'Mute' }));
    expect(media.muted).toBe(true);
  });

  it('announces loading, buffering, unsupported, and failed playback without hiding a refresh path', async () => {
    const refresh = vi.fn().mockResolvedValue({ kind: 'video' as const, src: '/api/remote-streams/opaque/content-v2' });
    const { rerender } = render(<LuminaPlayer source={{ kind: 'video', onRefresh: refresh, src: '/api/remote-streams/opaque/content' }} title="Remote film" />);
    const media = screen.getByLabelText('Remote film video');
    expect(screen.getByRole('status').textContent).toContain('Loading media');
    fireEvent.canPlay(media);
    expect(screen.queryByText('Loading media…')).toBeNull();
    fireEvent.waiting(media);
    expect(screen.getByRole('status').textContent).toContain('Loading media');
    fireEvent.error(media);
    expect(screen.getByRole('alert').textContent).toContain('Playback could not continue');
    fireEvent.click(screen.getByRole('button', { name: 'Refresh stream' }));
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.getByLabelText('Remote film video').getAttribute('src')).toBe('/api/remote-streams/opaque/content-v2'));

    rerender(<LuminaPlayer source={{ kind: 'video', message: 'This stream has no compatible audio.', src: null, state: 'unsupported' }} title="Silent film" />);
    expect(screen.getByRole('status').textContent).toContain('This stream has no compatible audio.');
    expect(screen.queryByLabelText('Silent film video')).toBeNull();
  });

  it('keeps externally attached media visible after it becomes ready without a native src', () => {
    render(<LuminaPlayer source={{ kind: 'video', src: null, state: 'loading' }} title="Managed DASH film" />);
    const media = screen.getByLabelText('Managed DASH film video');

    fireEvent.canPlay(media);

    expect(screen.getByLabelText('Managed DASH film video')).toBe(media);
    expect(screen.queryByText('Stream unavailable')).toBeNull();
    expect(screen.getByLabelText('Managed DASH film player').getAttribute('data-player-state')).toBe('ready');
  });

  it('keeps a terminal failure visible when refresh returns no replacement source', async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    render(<LuminaPlayer source={{ kind: 'video', onRefresh: refresh, src: null, state: 'failed' }} title="Exhausted remote film" />);

    fireEvent.click(screen.getByRole('button', { name: 'Refresh stream' }));

    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    expect(screen.getByRole('alert').textContent).toContain('Playback could not continue');
    expect(screen.queryByLabelText('Exhausted remote film video')).toBeNull();
  });

  it('places quality, chapters, and theater extensions inside the unified player controls', () => {
    const { container } = render(
      <LuminaPlayer
        extensions={{
          chapters: <button type="button">Chapter 1</button>,
          quality: <button type="button">1080p</button>,
          theater: <button type="button">Theater mode</button>,
        }}
        source={{ kind: 'video', src: '/api/library/film/media' }}
        title="Film"
      />,
    );

    expect(container.querySelector('[data-player-extension="quality"]')?.textContent).toContain('1080p');
    expect(container.querySelector('[data-player-extension="chapters"]')?.textContent).toContain('Chapter 1');
    expect(container.querySelector('[data-player-extension="theater"]')?.textContent).toContain('Theater mode');
    expect(container.querySelector('.player-progress-stack > [data-player-extension="chapters"]')).toBeTruthy();
    expect(container.querySelector('.player-control-cluster--end > [data-player-extension="quality"]')).toBeTruthy();
    expect(screen.getByLabelText('Seek').getAttribute('aria-valuetext')).toBe('0:00 of 0:00');
    expect(container.querySelectorAll('[data-player-extension] section')).toHaveLength(0);
    expect(container.firstElementChild?.getAttribute('data-motion')).toBe('none');
  });

  it('releases each caller-owned source when switching or unmounting', () => {
    const releaseFirst = vi.fn();
    const releaseSecond = vi.fn();
    const { rerender, unmount } = render(<LuminaPlayer source={{ id: 'first', kind: 'video', onRelease: releaseFirst, src: '/api/remote-streams/first/content' }} title="First" />);

    rerender(<LuminaPlayer source={{ id: 'second', kind: 'video', onRelease: releaseSecond, src: '/api/remote-streams/second/content' }} title="Second" />);
    expect(releaseFirst).toHaveBeenCalledOnce();
    unmount();
    expect(releaseSecond).toHaveBeenCalledOnce();
  });

  it('reapplies retained volume and mute state when a source remounts the native element', () => {
    const { rerender } = render(<LuminaPlayer source={{ id: 'first', kind: 'video', src: '/api/library/first/media' }} title="First" />);
    fireEvent.change(screen.getByLabelText('Volume'), { target: { value: '0.4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Mute' }));

    rerender(<LuminaPlayer source={{ id: 'second', kind: 'video', src: '/api/library/second/media' }} title="Second" />);
    const replacement = screen.getByLabelText('Second video') as HTMLVideoElement;
    expect(replacement.volume).toBe(0.4);
    expect(replacement.muted).toBe(true);
  });

  it('hides video chrome after playback settles and restores it on pointer activity', () => {
    vi.useFakeTimers();
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
    try {
      render(<LuminaPlayer source={{ kind: 'video', src: '/api/library/film/media' }} title="Film" />);
      const media = screen.getByLabelText('Film video') as HTMLVideoElement;
      const player = screen.getByLabelText('Film player');
      fireEvent.canPlay(media);
      fireEvent.play(media);
      act(() => vi.advanceTimersByTime(2200));
      expect(player.getAttribute('data-controls-visible')).toBe('false');
      fireEvent.pointerEnter(player, { pointerType: 'mouse' });
      expect(player.getAttribute('data-controls-visible')).toBe('true');
      act(() => vi.advanceTimersByTime(2200));
      expect(player.getAttribute('data-controls-visible')).toBe('false');
      const pauseCallsBeforeDisclosure = pause.mock.calls.length;
      fireEvent.pointerEnter(media, { pointerType: 'touch' });
      expect(player.getAttribute('data-controls-visible')).toBe('false');
      fireEvent.pointerDown(media, { pointerType: 'touch' });
      fireEvent.click(media);
      expect(player.getAttribute('data-controls-visible')).toBe('true');
      expect(pause).toHaveBeenCalledTimes(pauseCallsBeforeDisclosure);
      fireEvent.pointerMove(player);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('the live line', () => {
  const renderSource = (source: Parameters<typeof LuminaPlayer>[0]['source']) => render(<LuminaPlayer source={source} title="Clip" />);
  const renderLocalSource = () => renderSource({ kind: 'video', src: '/api/library/l/media', state: 'ready' });
  const renderRemoteVodSource = () => renderSource({ kind: 'video', src: '/api/remote/r', state: 'ready', seekable: true });
  const renderLiveSource = () => renderSource({ kind: 'video', src: '/api/remote/r', state: 'ready', live: true, seekable: false });

  it('shows the one LIVE badge on the bar and no retired live dot', () => {
    const { container } = renderSource({ kind: 'video', src: null, state: 'loading', live: true, seekable: false });
    const status = screen.getByRole('status', { name: 'Watching live' });
    expect(status.querySelector('.g-live.is-live.on-bar')?.textContent).toBe('LIVE');
    expect(container.querySelector('.player-live-badge, .player-live-dot')).toBeNull();
  });

  it('renders the same control set for a local video, a remote video on demand and a remote live source, apart from the seek bar and the live line', () => {
    const controls = (element: HTMLElement) => [...element.querySelectorAll('.player-controls button, .player-controls select')].map((control) => control.getAttribute('aria-label') || control.textContent?.trim()).filter((name) => name && !/^(Go live|Seek)$/.test(name));
    const local = controls(renderLocalSource().container);
    document.body.innerHTML = '';
    const vod = controls(renderRemoteVodSource().container);
    document.body.innerHTML = '';
    const live = controls(renderLiveSource().container);
    expect(vod).toEqual(local);
    // Playback speed is deliberately absent on a live edge (existing behaviour); everything else matches.
    expect(live).toEqual(local.filter((name) => name !== 'Playback speed'));
  });

  describe('top scrim title', () => {
    const renderWith = (source: Parameters<typeof LuminaPlayer>[0]['source'], prefs: PlayerPreferences) => render(
      <PlayerPreferencesContext.Provider value={prefs}><LuminaPlayer source={source} title="The Long Walk" /></PlayerPreferencesContext.Provider>,
    );

    it('shows the serif title and sub-line over the video only in fullscreen or theater', () => {
      const source = { kind: 'video', src: '/api/library/a/stream', state: 'ready' } as const;
      const { unmount } = renderWith(source, { subtitle: 'S2 · E4' });
      expect(document.querySelector('.player-top-scrim')).toBeNull();
      unmount();
      renderWith(source, { subtitle: 'S2 · E4', theater: true });
      const scrim = document.querySelector('.player-top-scrim')!;
      expect(scrim.textContent).toContain('The Long Walk');
      expect(scrim.querySelector('.g-label')?.textContent).toBe('S2 · E4');
      expect(scrim.getAttribute('aria-hidden')).toBe('true');
    });

    it('adds no title scrim over a live player in the page layout and keeps the one LiveBadge', () => {
      renderWith({ kind: 'video', src: null, state: 'loading', live: true, seekable: false, nativeTitle: 'HLS remote stream' }, {});
      expect(document.querySelector('.player-top-scrim')).toBeNull();
      expect(screen.getAllByRole('status', { name: 'Watching live' })).toHaveLength(1);
      expect(document.querySelector('.player-timecode')).toBeNull();
    });
  });
});

describe('captions', () => {
  const READY_VIDEO = { kind: 'video', src: '/api/library/a/stream', state: 'ready', nativeTitle: 'The Long Walk' } as const;
  const renderSource = (source: Parameters<typeof LuminaPlayer>[0]['source'], prefs: PlayerPreferences = {}) => render(<PlayerPreferencesContext.Provider value={prefs}><LuminaPlayer source={source} title="Clip" /></PlayerPreferencesContext.Provider>);

  it('maps prefs to the player style', () => {
    renderSource(READY_VIDEO, { captions: { size: 'large', background: 'box' } });
    const player = document.querySelector<HTMLElement>('.lumina-player')!;
    expect(player.style.getPropertyValue('--caption-scale')).toBe('1.3');
    expect(player.style.getPropertyValue('--caption-bg')).toBe('var(--g-caption-box)');
  });

  it("writes --caption-base from the player's height and follows resizes", () => {
    let fire: (height: number) => void = () => undefined;
    vi.stubGlobal('ResizeObserver', class { constructor(callback: ResizeObserverCallback) { fire = (height) => callback([{ contentRect: { height } } as ResizeObserverEntry], this as unknown as ResizeObserver); } observe() {} disconnect() {} });
    renderSource(READY_VIDEO);
    const player = document.querySelector<HTMLElement>('.lumina-player')!;
    act(() => fire(400));
    expect(player.style.getPropertyValue('--caption-base')).toBe('18px');
    act(() => fire(1000));
    expect(player.style.getPropertyValue('--caption-base')).toBe('45px');
    vi.unstubAllGlobals();
  });

  it('falls back to fixed sizes where cue styling cannot use variables', () => {
    vi.stubGlobal('CSS', { supports: () => false });
    renderSource(READY_VIDEO, { captions: { size: 'xlarge', background: 'solid' } });
    const player = document.querySelector<HTMLElement>('.lumina-player')!;
    expect(player.dataset.captionFallback).toBe('xlarge solid');
    vi.unstubAllGlobals();
  });

  it('swaps in a fresh element when the audio clock stalls, tells the owner, and clears the notice on a new source', async () => {
    vi.useFakeTimers();
    const node = { gain: { setValueAtTime: vi.fn(), cancelScheduledValues: vi.fn() }, connect: vi.fn((next: unknown) => next) };
    class StalledContext {
      currentTime = 0;
      state = 'running';
      destination = {};
      createGain = () => node;
      createMediaElementSource = () => ({ connect: () => node });
      close = vi.fn(async () => undefined);
    }
    vi.stubGlobal('AudioContext', StalledContext);
    const media: Array<HTMLMediaElement | null> = [];
    const onMediaReplaced = vi.fn();
    const props = { audio: { gain: 1, muteRanges: [] }, mediaRef: (node: HTMLMediaElement | null) => { media.push(node); }, onMediaReplaced, title: 'Forest walk' };
    const { rerender } = render(<LuminaPlayer {...props} source={{ kind: 'video', src: '/a.mp4' }} />);
    const first = media.at(-1)!;
    Object.defineProperty(first, 'paused', { value: false, configurable: true });
    await act(async () => { first.dispatchEvent(new Event('play')); });
    await act(async () => { await vi.advanceTimersByTimeAsync(600); });
    expect(onMediaReplaced).toHaveBeenCalledTimes(1);
    expect(media.at(-1)).not.toBe(first);
    expect(screen.getByText(/Loudness levelling is off/)).toBeTruthy();
    rerender(<LuminaPlayer {...props} source={{ kind: 'video', src: '/b.mp4' }} />);
    expect(screen.queryByText(/Loudness levelling is off/)).toBeNull();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });
});
