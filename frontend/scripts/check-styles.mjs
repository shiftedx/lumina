#!/usr/bin/env node
// Style check (app polish spec 1.8 step 4, 14; plan 00 §5). Node built-ins only. Runs after `vite build`.
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { gzipSync } from 'node:zlib';

const KB = 1024;
export const CSS_BUDGET = 44 * KB; // +1 KB for 2.9 sign-in release showcase (art, glass panel, pips; the calm fallback stays), +2 KB for the lazy editor chunk, +1 KB for 2.3.0 Streaming (a new page; Live/Explore/Subscriptions mastheads already trimmed), +4 KB for 2.6.0 Requests (a new tab, 3.4 KB, and the player CSS now its own chunk shared with the inline trailer); re-tighten by splitting route CSS if it grows again
export const CORE_BUDGET = 7 * KB; // tokens.css + ui.css (spec 1.8)
export const CHUNK_BUDGETS = { ui: 8 * KB, palette: 12 * KB };
const HEX = /#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})\b/gi;
const FUNC = /\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\((?!\s*var\()/gi; // `color-mix(` is not `color(`; the retired galleryColours lint's superset
const NAMED = /\b(white|black|red|green|blue|gray|grey|silver|orange|yellow|purple|pink|maroon|navy|teal|olive|lime|aqua|fuchsia|gold)\b/i;
const COLOUR_PROP = /^(--|color$|background|border|outline|fill$|stroke$|.*shadow$|caret-color$|accent-color$|text-decoration)/;
/** The old teal-era danger literals (`--coral-300`, the light `--danger`, their rgba tints). Danger is functional, so they are allowed only in a style-debt file until its owner migrates it to `var(--g-danger)`. */
const LEGACY_DANGER = /^(?:#dd8d78|#963e32|rgba?\(\s*221[\s,]+141[\s,]+120)/i;
const RGB_FN = /rgba?\(\s*(\d+(?:\.\d+)?)[\s,]+(\d+(?:\.\d+)?)[\s,]+(\d+(?:\.\d+)?)/gi;
const RETIRED_REDS = /#c0264a|rgba\(\s*105\s*,\s*30\s*,\s*22|rgba\(\s*255\s*,\s*132\s*,\s*107|rgba\(\s*192\s*,\s*38\s*,\s*74/gi;
const RETIRED_LIVE = /\.(?:lifecycle-badge|watch-lifecycle-badge|playlist-lifecycle-badge|player-live-badge|player-live-dot|channel-live|live-provider)\b/g;
const TS_LITERAL = /(?:['"`]|[:(,]\s*)(#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})\b|(?:rgba?|hsla?)\((?!\s*var\())/gi;

export const gzipSize = (text) => gzipSync(text, { level: 9 }).length;
const lineAt = (text, index) => text.slice(0, index).split('\n').length;

/** Blanks comments (and, for TS, leaves strings alone) while keeping every newline, so line numbers survive. */
export function stripComments(text, ts = false) {
  let out = '';
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (ts && (char === '"' || char === "'" || char === '`')) {
      const start = i; i += 1;
      while (i < text.length && text[i] !== char) { if (text[i] === '\\') i += 1; i += 1; }
      out += text.slice(start, i + 1); continue;
    }
    if (char === '/' && text[i + 1] === '*') {
      const end = text.indexOf('*/', i + 2); const stop = end === -1 ? text.length : end + 2;
      out += text.slice(i, stop).replace(/[^\n]/g, ' '); i = stop - 1; continue;
    }
    if (ts && char === '/' && text[i + 1] === '/') {
      const end = text.indexOf('\n', i); const stop = end === -1 ? text.length : end;
      out += ' '.repeat(stop - i); i = stop - 1; continue;
    }
    out += char;
  }
  return out;
}

/** Hits in CSS declaration values only; selectors and at-rule preludes are never scanned. */
export function cssHits(source) {
  const text = stripComments(source);
  const hits = [];
  let start = 0;
  for (let i = 0; i <= text.length; i += 1) {
    const char = text[i];
    if (char !== '{' && char !== '}' && char !== ';' && i !== text.length) continue;
    const segment = text.slice(start, i);
    if (char !== '{') {
      const colon = segment.indexOf(':');
      if (colon > 0 && !segment.trim().startsWith('@')) {
        const prop = segment.slice(0, colon).trim().toLowerCase();
        const value = segment.slice(colon + 1);
        const valueStart = start + colon + 1;
        for (const regex of [HEX, FUNC]) {
          for (const match of value.matchAll(regex)) hits.push({ line: lineAt(text, valueStart + match.index), text: match[0] });
        }
        const named = COLOUR_PROP.test(prop) ? NAMED.exec(value.replace(/(['"]).*?\1/g, '').replace(/var\([^)]*\)/g, '')) : null; // a token's name (var(--g-gold)) is not a colour name
        if (named) hits.push({ line: lineAt(text, valueStart + value.indexOf(named[0])), text: named[0] });
      }
    }
    start = i + 1;
  }
  return hits;
}

/** Red and pink by hue (hue <= 20 or >= 330 degrees, saturation > 40%): the guard that lived in redColours.test.ts, moved here so the check owns it. */
export function isRedOrPink(r, g, b) {
  const max = Math.max(r, g, b) / 255; const min = Math.min(r, g, b) / 255; const delta = max - min;
  if (delta === 0) return false;
  const saturation = delta / (1 - Math.abs(max + min - 1));
  const hue = (((max === r / 255 ? ((g - b) / 255) / delta : max === g / 255 ? 2 + ((b - r) / 255) / delta : 4 + ((r - g) / 255) / delta) * 60) + 360) % 360;
  return saturation > 0.4 && (hue <= 20 || hue >= 330);
}

function hexIsRed(literal) {
  const hex = literal.slice(1);
  const full = hex.length <= 4 ? [...hex.slice(0, 3)].map((c) => c + c).join('') : hex.slice(0, 6);
  return isRedOrPink(...[0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)));
}

/** Red or pink literals in ANY declaration, custom property definitions included, except the `--g-danger*` tokens (danger is a functional colour).
 *  A red cannot hide behind a token: the literal is what is found, so `--x: #ff89a2` and `color: var(--x)` fail at the definition. */
export function redHits(source, allowLegacyDanger = false) {
  const text = stripComments(source);
  const hits = [];
  for (const rule of text.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    let offset = rule.index + rule[1].length + 1;
    for (const declaration of rule[2].split(';')) {
      const colon = declaration.indexOf(':');
      const prop = colon < 0 ? '' : declaration.slice(0, colon).trim().toLowerCase();
      if (colon > 0 && !prop.startsWith('--g-danger')) {
        const value = declaration.slice(colon + 1);
        const found = [];
        for (const match of value.matchAll(HEX)) if (hexIsRed(match[0])) found.push(match[0]);
        for (const match of value.matchAll(RGB_FN)) if (isRedOrPink(Number(match[1]), Number(match[2]), Number(match[3]))) found.push(match[0]);
        for (const literal of found.filter((text) => !(allowLegacyDanger && LEGACY_DANGER.test(text)))) hits.push({ line: lineAt(text, offset + colon + 1 + value.indexOf(literal)), text: literal });
      }
      offset += declaration.length + 1;
    }
  }
  return hits;
}

/** `files` is [path, source] pairs; every stylesheet is scanned, whether or not the literal rule skips it (debt files, tokens.css). */
export function redProblems(files, debt = []) {
  return files.flatMap(([file, source]) => redHits(source, debt.includes(file)).map((hit) => `${file}:${hit.line}: red or pink ${hit.text}`));
}

export function retiredLiveHits(source) {
  const text = stripComments(source);
  return [...text.matchAll(RETIRED_REDS), ...text.matchAll(RETIRED_LIVE)].map((match) => ({ line: lineAt(text, match.index), text: match[0] })).sort((a, b) => a.line - b.line);
}

// Runtime-set --g-* names (contracts §4.1): the mini player's toast offset, ChannelAvatar's size, the title/album pages' artwork accent.
const RUNTIME_TOKENS = new Set(['--g-toast-offset', '--g-avatar-size', '--g-accent']);
/** D1 (contracts §5.1 rule 7): a --g-* custom property is defined only in src/styles/tokens.css; style-debt.txt never waives it. */
export function tokenDefinitionHits(source, ts = false) {
  const text = stripComments(source, ts);
  const pattern = ts ? /['"](--g-[\w-]+)['"]\s*[:,]/g : /(?<![\w-])(--g-[\w-]+)\s*:/g;
  return [...text.matchAll(pattern)].filter((match) => !RUNTIME_TOKENS.has(match[1])).map((match) => ({ line: lineAt(text, match.index), text: match[1] }));
}

export function tsHits(source) {
  const text = stripComments(source, true);
  return [...text.matchAll(TS_LITERAL)].map((match) => ({ line: lineAt(text, match.index), text: match[1] }));
}

export function isAllowed(file) {
  return file === 'src/styles/tokens.css' || file === 'src/features/gallery/galleryModel.ts' || /\.test\.[^/]+$/.test(file) || file.startsWith('src/test/');
}

export function debtErrors(debt, hitsByFile, exists) {
  return debt.flatMap((file) => {
    if (!exists(file)) return [`${file}: listed in scripts/style-debt.txt but missing`];
    return (hitsByFile.get(file) ?? 0) === 0 ? [`${file}: no colour literals left; remove it from scripts/style-debt.txt`] : [];
  });
}

export function forbiddenHits(source, patterns) {
  if (!patterns.length) return [];
  const regex = new RegExp(`(?<![\\w-])--(?:${patterns.join('|')})(?![\\w-])`, 'g');
  const text = stripComments(source);
  return [...text.matchAll(regex)].map((match) => ({ line: lineAt(text, match.index), text: match[0] }));
}

export function debtListErrors(debt) {
  return debt.length ? [`scripts/style-debt.txt must be empty after integration; it lists ${debt.join(', ')}`] : [];
}

const SMALL_TEXT = /(?<![\w-])font(?:-size)?\s*:[^;{}]*?(?<![\w.-])(\d+(?:\.\d+)?)px/g;
/** Spec 1.5: no text below 11px. Only `font`/`font-size` declarations are read, so --g-type-label's 10px phone value
 *  (a custom property, used through `font: var(--g-type-label)`) is never a hit. */
export function smallTextHits(source) {
  const text = stripComments(source);
  return [...text.matchAll(SMALL_TEXT)]
    .filter((match) => Number(match[1]) < 11)
    .map((match) => ({ line: lineAt(text, match.index), text: `${match[1]}px` }));
}

const LIVE_SELECTOR = /live|lifecycle|upcoming|timecode/i;
const RED_TOKEN = /var\(\s*--(?:g-)?(?:danger|error|red|coral)[a-z0-9-]*/i;
/** Part C backlog: live state is gold or ink, never a danger or red token (the hue guard sees literals only). */
export function liveTokenHits(source) {
  const text = stripComments(source);
  return [...text.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter((rule) => LIVE_SELECTOR.test(rule[1]) && RED_TOKEN.test(rule[2]))
    .map((rule) => ({ line: lineAt(text, rule.index), text: rule[1].trim() }));
}

const listFile = (path) => (existsSync(path) ? readFileSync(path, 'utf8').split('\n').map((line) => line.trim()).filter((line) => line && !line.startsWith('#')) : []);
function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

function main() {
  const root = join(dirname(fileURLToPath(import.meta.url)), '..');
  const problems = [];
  const debt = listFile(join(root, 'scripts/style-debt.txt'));
  const forbidden = listFile(join(root, 'scripts/forbidden-tokens.txt'));
  const hitsByFile = new Map();
  const redFiles = [];
  for (const path of walk(join(root, 'src')).filter((file) => /\.(css|tsx?)$/.test(file))) {
    const file = relative(root, path).split('\\').join('/');
    const source = readFileSync(path, 'utf8');
    for (const hit of forbiddenHits(source, forbidden)) problems.push(`${file}:${hit.line}: retired token ${hit.text}`);
    if (!/\.test\.[^/]+$/.test(file) && !file.startsWith('src/test/')) {
      // Red and pink are checked in every stylesheet (debt files and tokens.css included) and in TS hex literals.
      if (file.endsWith('.css')) {
        for (const hit of liveTokenHits(source)) problems.push(`${file}:${hit.line}: ${hit.text} uses a danger or red token; live state is gold or ink`);
        for (const hit of smallTextHits(source)) problems.push(`${file}:${hit.line}: text below 11px (${hit.text}); use a --g-type-* token`);
        redFiles.push([file, source]); for (const hit of retiredLiveHits(source)) problems.push(`${file}:${hit.line}: retired live colour or class ${hit.text}`); }
      else for (const hit of tsHits(source)) if (hit.text.startsWith('#') && hexIsRed(hit.text)) problems.push(`${file}:${hit.line}: red or pink ${hit.text}`);
    }
    if (file !== 'src/styles/tokens.css' && !/\.test\.[^/]+$/.test(file) && !file.startsWith('src/test/')) {
      // D1: checked before the allowlist and the debt list, so neither can waive it.
      for (const hit of tokenDefinitionHits(source, !file.endsWith('.css'))) problems.push(`${file}:${hit.line}: ${hit.text} is defined outside src/styles/tokens.css`);
    }
    if (isAllowed(file)) continue;
    const hits = file.endsWith('.css') ? cssHits(source) : tsHits(source);
    hitsByFile.set(file, hits.length);
    if (debt.includes(file)) continue;
    for (const hit of hits) problems.push(`${file}:${hit.line}: colour literal ${hit.text} (use a token from src/styles/tokens.css)`);
  }
  problems.push(...debtListErrors(debt));
  problems.push(...debtErrors(debt, hitsByFile, (file) => existsSync(join(root, file))));
  problems.push(...redProblems(redFiles, debt));

  const assets = join(root, 'dist/assets');
  if (!existsSync(assets)) problems.push('dist/assets is missing: run vite build first');
  else {
    let total = 0;
    for (const name of readdirSync(assets).filter((file) => file.endsWith('.css'))) {
      const size = gzipSize(readFileSync(join(assets, name)));
      total += size; console.log(`css ${name}: ${(size / KB).toFixed(1)} KB gzip`);
    }
    console.log(`css total: ${(total / KB).toFixed(1)} KB gzip (budget ${CSS_BUDGET / KB} KB)`);
    if (total > CSS_BUDGET) problems.push(`CSS is ${(total / KB).toFixed(1)} KB gzip, over the ${CSS_BUDGET / KB} KB budget`);
    const core = gzipSize(['src/styles/tokens.css', 'src/ui/ui.css'].map((file) => readFileSync(join(root, file), 'utf8')).join('\n'));
    console.log(`tokens.css + ui.css: ${(core / KB).toFixed(1)} KB gzip (budget ${CORE_BUDGET / KB} KB)`);
    if (core > CORE_BUDGET) problems.push(`tokens.css + ui.css are ${(core / KB).toFixed(1)} KB gzip, over ${CORE_BUDGET / KB} KB`);
    for (const [chunk, budget] of Object.entries(CHUNK_BUDGETS)) {
      const file = readdirSync(assets).find((name) => name.startsWith(`${chunk}-`) && name.endsWith('.js'));
      if (!file) { console.log(`${chunk} chunk absent`); continue; }
      const size = gzipSize(readFileSync(join(assets, file)));
      console.log(`${chunk} chunk: ${(size / KB).toFixed(1)} KB gzip (budget ${budget / KB} KB)`);
      if (size > budget) problems.push(`${chunk} chunk is ${(size / KB).toFixed(1)} KB gzip, over ${budget / KB} KB`);
    }
  }
  if (problems.length) { console.error(problems.join('\n')); process.exit(1); }
  console.log('check-styles: ok');
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) main();
