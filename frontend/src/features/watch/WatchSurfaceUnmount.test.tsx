import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { watchSurface } from '../../test/watchSurface';

afterEach(() => vi.restoreAllMocks());

describe('remote checkpoints after the surface closes', () => {
  it('a checkpoint queued behind a slow one is never sent once the surface has unmounted', async () => {
    const puts: Array<{ keepalive: boolean }> = [];
    let answerFirst: (r: Response) => void = () => undefined;
    const progressUrl = '/api/playback/remote/youtube%3Avideo-m1';
    vi.spyOn(globalThis, 'fetch').mockImplementation((url, init) => {
      if (url === progressUrl && init?.method === 'PUT') {
        puts.push({ keepalive: Boolean(init.keepalive) });
        if (puts.length === 1) return new Promise<Response>((resolve) => { answerFirst = resolve; });
        return Promise.resolve(new Response('{}', { status: 200 }));
      }
      return Promise.resolve(new Response(null, { status: 204 }));
    });
    const view = render(watchSurface({
      selection: {
        kind: 'remote',
        item: { id: 'video-m1', source: 'youtube', title: 'M1', uploader: 'Channel', webpage_url: 'https://www.youtube.com/watch?v=video-m1' },
        preview: { kind: 'video', entries: [], playback: { status: 'ready', stream_id: 'remote-m1', transport: 'progressive', media_kind: 'video', playback_url: '/api/remote-streams/remote-m1/content', content_type: 'video/mp4', has_video: true, has_audio: true, seekable: true }, raw: { extractor: 'youtube', id: 'video-m1', duration: 300 } },
      },
    }));
    const media = await screen.findByTitle('Progressive remote stream') as HTMLVideoElement;
    Object.defineProperties(media, { currentTime: { configurable: true, value: 30, writable: true }, duration: { configurable: true, value: 300 }, ended: { configurable: true, value: false } });
    fireEvent.pause(media); // checkpoint N goes out and stays pending
    media.currentTime = 60;
    fireEvent.seeked(media); // N+1 queues behind it
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(puts).toEqual([{ keepalive: false }]);
    view.unmount(); // the unmount keepalive carries the final position
    expect(puts).toEqual([{ keepalive: false }, { keepalive: true }]);
    answerFirst(new Response('{}', { status: 200 }));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(puts).toHaveLength(2);
  });
});
