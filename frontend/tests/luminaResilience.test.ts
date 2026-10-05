// @vitest-environment node

import { readFileSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const testDirectory = fileURLToPath(new URL('.', import.meta.url));
// The split of lumina.css (app polish plan 00 §3): every file that holds a former lumina.css rule.
const SPLIT_CSS = ['styles/tokens.css', 'styles/base.css', 'ui/ui.css', 'libraryCuration.css', 'features/watch/watch.css', 'app/shell.css',
  'features/auth/auth.css', 'features/onboarding/onboarding.css', 'features/settings/settings.css', 'features/downloads/downloads.css',
  'features/collections/collections.css', 'features/watch/player.css', 'features/watch/tools.css'];
const css = SPLIT_CSS.map((file) => readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8')).join('\n');
const index = readFileSync(new URL('../index.html', import.meta.url), 'utf8');

function rule(selector: string) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(new RegExp(`(?:^|})\\s*${escaped}\\s*\\{([^}]+)\\}`));
  expect(match, `missing CSS rule for ${selector}`).not.toBeNull();
  return match?.[1] ?? '';
}

describe('Lumina interaction resilience contracts', () => {
  // Targets follow --g-control: 44px (48px at the 10-foot step) for touch and remotes, 36px only for a mouse or trackpad.
  it('keeps the 44 pixel control by default and shrinks it only for a fine pointer', () => {
    expect(css).toMatch(/:root\s*\{[^}]*--g-control:\s*44px/);
    expect(css).toMatch(/@media \(pointer: fine\) and \(min-width: 600px\) \{\s*:root \{[^}]*--g-control: 36px/);
    expect(css.match(/--g-control:\s*36px/g)).toHaveLength(1);
  });

  it.each([
    '.g-icon-button',
  ])('gives %s a square --g-control target', (selector) => {
    expect(rule(selector)).toMatch(/width:\s*var\(--g-control\)/);
    expect(rule(selector)).toMatch(/height:\s*var\(--g-control\)/);
  });

  it.each([
    '.g-chip, .g-button',
    '.g-text-button',
    '.description-card summary',
  ])('gives %s a minimum --g-control target', (selector) => {
    expect(rule(selector)).toMatch(/min-height:\s*var\(--g-control\)/);
  });

  it('bundles Newsreader locally and opts into safe-area viewport handling', () => {
    expect(css).toContain('@font-face');
    expect(css).toContain("url('/fonts/Newsreader-variable.woff2')");
    expect(statSync(`${testDirectory}/../public/fonts/Newsreader-variable.woff2`).size).toBeGreaterThan(100_000);
    expect(statSync(`${testDirectory}/../public/fonts/OFL.txt`).size).toBeGreaterThan(1_000);
    expect(index).toMatch(/content="width=device-width, initial-scale=1\.0, viewport-fit=cover"/);
  });

  it('accounts for device safe areas and reduced motion', () => {
    expect(css).toContain('env(safe-area-inset-top)');
    expect(css).toContain('env(safe-area-inset-right)');
    expect(css).toContain('env(safe-area-inset-bottom)');
    expect(css).toContain('env(safe-area-inset-left)');
    expect(css).toContain('@media (prefers-reduced-motion: reduce)');
  });
});
