import { afterEach, describe, expect, it, vi } from 'vitest';

import { backgroundReady, holdBackground, MAX_HOLD_MS, releaseBackground } from './backgroundGate';

afterEach(() => { releaseBackground(); vi.useRealTimers(); });

describe('backgroundGate', () => {
  it('lets side requests through at once when nothing holds them', async () => {
    await expect(backgroundReady()).resolves.toBeUndefined();
  });

  it('holds side requests until the first frame releases them', async () => {
    holdBackground();
    const settled = vi.fn();
    void backgroundReady().then(settled);
    await Promise.resolve();
    expect(settled).not.toHaveBeenCalled();
    releaseBackground();
    await vi.waitFor(() => expect(settled).toHaveBeenCalled());
  });

  it('never holds longer than MAX_HOLD_MS, so a player with no frame cannot starve the page', async () => {
    vi.useFakeTimers();
    holdBackground();
    const settled = vi.fn();
    void backgroundReady().then(settled);
    await vi.advanceTimersByTimeAsync(MAX_HOLD_MS - 1);
    expect(settled).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(settled).toHaveBeenCalled();
  });
});
