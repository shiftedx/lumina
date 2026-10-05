import { describe, expect, it } from 'vitest';
import { captionPrefsFromUiPrefs, captionStyle, DEFAULT_CAPTIONS } from './captionPrefs';

describe('caption preferences', () => {
  it('read ui_prefs.captions and fall back per field', () => {
    expect(captionPrefsFromUiPrefs(undefined)).toEqual(DEFAULT_CAPTIONS);
    expect(captionPrefsFromUiPrefs({ captions: { size: 'large', background: 'bogus' } })).toEqual({ size: 'large', background: 'shadow' });
    expect(captionPrefsFromUiPrefs({ captions: ['large'] })).toEqual(DEFAULT_CAPTIONS);
  });
  it('map to the ::cue custom properties', () => {
    expect(captionStyle({ size: 'xlarge', background: 'box' })).toEqual({ '--caption-scale': '1.6', '--caption-bg': 'var(--g-caption-box)', '--caption-shadow': 'none' });
    expect(captionStyle({ size: 'small', background: 'shadow' })).toEqual({ '--caption-scale': '0.8', '--caption-bg': 'transparent', '--caption-shadow': 'var(--g-caption-shadow)' });
    expect(captionStyle(DEFAULT_CAPTIONS)['--caption-scale']).toBe('1');
    expect(captionStyle({ size: 'medium', background: 'solid' })['--caption-bg']).toBe('var(--g-caption-solid)');
  });
});
