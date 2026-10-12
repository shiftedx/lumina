import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { LocalLibraryPlayer } from './localPlayer';
import type { LocalPlaybackOptions } from './types';

function respond(body: LocalPlaybackOptions) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })));
}

const facts = { container: 'mov,mp4,m4a,3gp,3g2,mj2', video_codec: 'h264', audio_codec: 'aac', width: 1920, height: 1080, duration: 12 };

describe('local playback options', () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it('plays a direct file from the authenticated media URL and shows its probed facts', async () => {
    respond({ mode: 'direct', reason: null, facts });
    const { container } = render(<LocalLibraryPlayer itemId="item-1" kind="video" poster={null} title="Clip" />);
    expect(await screen.findByText('1080p · H.264 / AAC · Original')).toBeTruthy();
    expect(container.querySelector('video')?.getAttribute('src')).toMatch(/\/api\/library\/item-1\/media$/);
  });

  it('does not hand an unplayable file to the browser as if it were native', async () => {
    respond({ mode: 'unavailable', reason: 'probe_failed', facts: null });
    const { container } = render(<LocalLibraryPlayer itemId="item-2" kind="video" poster={null} title="Clip" />);
    expect(await screen.findByText('This file has no playable audio or video.')).toBeTruthy();
    expect(container.querySelector('video[src]')).toBeNull();
  });
});
