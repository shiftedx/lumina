/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const raw = readFileSync('src/features/watch/player.css', 'utf8');
// Rules after the end marker are legacy; the guard covers the re-tokened chrome above it.
const css = raw.split('/* ---- end of the base sections')[0];
const flat = css.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\s+/g, ' ');
const tsx = readFileSync('src/LuminaPlayer.tsx', 'utf8');
const ALLOWED = /^--(g-(overlay|gold|on-gold|scrim|caption|control|radius|focus|type|serif|sans|mono|track|hover|ink|paper|rule|hairline|elev|z|space|toast)|player-|caption-|progress)/;

describe('player.css', () => {
  it('player.css holds no red and no teal', () => {
    expect(css).not.toMatch(/#c0264a|rgba\(105, 30, 22|rgba\(255, 132, 107|--teal-|--caramel-|--coral-|--accent|#ff89a2/i);
  });
  it('reads only overlay, gold, scrim, caption and layout tokens, so a danger or live-red token cannot slip in as a var()', () => {
    const names = [...flat.matchAll(/var\((--[a-z0-9-]+)/gi)].map((match) => match[1]);
    expect(names.length).toBeGreaterThan(20);
    expect(names.filter((name) => !ALLOWED.test(name))).toEqual([]);
    expect(names.filter((name) => /danger|live|(^|-)red(-|$)|coral|warm/.test(name))).toEqual([]);
  });
  it('keeps the live time code retired: one LiveBadge, no coloured label', () => {
    expect(raw).not.toMatch(/player-timecode--live/);
    expect(tsx).not.toMatch(/player-timecode--live/);
  });
  it('fills played time and the active chapter with gold on the overlay track', () => {
    expect(flat).toMatch(/\.player-seek-control input \{[^}]*linear-gradient\(to right, var\(--g-gold\) 0 var\(--player-progress\)/);
    expect(flat).toMatch(/--g-overlay-track/);
    expect(flat).toMatch(/\[data-chapter-marker='true'\]\.active \{[^}]*background: var\(--g-gold\)/);
  });
  it('draws overlay focus with the overlay ring and a black halo', () => {
    expect(flat).toMatch(/\.lumina-player:focus-visible, \.lumina-player :focus-visible \{ outline: var\(--g-focus-width\) solid var\(--g-overlay-focus\); outline-offset: 2px; box-shadow: 0 0 0 1px var\(--g-overlay-halo\); \}/);
  });
  it('keeps every player button a 44px hit area', () => {
    expect(flat).toMatch(/\.player-controls button \{[^}]*min-width: var\(--g-control\)[^}]*height: var\(--g-control\)/);
  });
  it('overlays the loading and unavailable status so removing it never reflows the grid under the video and controls', () => {
    expect(flat).toMatch(/\.lumina-player > \[role='status'\], \.lumina-player > \[role='alert'\] \{[^}]*position: absolute; inset: 0;[^}]*margin: auto;/);
  });
});
