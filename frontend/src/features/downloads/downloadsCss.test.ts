import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

describe('downloads.css', () => {
  // content-visibility:auto paints-contain each row; the 5px focus ring of the right-most action needs inline room.
  it('pads .g-list-row inline so contained rows do not clip focus rings', () => {
    const css = readFileSync('src/features/downloads/downloads.css', 'utf8')
    expect(css).toMatch(/\.g-list-row\s*\{[^}]*padding:\s*12px\s+8px/)
  })
  // settings.css loads later and makes every .g-list-row a flex row; without the compound selector the copy column centres.
  it('keeps .g-job a grid even though settings.css makes .g-list-row flex', () => {
    const css = readFileSync('src/features/downloads/downloads.css', 'utf8')
    expect(css).toMatch(/\.g-list-row\.g-job\s*\{\s*display:\s*grid/)
  })
})
