import { readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { describe, expect, it } from 'vitest';
import { CORE_BUDGET, CSS_BUDGET, cssHits, debtErrors, debtListErrors, forbiddenHits, gzipSize, isAllowed, liveTokenHits, redHits, redProblems, retiredLiveHits, smallTextHits, tokenDefinitionHits, tsHits } from './check-styles.mjs';

describe('check-styles', () => {
  it('flags hex, rgb() and hsl() in declaration values', () => {
    expect(cssHits('.a { color: #fff; }\n.b { background: rgba(0, 0, 0, .5); }\n.c { fill: hsl(0 0% 0%); }').map((hit) => hit.line)).toEqual([1, 2, 3]);
  });
  it('never scans selectors', () => {
    expect(cssHits('#main-content, #add, .x[data-c="#fff"] { outline: 0; }')).toEqual([]);
  });
  it('ignores issue numbers in comments', () => {
    expect(cssHits('/* issue #110, #abc */ .a { color: var(--g-ink); }')).toEqual([]);
    expect(tsHits('// see #137\nconst a = 1; /* #F13 */')).toEqual([]);
  });
  it('allows rgb(var(...)), color-mix with tokens and system colours', () => {
    expect(cssHits('.a { color: rgb(var(--x) / .5); background: color-mix(in srgb, var(--g-ink) 8%, transparent); border-color: CanvasText; }')).toEqual([]);
  });
  it('flags a named colour only in a colour property', () => {
    expect(cssHits(".a { color: white; }\n.b { content: 'white'; }\n.c { --tint: black; }").map((hit) => hit.line)).toEqual([1, 3]);
  });
  it('flags a literal in a custom property definition outside tokens.css', () => {
    expect(cssHits(':root { --g-live-gold: #e5a00d; }\n.a { --chip: rgb(21 19 15 / .86); }').map((hit) => hit.line)).toEqual([1, 2]);
    expect(cssHits(':root { --g-live-gold: var(--g-gold); }')).toEqual([]);
  });
  it('catches the colour functions and names the retired gallery lint caught', () => {
    expect(cssHits('.a { color: oklch(70% .1 30); }\n.b { background: hwb(0 0% 0%); }\n.c { border-color: color(srgb 1 0 0); }\n.d { color: gold; }\n.e { background: color-mix(in srgb, var(--g-ink) 8%, transparent); }').map((hit) => hit.line)).toEqual([1, 2, 3, 4]);
  });
  it('flags hex in a TS string but not in prose', () => {
    expect(tsHits("const a = { color: '#fff' };\nconst b = `issue #110`;\nstyle={{ background: 'rgb(0 0 0 / .5)' }}").map((hit) => hit.line)).toEqual([1, 3]);
  });
  it('allowlists tokens.css, galleryModel.ts and tests', () => {
    expect(isAllowed('src/styles/tokens.css')).toBe(true);
    expect(isAllowed('src/features/gallery/galleryModel.ts')).toBe(true);
    expect(isAllowed('src/ui/Button.test.tsx')).toBe(true);
    expect(isAllowed('src/test/galleryFixtures.ts')).toBe(true);
    expect(isAllowed('src/ui/ui.css')).toBe(false);
  });
  it('fails a debt entry that is already clean, and one that does not exist', () => {
    const hitsByFile = new Map([['src/a.css', 2]]);
    expect(debtErrors(['src/a.css', 'src/b.css', 'src/gone.css'], hitsByFile, (file) => file !== 'src/gone.css'))
      .toEqual(['src/b.css: no colour literals left; remove it from scripts/style-debt.txt', 'src/gone.css: listed in scripts/style-debt.txt but missing']);
  });
  it('forbids retired token names once listed', () => {
    expect(forbiddenHits('.a { color: var(--text-muted); }\n.b { color: var(--g-ink); }', ['text-(primary|muted)'])).toEqual([{ line: 1, text: '--text-muted' }]);
  });
  it('finds red and pink literals by hue anywhere, custom properties included', () => {
    expect(redHits('.a-live { color: #ff89a2; }\n:root { --g-live-pink: #ff89a2; }\n.b-lifecycle { color: rgba(192, 38, 74, .5); }\n.c { background: color-mix(in srgb, #f00 40%, transparent); }').map((hit) => hit.line)).toEqual([1, 2, 3, 4]);
  });
  it('leaves the danger tokens, golds and neutrals alone', () => {
    expect(redHits(':root { --g-danger: #e58a74; --g-danger-wash: color-mix(in srgb, var(--g-danger) 12%, transparent); }\n:root { --g-gold: #e5a00d; --g-ink: #efe9dd; --g-scrim-modal: rgb(0 0 0 / .6); --g-art-chip: rgb(21 19 15 / .86); }')).toEqual([]);
    expect(redHits('.err { color: var(--g-danger); }')).toEqual([]);
  });
  it('scans debt files and tokens.css for red, which the literal rule skips', () => {
    const files = [['src/features/watch/player.css', '.player-live { color: #ff89a2; }\n.err { color: rgba(221, 141, 120, .4); }'], ['src/styles/tokens.css', ':root { --g-pink: #ff89a2; }'], ['src/ok.css', '.a { color: var(--g-ink); }'], ['src/clean.css', '.err { color: #dd8d78; }']];
    // The legacy coral danger literal is allowed only in a debt file; pink is never allowed.
    expect(redProblems(files, ['src/features/watch/player.css'])).toEqual(['src/features/watch/player.css:1: red or pink #ff89a2', 'src/styles/tokens.css:1: red or pink #ff89a2', 'src/clean.css:1: red or pink #dd8d78']);
  });
  it('bans the retired live reds and classes in every stylesheet', () => {
    expect(retiredLiveHits('.player-live-badge { color: #c0264a; }\n.x { color: rgba(105, 30, 22, .4); }').map((hit) => hit.line)).toEqual([1, 1, 2]);
    expect(retiredLiveHits('.g-live { color: var(--g-ink); }')).toEqual([]);
  });
  it('a --g-* token is defined only in tokens.css, whatever the debt list says (D1, contracts §5.1 rule 7)', () => {
    expect(tokenDefinitionHits('.a { --g-gold: #000; }\n@media (min-width: 1600px) { .b { --g-gutter: 72px; } }').map((hit) => [hit.line, hit.text])).toEqual([[1, '--g-gold'], [2, '--g-gutter']]);
    expect(tokenDefinitionHits('.c { color: var(--g-ink, #fff); --h-gap: 24px; background: var(--g-gold); }')).toEqual([]);
    expect(tokenDefinitionHits("el.style.setProperty('--g-gold', x);\nconst s = { '--g-ink': y };", true).map((hit) => hit.text)).toEqual(['--g-gold', '--g-ink']);
    expect(tokenDefinitionHits("const s = { '--g-toast-offset': '72px', '--g-avatar-size': '32px', '--g-accent': accent };", true)).toEqual([]);
  });
  it('sums gzip sizes', () => {
    const text = 'a'.repeat(1000);
    expect(gzipSize(text)).toBe(gzipSync(text, { level: 9 }).length);
  });
});

describe('check-styles after integration', () => {
  it('forbids the legacy names', () => {
    const patterns = ['surface(-[a-z]+)?', 'text-(primary|strong|body|secondary|muted)', 'accent(-[a-z]+)?', 'line(-strong)?', 'border-subtle', 'danger', 'success', 'shadow', '[a-z]+-rgb', '(ink|pearl|teal|caramel|coral)-[0-9]+'];
    expect(forbiddenHits('.a { color: var(--text-muted); background: var(--surface-panel); }', patterns)).toHaveLength(2);
    expect(forbiddenHits('.a { color: var(--g-danger); box-shadow: var(--g-elev-1); }', patterns)).toEqual([]);
  });
  it('forbids the retired layout aliases the shell once defined', () => {
    const file = readFileSync('scripts/forbidden-tokens.txt', 'utf8').split('\n').filter((line) => line && !line.startsWith('#'));
    expect(forbiddenHits('.a { margin-left: var(--sidebar-width); top: var(--topbar-height); }', file)).toHaveLength(2);
  });
  it('fails when the debt list is not empty', () => {
    expect(debtListErrors(['src/a.css'])).toEqual(['scripts/style-debt.txt must be empty after integration; it lists src/a.css']);
    expect(debtListErrors([])).toEqual([]);
  });
  it('flags text below 11px, except the phone label token', () => {
    const css = '.a { font-size: 9px; }\n.b { font: 600 10px / 1 var(--g-sans); }\n.c { font-size: 11px; }\n:root { --g-type-label: 600 10px / 1.3 var(--g-sans); }';
    expect(smallTextHits(css).map((hit) => hit.line)).toEqual([1, 2]);
  });
  it('holds CSS to 44 KB', () => {
    expect(CSS_BUDGET).toBe(44 * 1024);
  });
  it('holds tokens + ui to 7 KB', () => {
    expect(CORE_BUDGET).toBe(7 * 1024);
  });
  // Part C backlog: the hue guard reads colour literals only, so a live rule painted with a danger token passes it.
  it('forbids danger and red tokens in live, lifecycle, upcoming and timecode rules', () => {
    expect(liveTokenHits('.g-live.is-live { color: var(--g-danger); }\n.player-timecode--live { background: var(--g-error); }')).toHaveLength(2);
    expect(liveTokenHits('.g-live.is-live .g-live-dot { background: var(--g-gold); }\n.err { color: var(--g-danger); }')).toEqual([]);
  });
});
