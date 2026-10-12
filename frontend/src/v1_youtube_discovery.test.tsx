import { describe, expect, it } from 'vitest';
import { normalizeChannelAddress } from './channelSubscriptions';

describe('YouTube channel identity', () => {
  it('collapses handle case, channel tabs and host variants onto one identity', () => {
    expect(normalizeChannelAddress('https://m.youtube.com/@Veritasium/videos?view=0')).toBe('https://www.youtube.com/@veritasium');
    expect(normalizeChannelAddress('youtube.com/channel/UCHnyfMqiRRG1u-2MsSQLbXA/streams')).toBe(
      'https://www.youtube.com/channel/UCHnyfMqiRRG1u-2MsSQLbXA',
    );
    expect(normalizeChannelAddress('https://www.youtube.com/watch?v=abc')).toBe('https://www.youtube.com/watch?v=abc');
  });
});
