import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const read = (path: string) => readFileSync(path, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

const styles = {
  artwork: read('src/features/titles/editor/artwork/artwork.css'),
  avatar: read('src/features/gallery/channelAvatar.css'),
  channels: read('src/features/channels/channels.css'),
  channelsView: read('src/features/library/channelsView.css'),
  explore: read('src/features/explore/explore.css'),
  fields: read('src/features/titles/editor/fields.css'),
  gallery: read('src/features/gallery/gallery.css'),
  home: read('src/features/home/home.css'),
  titlePage: read('src/features/gallery/titlePage.css'),
  titles: read('src/features/titles/titles.css'),
};

function declarations(css: string, selector: string): string[] {
  return [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter(([, selectors]) => selectors.split(',').map((part) => part.trim()).includes(selector))
    .map(([, , body]) => body);
}

const fullOutline = /(?:^|;)\s*(?:border\s*:(?!\s*(?:0|none)\b)(?![^;]*\btransparent\b)|border-color\s*:(?!\s*transparent\b)|border-style\s*:(?!\s*none\b)|border-width\s*:(?!\s*0\b)|outline\s*:(?!\s*(?:0|none)\b)|box-shadow\s*:(?!\s*none\b))/;

function expectUnboxed(css: string, selector: string) {
  const rules = declarations(css, selector);
  expect(rules.length, selector).toBeGreaterThan(0);
  expect(rules, selector).not.toEqual(expect.arrayContaining([expect.stringMatching(fullOutline)]));
}

describe('media surface styling', () => {
  it('uses filled loading surfaces instead of outlined image frames', () => {
    const frames: Array<[string, string]> = [
      ['gallery', '.gallery .g-slot'],
      ['gallery', '.gallery .g-still-slot'],
      ['gallery', '.gallery .g-frame'],
      ['gallery', '.gallery .g-chapter-frame'],
      ['gallery', '.gallery .g-rail-slots .g-still-slot'],
      ['home', '.gallery.home .h-frame'],
      ['explore', '.gallery .g-explore-wall .g-still-slot'],
      ['channels', '.gallery .g-channels-wall .g-still-slot'],
      ['channels', '.gallery .g-channel-wall .g-still-slot'],
      ['channelsView', '.gallery .g-channels-tiles .g-still-slot'],
    ];
    for (const [file, selector] of frames) {
      const css = styles[file as keyof typeof styles];
      expectUnboxed(css, selector);
      expect(declarations(css, selector).join(' '), selector).toContain('background: var(--g-paper-2)');
    }
  });

  it('keeps cards, panels, tags and resting controls free of complete outlines', () => {
    const surfaces: Array<[string, string]> = [
      ['home', '.gallery.home .h-remove'],
      ['avatar', '.g-avatar'],
      ['channels', '.subscription-control'],
      ['channels', '.subscription-control-failure'],
      ['channels', '.subscription-control-automatic'],
      ['channels', '.subscription-control-confirmation'],
      ['titles', '.identify-results li'],
      ['artwork', '.ed-art-card'],
      ['fields', '.ed-src'],
      ['fields', '.ed-chip'],
      ['titlePage', '.t-versions label'],
    ];
    for (const [file, selector] of surfaces) expectUnboxed(styles[file as keyof typeof styles], selector);
    expect(declarations(styles.gallery, '.gallery .g-card::after')).toEqual([]);
    expect(declarations(styles.gallery, '.gallery .g-remote-card.is-playlist .g-still-frame')).toEqual([]);
    expect(declarations(styles.channelsView, '.gallery .g-channel-card-avatar').join(' ')).not.toContain('box-shadow');
  });

  it('uses state cues without restoring resting button outlines', () => {
    expectUnboxed(styles.home, '.gallery.home .h-hero .g-button');
    expectUnboxed(styles.home, '.gallery.home .h-hero-step');
    expectUnboxed(styles.gallery, '.gallery .g-live-hero-actions .g-button');
    expectUnboxed(styles.gallery, '.gallery .g-live-hero-actions .g-live-watch');
    expectUnboxed(styles.channels, '.subscription-control .subscription-control-danger');
    expect(declarations(styles.titlePage, '.t-versions label:has(input:checked)').join(' ')).toContain('text-decoration: underline');
  });

  it('does not draw a box around the programmatically focused title heading', () => {
    expect(declarations(styles.titlePage, '.t-page h1:focus-visible')).toEqual([]);
    expect(declarations(styles.titlePage, '.t-page h1:focus').join(' ')).toContain('outline: none');
  });
});
