/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const html = readFileSync('index.html', 'utf8');
const favicon = readFileSync('public/favicon.svg', 'utf8');
const base = readFileSync('src/styles/base.css', 'utf8');

describe('index.html and the brand assets', () => {
  it('loads common Newsreader glyphs from smaller split files without forcing fonts ahead of the app', () => {
    const faces = ['Newsreader-variable.woff2', 'Newsreader-Italic-variable.woff2', 'Newsreader-Latin-variable.woff2', 'Newsreader-Italic-Latin-variable.woff2'];
    for (const face of faces) {
      expect(html).not.toContain(`href="/fonts/${face}"`);
      expect(base).toContain(`url('/fonts/${face}')`);
    }
    expect(readFileSync('public/fonts/Newsreader-Latin-variable.woff2').length).toBeLessThan(readFileSync('public/fonts/Newsreader-variable.woff2').length);
    expect(readFileSync('public/fonts/Newsreader-Italic-Latin-variable.woff2').length).toBeLessThan(readFileSync('public/fonts/Newsreader-Italic-variable.woff2').length);
    expect(readFileSync('public/fonts/OFL.txt', 'utf8')).toContain('Copyright 2020 The Newsreader Project Authors');
    expect(base.match(/font-display: swap/g)).toHaveLength(4);
    expect(base.match(/unicode-range: U\+0000-00FF, U\+2000-206F, U\+20AC, U\+2122/g)).toHaveLength(2);
    for (const character of '“Hello”—she said… €™') {
      const point = character.codePointAt(0)!;
      expect(point <= 0xff || (point >= 0x2000 && point <= 0x206f) || point === 0x20ac || point === 0x2122).toBe(true);
    }
    expect(base).not.toContain('U+0100-10FFFF');
    expect(base).toContain("font-family: 'Newsreader Fallback'");
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

describe('social preview images', () => {
  it.each(['share-v1', 'share-v2', 'invite-v1'])('ships a compact 1200×630 JPEG for %s links', (kind) => {
    const bytes = readFileSync(`public/social/lumina-${kind}.jpg`);
    expect(bytes.subarray(0, 2).toString('hex')).toBe('ffd8');
    const frame = bytes.indexOf(Buffer.from([0xff, 0xc0]));
    expect(frame).toBeGreaterThan(0);
    expect(bytes.readUInt16BE(frame + 5)).toBe(630);
    expect(bytes.readUInt16BE(frame + 7)).toBe(1200);
    expect(bytes.length).toBeLessThan(400_000);
  });
});

describe('email artwork', () => {
  it.each(['film-archive', 'security-keys'])('ships a compact retina %s banner', (name) => {
    const banner = readFileSync(`public/email/${name}-v1.jpg`);
    const frame = banner.indexOf(Buffer.from([0xff, 0xc0]));
    expect(frame).toBeGreaterThan(0);
    expect(banner.readUInt16BE(frame + 5)).toBe(480);
    expect(banner.readUInt16BE(frame + 7)).toBe(1280);
    expect(banner.length).toBeLessThan(250_000);
  });
  it('ships a compact retina vault banner and an unboxed horizon mark', () => {
    const banner = readFileSync('public/email/vault-v1.jpg');
    const frame = banner.indexOf(Buffer.from([0xff, 0xc0]));
    expect(banner.readUInt16BE(frame + 5)).toBe(480);
    expect(banner.readUInt16BE(frame + 7)).toBe(1280);
    expect(banner.length).toBeLessThan(250_000);
    const mark = readFileSync('public/email/mark-v1.png');
    expect(mark.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a');
    expect(mark.readUInt32BE(16)).toBe(144);
    expect(mark.readUInt32BE(20)).toBe(112);
    expect(mark[25]).toBe(6); // RGBA: the horizon sits on transparency, without the old icon box.
  });
});
