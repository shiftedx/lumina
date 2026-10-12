/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const html = readFileSync('index.html', 'utf8');
const favicon = readFileSync('public/favicon.svg', 'utf8');

describe('index.html and the brand assets', () => {
  it('preloads both Newsreader faces', () => {
    for (const face of ['Newsreader-variable.woff2', 'Newsreader-Italic-variable.woff2']) {
      expect(html).toContain(`<link rel="preload" href="/fonts/${face}" as="font" type="font/woff2" crossorigin />`);
    }
  });
  it('declares a paper theme-color per scheme and busts the favicon cache', () => {
    expect(html).toContain('<meta name="theme-color" media="(prefers-color-scheme: light)" content="#f4f1ea" />');
    expect(html).toContain('<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#0f0e0c" />');
    expect(html).toContain('/favicon.svg?v=lumina-3');
  });
  it('restores the 1.9.0 hairline-sun favicon, not the 2.0.0 paper/ink/gold mark', () => {
    expect(favicon).toContain('stroke="#D2A875"');
    expect(favicon).not.toContain('#E5A00D'.toLowerCase());
    expect(favicon).not.toContain('#f4f1ea');
  });
});
