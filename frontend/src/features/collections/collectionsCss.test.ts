import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

// --g-rule is a full `1px solid ...` shorthand, so it must be the whole border value.
describe('collections.css', () => {
  it('uses --g-rule as a complete border shorthand', () => {
    const css = readFileSync('src/features/collections/collections.css', 'utf8');
    expect(css).not.toMatch(/\d+px\s+solid\s+var\(--g-rule\)/);
  });
});
