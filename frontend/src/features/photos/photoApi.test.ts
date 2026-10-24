import { afterEach, expect, it, vi } from 'vitest';

import { uploadPhoto } from './photoApi';

afterEach(() => vi.unstubAllGlobals());

it.each([
  ['preview_storage_full', 'Photo storage is full. Free space or increase the photo storage limit, then retry.'],
  ['preview_storage_busy', 'Another copy of this photo is still being prepared. Retry in a moment.'],
  ['Sign in again.', 'Sign in again.'],
])('explains a rejected photo upload so it can be retried: %s', async (detail, message) => {
  class RejectedUpload {
    upload = {};
    status = 503;
    responseText = JSON.stringify({ detail });
    onload: (() => void) | null = null;
    open() {}
    setRequestHeader() {}
    send() { queueMicrotask(() => this.onload?.()); }
  }
  vi.stubGlobal('XMLHttpRequest', RejectedUpload);
  const file = new File(['photo'], 'photo.jpg', { type: 'image/jpeg' });
  await expect(uploadPhoto(file, vi.fn()).request).rejects.toThrow(message);
});
