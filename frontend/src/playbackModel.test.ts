import { describe, expect, it } from 'vitest';

import { canonicalRemoteSourceUrl, capturedChatIdentity, distinctRemoteSources, remoteSourceIdentity, shouldSavePlaybackCheckpoint } from './playbackModel';

describe('playback checkpoint throttling', () => {
  it('saves useful intervals while allowing lifecycle events to flush immediately', () => {
    expect(shouldSavePlaybackCheckpoint({ lastSavedPosition: 30, position: 38 })).toBe(false);
    expect(shouldSavePlaybackCheckpoint({ lastSavedPosition: 30, position: 45 })).toBe(true);
    expect(shouldSavePlaybackCheckpoint({ lastSavedPosition: 45, position: 48, force: true })).toBe(true);
    expect(shouldSavePlaybackCheckpoint({ lastSavedPosition: 0, position: 0 })).toBe(false);
  });
});

describe('remote playback identity', () => {
  it('uses a stable YouTube id across watch and share addresses', () => {
    expect(remoteSourceIdentity({ id: 'abc123', source: 'youtube', webpage_url: 'https://www.youtube.com/watch?v=abc123&si=share' })).toBe('youtube:abc123');
    expect(remoteSourceIdentity({ webpage_url: 'https://youtu.be/abc123?feature=shared' })).toBe('youtube:abc123');
  });

  it('falls back to a canonical URL without dropping source-defining query values', () => {
    expect(remoteSourceIdentity({ webpage_url: 'https://example.test/watch?utm_source=mail&b=2&a=1#chapter' })).toBe('url:https://example.test/watch?a=1&b=2');
    expect(remoteSourceIdentity({ webpage_url: 'https://example.test/watch?feature=full' })).toBe('url:https://example.test/watch?feature=full');
  });

  it('uses the same explicit canonical URL rules as the backend stream cache', () => {
    expect(canonicalRemoteSourceUrl('HTTPS://Example.TEST:443/watch?utm_medium=email&z=2&a=1#chapter')).toBe('https://example.test/watch?a=1&z=2');
    expect(canonicalRemoteSourceUrl('https://www.youtube.com/watch?si=share&v=abc123&feature=shared')).toBe('https://www.youtube.com/watch?v=abc123');
  });

  it('removes the current video and duplicate URL forms from the autoplay queue', () => {
    const current = remoteSourceIdentity({ webpage_url: 'https://youtu.be/abc123?feature=shared' });
    const queue = distinctRemoteSources([
      { title: 'Current again', webpage_url: 'https://www.youtube.com/watch?v=abc123&si=duplicate' },
      { title: 'Next', webpage_url: 'https://youtube.com/watch?v=def456' },
      { title: 'Next duplicate', webpage_url: 'https://youtu.be/def456' },
    ], current, 8);

    expect(queue.map((item) => item.title)).toEqual(['Next']);
  });
});

describe('captured chat identity for a local Library item (#110)', () => {
  it('keys a Twitch live recording by its per-broadcast stream id, not its channel URL', () => {
    // recording_library_info files the recording under extractor "twitch" with
    // the stream-session id; the channel URL would only ever derive url:<...>,
    // which never matches the captured twitch:<stream_id> asset.
    expect(capturedChatIdentity({ extractor: 'twitch', remote_id: '424242', webpage_url: 'https://www.twitch.tv/somestreamer' }))
      .toBe('twitch:424242');
  });

  it('keys Twitch sub-extractors by provider so identity stays provider-scoped', () => {
    expect(capturedChatIdentity({ extractor: 'twitch:stream', remote_id: '424242', webpage_url: 'https://www.twitch.tv/somestreamer' }))
      .toBe('twitch:424242');
  });

  it('keeps the stable YouTube live→VOD identity for a YouTube recording', () => {
    expect(capturedChatIdentity({ extractor: 'youtube', remote_id: 'abc123', webpage_url: 'https://www.youtube.com/watch?v=abc123' }))
      .toBe('youtube:abc123');
  });

  it('falls back to the canonical URL identity for other items and null without any identity', () => {
    expect(capturedChatIdentity({ extractor: 'generic', remote_id: 'x1', webpage_url: 'https://example.test/watch?utm_source=mail&b=2&a=1' }))
      .toBe('url:https://example.test/watch?a=1&b=2');
    expect(capturedChatIdentity({ extractor: 'twitch', remote_id: null, webpage_url: null })).toBeNull();
    expect(capturedChatIdentity({})).toBeNull();
  });
});
