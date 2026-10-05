import { describe, expect, it } from 'vitest';
import { isProviderVisible, resolveStreamingProviders, withStreamingProviders } from './providers';

const DEFAULTS = ['youtube', 'twitch'];

describe('resolveStreamingProviders', () => {
  it.each([undefined, null, {}, 'x', 7, [], { streaming_providers: 'kick' }, { streaming_providers: null }])('defaults for %j', (prefs) => {
    expect(resolveStreamingProviders(prefs)).toEqual(DEFAULTS);
  });
  it('honours a saved choice, always starting with youtube', () => {
    expect(resolveStreamingProviders({ streaming_providers: ['kick'] })).toEqual(['youtube', 'kick']);
    expect(resolveStreamingProviders({ streaming_providers: [] })).toEqual(['youtube']);
    expect(resolveStreamingProviders({ streaming_providers: ['kick', 'twitch'] })).toEqual(['youtube', 'twitch', 'kick']);
  });
  it('drops unknown names, duplicates and youtube itself', () => {
    expect(resolveStreamingProviders({ streaming_providers: ['vimeo', 'kick', 'kick', 'youtube', 3] })).toEqual(['youtube', 'kick']);
  });
});

describe('withStreamingProviders', () => {
  it('keeps other keys such as theater_mode', () => {
    expect(withStreamingProviders({ theater_mode: true }, ['kick'])).toEqual({ theater_mode: true, streaming_providers: ['kick'] });
    expect(withStreamingProviders(null, [])).toEqual({ streaming_providers: [] });
  });
});

describe('isProviderVisible', () => {
  it('hides only a turned-off twitch or kick', () => {
    const providers = resolveStreamingProviders({ streaming_providers: [] }) as ('youtube')[];
    expect(isProviderVisible(providers, 'twitch')).toBe(false);
    expect(isProviderVisible(providers, 'kick')).toBe(false);
    expect(isProviderVisible(providers, 'youtube')).toBe(true);
    expect(isProviderVisible(providers, undefined)).toBe(true);
    expect(isProviderVisible(['youtube', 'kick'], 'kick')).toBe(true);
  });
});
