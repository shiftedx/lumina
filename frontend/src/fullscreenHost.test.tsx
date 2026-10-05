import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('hls.js', () => import('./test/fakeHls'));
const api = vi.hoisted(() => ({ getLocalPlaybackOptions: vi.fn(), startLocalPlaybackSession: vi.fn(), stopLocalPlaybackSession: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const { hlsCalls, resetHlsCalls } = await import('./test/fakeHls');
const { WatchFullscreenHost } = await import('./fullscreenHost');
const { LuminaPlayer } = await import('./LuminaPlayer');
const { LocalLibraryPlayer } = await import('./localPlayer');
const { libraryMediaUrl } = await import('./api');

/** jsdom has no Fullscreen API: a fake that behaves like the browser's (document state + change event). */
function fakeFullscreen() {
  let current: Element | null = null;
  const set = (element: Element | null) => { current = element; document.dispatchEvent(new Event('fullscreenchange')); };
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => current });
  const exit = vi.fn(async () => set(null));
  Object.defineProperty(document, 'exitFullscreen', { configurable: true, value: exit });
  Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', { configurable: true, value(this: HTMLElement) { set(this); return Promise.resolve(); } });
  return { exit, current: () => current };
}

afterEach(() => {
  delete (document as { fullscreenElement?: unknown }).fullscreenElement;
  delete (document as { exitFullscreen?: unknown }).exitFullscreen;
  delete (HTMLElement.prototype as { requestFullscreen?: unknown }).requestFullscreen;
});

const episode = (id: string, active = true) => (
  <WatchFullscreenHost active={active}>
    <LuminaPlayer key={id} source={{ id, kind: 'video', src: `/api/library/${id}/media` }} title={id} />
  </WatchFullscreenHost>
);

describe('WatchFullscreenHost', () => {
  it('keeps fullscreen across the next episode replacing the player, and hands the screen back when Watch stops', async () => {
    const fullscreen = fakeFullscreen();
    const { container, rerender } = render(episode('ep-1'));
    const host = container.querySelector('.watch-host') as HTMLElement;
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Enter fullscreen' })); });
    expect(fullscreen.current()).toBe(host);
    expect(host.dataset.fullscreen).toBe('true');

    const firstVideo = container.querySelector('video');
    rerender(episode('ep-2'));
    expect(container.querySelector('video')).not.toBe(firstVideo); // a new player for the next episode
    expect(fullscreen.current()).toBe(host);
    expect(host.isConnected).toBe(true);
    expect(fullscreen.exit).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Exit fullscreen' })).toBeTruthy();

    await act(async () => { rerender(episode('ep-2', false)); });
    expect(fullscreen.exit).toHaveBeenCalledTimes(1);
    expect(fullscreen.current()).toBeNull();
  });
});

/** jsdom has no Picture-in-Picture API: the document's element, request on a video, and exit. */
function fakePictureInPicture() {
  let current: Element | null = null;
  Object.defineProperty(document, 'pictureInPictureEnabled', { configurable: true, value: true });
  Object.defineProperty(document, 'pictureInPictureElement', { configurable: true, get: () => current });
  Object.defineProperty(document, 'exitPictureInPicture', { configurable: true, value: vi.fn(async () => { current = null; }) });
  Object.defineProperty(HTMLVideoElement.prototype, 'requestPictureInPicture', { configurable: true, value(this: HTMLVideoElement) { current = this; return Promise.resolve({}); } });
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
  return { current: () => current };
}

describe('picture-in-picture across the next episode', () => {
  afterEach(() => {
    cleanup(); // unmount (stopping sessions) before the mocks reset
    for (const name of ['pictureInPictureEnabled', 'pictureInPictureElement', 'exitPictureInPicture']) delete (document as unknown as Record<string, unknown>)[name];
    delete (HTMLVideoElement.prototype as { requestPictureInPicture?: unknown }).requestPictureInPicture;
    vi.restoreAllMocks();
    vi.resetAllMocks();
    resetHlsCalls();
  });

  const watch = (id: string, active = true) => (
    <WatchFullscreenHost active={active}>
      {active ? <LocalLibraryPlayer itemId={id} key={id} kind="video" poster={null} title={id} /> : null}
    </WatchFullscreenHost>
  );
  const enterPictureInPicture = async () => { await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Picture in picture' })); }); };

  it('hands the same element to the next episode: the old hls.js and server session stop, the new session attaches', async () => {
    const pip = fakePictureInPicture();
    api.getLocalPlaybackOptions.mockResolvedValue({ mode: 'transcode', facts: {}, audio_tracks: [], quality_heights: [] });
    api.startLocalPlaybackSession.mockImplementation(async (itemId: string) => ({ session_id: `s-${itemId}`, mode: 'transcode', playback_url: `/api/playback-sessions/s-${itemId}/index.m3u8`, start: 0 }));
    api.stopLocalPlaybackSession.mockResolvedValue(undefined);
    const { container, rerender } = render(watch('ep-1'));
    await waitFor(() => expect(hlsCalls.attached).toHaveLength(1));
    const video = container.querySelector('video') as HTMLVideoElement;
    expect(hlsCalls.attached[0]).toBe(video);
    await enterPictureInPicture();
    expect(pip.current()).toBe(video);

    rerender(watch('ep-2'));
    expect(hlsCalls.destroyed).toBe(1);
    await waitFor(() => expect(api.stopLocalPlaybackSession).toHaveBeenCalledWith('s-ep-1'));
    await waitFor(() => expect(hlsCalls.attached).toHaveLength(2));
    expect(hlsCalls.attached[1]).toBe(video);
    expect(hlsCalls.sources[1]).toContain('s-ep-2');
    expect(container.querySelector('video')).toBe(video);
    expect(video.isConnected).toBe(true);
    expect(pip.current()).toBe(video);
  });

  it('swaps a direct file into the same element, and Watch closing drops it', async () => {
    const pip = fakePictureInPicture();
    api.getLocalPlaybackOptions.mockResolvedValue({ mode: 'direct', facts: {}, audio_tracks: [], quality_heights: [] });
    const { container, rerender } = render(watch('ep-1'));
    await waitFor(() => expect(container.querySelector('video')?.getAttribute('src')).toBe(libraryMediaUrl('ep-1')));
    const video = container.querySelector('video') as HTMLVideoElement;
    await enterPictureInPicture();

    rerender(watch('ep-2'));
    expect(container.querySelector('video')).toBe(video);
    await waitFor(() => expect(video.getAttribute('src')).toBe(libraryMediaUrl('ep-2')));
    expect(pip.current()).toBe(video);

    await act(async () => { rerender(watch('ep-2', false)); });
    expect(video.isConnected).toBe(false);
  });

  it('gives the next episode a new element when nothing is in picture-in-picture', async () => {
    fakePictureInPicture();
    api.getLocalPlaybackOptions.mockResolvedValue({ mode: 'direct', facts: {}, audio_tracks: [], quality_heights: [] });
    const { container, rerender } = render(watch('ep-1'));
    const video = container.querySelector('video');
    rerender(watch('ep-2'));
    expect(container.querySelector('video')).not.toBe(video);
    expect(video?.isConnected).toBe(false);
  });
});
