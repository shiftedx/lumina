// @vitest-environment jsdom

import { expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ stopCast: vi.fn().mockResolvedValue(undefined) }));

vi.mock('./features/music/musicCastSession', () => ({
  forgetMusicCast: vi.fn(),
  stopMusicCast: mocks.stopCast,
}));

import { stopMusicOutputBeforeVideo, stopOwnerMusicOutputBeforeVideo } from './LuminaApp';

it('stops a cold owner cast before video even when no cast is restored in memory', async () => {
  const error = vi.fn();

  await expect(stopOwnerMusicOutputBeforeVideo('member', () => true, error)).resolves.toBe(true);

  expect(mocks.stopCast).toHaveBeenCalledWith('member');
  expect(error).not.toHaveBeenCalled();
});

it('does not open video after the music stop belongs to a stale member request', async () => {
  let finish: (() => void) | undefined;
  const stop = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
  const error = vi.fn();
  let current = true;

  const pending = stopMusicOutputBeforeVideo(stop, () => current, error);
  current = false;
  finish?.();

  await expect(pending).resolves.toBe(false);
  expect(error).not.toHaveBeenCalled();
});

it('reports a current speaker-stop failure and prevents the video transition', async () => {
  const failure = new Error('Speaker stop failed');
  const error = vi.fn();
  await expect(stopMusicOutputBeforeVideo(() => Promise.reject(failure), () => true, error)).resolves.toBe(false);
  expect(error).toHaveBeenCalledWith(failure);
});
