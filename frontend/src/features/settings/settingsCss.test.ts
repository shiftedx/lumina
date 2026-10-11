/// <reference types="node" />
import { execSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const strip = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, '');
const settings = strip(readFileSync('src/features/settings/settings.css', 'utf8'));
const ui = strip(readFileSync('src/ui/ui.css', 'utf8'));
const selectors = (css: string) => [...css.matchAll(/(?:^|[}{])\s*([^{}@]+)\{/g)].flatMap((m) => m[1].split(',').map((s) => s.trim())).filter(Boolean);
const settingsSelectors = selectors(settings);

describe('settings.css', () => {
  it.each(['.g-table', '.g-tabs', '.g-status', '.g-choice', '.g-list-row'])('does not redefine the primitive base %s that ui.css owns', (base) => {
    expect(selectors(ui).some((s) => s === base || s.startsWith(`${base} `))).toBe(true);
    // a settings.css rule on the bare base (or its th/td/tab/svg internals) would be a second definition
    const duplicates = settingsSelectors.filter((s) => s === base || new RegExp(`^${base.replace('.', '\\.')} (th|td|thead th|> \\[role='tab'\\]|svg|\\.is-)`).test(s) || new RegExp(`^${base.replace('.', '\\.')}\\.is-`).test(s));
    // the only allowed override is the one settings row layout rule on .g-list-row
    expect(duplicates.filter((s) => s !== '.g-list-row')).toEqual([]);
  });

  it('defines each shared name once', () => {
    const count = (selector: string) => settingsSelectors.filter((s) => s === selector).length;
    for (const selector of ['.g-list', '.g-list-row', '.g-setting-note', '.g-panel', '.g-stat-strip', '.g-log', '.g-mono', '.g-fieldset']) expect(count(selector), selector).toBeLessThanOrEqual(1);
  });

  it('keeps no rule for a class that no Settings or admin source uses', () => {
    const source = execSync("cat $(find src -name '*.ts' -o -name '*.tsx' | grep -v '\\.test\\.')", { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
    const unused = [...new Set([...settings.matchAll(/\.([a-zA-Z_][\w-]*)/g)].map((m) => m[1]))].filter((name) => !/^\d/.test(name) && !source.includes(name));
    expect(unused).toEqual([]);
  });
});

const rule = (selector: string) => settings.match(new RegExp(`(?:^|\\})\\s*${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\{([^}]*)\\}`, 'm'))?.[1] ?? '';

describe('full-width Settings (owner 2026-10-01)', () => {
  it('insets search text from both borders', () => {
    expect(rule('.g-settings-search .g-input')).toContain('padding: 0 12px');
  });
  it('lets the shell use the whole width beside a token-width sidebar', () => {
    const shell = rule('.g-settings');
    expect(shell).not.toContain('max-width');
    expect(shell).toContain('grid-template-columns: var(--g-sidebar) minmax(0, 1fr)');
  });
  it('uses a short category list without an independently scrolling sidebar', () => {
    const side = rule('.g-settings-sidebar');
    expect(side).not.toContain('overflow-y: auto');
    expect(side).not.toContain('max-height');
  });
  it('keeps search visible in phone detail pages and swaps subpage links for a labelled select', () => {
    expect(settings).toContain("grid-template-areas: 'head' 'search' 'side' 'pane'");
    expect(settings).not.toContain(".g-settings[data-view='detail'] .g-settings-search");
    expect(settings).toContain('.g-settings-page-select { display: block;');
    expect(settings).toContain('.g-settings-pages { display: none;');
  });
  it('puts every control in one right-hand column of the same width in every row and sub-row, and never flows rows into columns', () => {
    expect(rule('.g-setting-row')).toContain('grid-template-columns: minmax(0, 1fr) min(20rem, 48%)');
    expect(settings).toMatch(/\.g-subrows > \.g-field, [^{]*\{[^}]*grid-template-columns: minmax\(0, 1fr\) min\(20rem, 48%\)/);
    expect(settings).not.toMatch(/\.g-settings-section[^{]*\{[^}]*(column-count|grid-template-columns)/);
  });
});
