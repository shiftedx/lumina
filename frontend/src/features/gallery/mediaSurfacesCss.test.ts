import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const read = (path: string) => readFileSync(path, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

const styles = {
  artwork: read('src/features/titles/editor/artwork/artwork.css'),
  avatar: read('src/features/gallery/channelAvatar.css'),
  channels: read('src/features/channels/channels.css'),
  channelsView: read('src/features/library/channelsView.css'),
  collections: read('src/features/collections/collections.css'),
  downloads: read('src/features/downloads/downloads.css'),
  explore: read('src/features/explore/explore.css'),
  fields: read('src/features/titles/editor/fields.css'),
  gallery: read('src/features/gallery/gallery.css'),
  home: read('src/features/home/home.css'),
  music: read('src/features/gallery/music.css'),
  requests: read('src/features/requests/requests.css'),
  streaming: read('src/features/streaming/streaming.css'),
  titlePage: read('src/features/gallery/titlePage.css'),
  titles: read('src/features/titles/titles.css'),
  ui: read('src/ui/ui.css'),
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

function expectSheetRadius(css: string, selector: string) {
  const rules = declarations(css, selector);
  expect(rules.length, selector).toBeGreaterThan(0);
  expect(rules.join(' '), selector).toContain('border-radius: var(--g-radius-sheet)');
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

  it('rounds content cards and art with the navigation surface radius', () => {
    const cards: Array<[keyof typeof styles, string]> = [
      ['gallery', '.gallery .g-art'],
      ['gallery', '.gallery .g-poster'],
      ['gallery', '.gallery .g-still'],
      ['gallery', '.gallery .g-slot'],
      ['gallery', '.gallery .g-feature'],
      ['gallery', '.gallery .g-scene'],
      ['gallery', '.gallery .g-still-slot'],
      ['gallery', '.gallery .g-frame'],
      ['gallery', '.gallery .g-chapter-frame'],
      ['gallery', '.gallery .g-remote-card .g-still-frame'],
      ['gallery', '.gallery .g-rail-slots .g-still-slot'],
      ['titlePage', '.t-episode'],
      ['titlePage', '.t-still'],
      ['titlePage', '.t-person'],
      ['music', '.gallery.g-audio-stage .g-audio-art'],
      ['channelsView', '.gallery .g-channels-tiles .g-still-slot'],
      ['channelsView', '.gallery .g-channel-card'],
      ['channelsView', '.gallery .g-channel-card-frame'],
      ['explore', '.gallery .g-explore-wall .g-still-slot'],
      ['explore', '.gallery .g-explore-channel'],
      ['channels', '.gallery .g-channels-wall .g-still-slot'],
      ['channels', '.gallery .g-channel-wall .g-still-slot'],
      ['channels', '.gallery .g-channel-tile'],
      ['channels', '.subscription-control'],
      ['channels', '.subscription-control-failure'],
      ['channels', '.subscription-control-confirmation'],
      ['requests', '.rq-frame'],
      ['requests', '.rq-card-open'],
      ['requests', '.rq-language-option'],
      ['requests', '.rq-schedule-day'],
      ['collections', '.collection-row'],
      ['collections', '.collection-entry-thumb'],
      ['collections', '.g-collection-tile'],
      ['collections', '.g-mosaic'],
      ['collections', '.inline-workspace-error'],
      ['collections', '.workspace-empty'],
      ['downloads', '.g-job-thumb'],
      ['downloads', '.g-playlist-thumb'],
      ['downloads', '.download-menu'],
    ];
    for (const [file, selector] of cards) expectSheetRadius(styles[file], selector);
  });

  it('keeps full-bleed media flush while rounding contained art', () => {
    for (const [file, selector] of [
      ['gallery', '.gallery .g-live-hero-art .g-art'],
      ['titlePage', '.gallery.t-page .t-hero-art'],
      ['channels', '.gallery .g-channel-banner .g-art'],
      ['requests', '.rq-trailer .player-frame'],
    ] as Array<[keyof typeof styles, string]>) {
      expect(declarations(styles[file], selector).join(' '), selector).toContain('border-radius: 0');
    }
  });

  it('draws one selected underline in the Streaming switches', () => {
    const local = declarations(styles.streaming, '.gallery .g-streaming-bar .g-segment:has(input:checked)').join(' ');
    expect(local).toContain('text-decoration: none');
    expect(local).not.toContain('box-shadow');
    expect(declarations(styles.ui, '.g-segment:has(input:checked)').join(' ')).toContain('box-shadow: inset 0 -2px');
  });
});
