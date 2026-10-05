import { describe, expect, it } from 'vitest';
import { resultFromPreview } from './luminaModel';
import type { PreviewResponse } from './types';

describe('Kick resolution', () => {
  it('labels a resolved Kick link as Kick with its canonical address', () => {
    const preview: PreviewResponse = {
      kind: 'video',
      title: 'Clip',
      extractor_key: 'KickClip',
      webpage_url: 'https://kick.com/mxddy/clips/clip_01GYXVB5Y8PWAPWCWMSBCFB05X',
      capabilities: {
        provider: 'kick', lifecycle: 'vod', can_play: false, play_reason: 'provider_not_supported',
        can_acquire: false, acquire_reason: 'provider_not_supported', chat: { live: 'unavailable', replay: 'unavailable' },
      },
      entries: [],
      raw: { id: 'clip_01GYXVB5Y8PWAPWCWMSBCFB05X' },
    };
    const item = resultFromPreview(preview);
    expect(item.source).toBe('kick');
    expect(item.source_label).toBe('Kick');
    expect(item.webpage_url).toBe('https://kick.com/mxddy/clips/clip_01GYXVB5Y8PWAPWCWMSBCFB05X');
    expect(resultFromPreview({ ...preview, capabilities: null }).source_label).toBe('YouTube');
  });
});
