import { readFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

// Approved public-demo captures only; never household media, accounts, or invite tokens.
const root = new URL('../', import.meta.url);
const data = async (path, mime) => `data:${mime};base64,${(await readFile(new URL(path, root))).toString('base64')}`;
const television = await data('social/tvos-home-v1.jpg', 'image/jpeg');
const phone = await data('social/ios-home-v1.jpg', 'image/jpeg');
const roman = await data('public/fonts/Newsreader-variable.woff2', 'font/woff2');
const italic = await data('public/fonts/Newsreader-Italic-variable.woff2', 'font/woff2');
const icon = await data('public/media-mark.svg', 'image/svg+xml');
const output = new URL('public/social/', root);
await mkdir(output, { recursive: true });
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
  for (const [kind, version, headline, copy] of [
    ['share', 4, 'Your media.<br><em>On every<br>screen.</em>', 'Movies, series, music and books.<br>One self-hosted library.'],
    ['invite', 3, 'You’re invited.<br><em>Make yourself<br>at home.</em>', 'Movies, series, music and books.<br>A shared library, ready to explore.'],
  ]) {
    await page.setContent(`<!doctype html><html><head><meta charset="utf-8"><style>
      @font-face { font-family: Newsreader; src: url('${roman}'); font-style: normal; font-weight: 100 900; }
      @font-face { font-family: Newsreader; src: url('${italic}'); font-style: italic; font-weight: 100 900; }
      * { box-sizing: border-box; } html, body { width: 1200px; height: 630px; margin: 0; overflow: hidden; }
      body { position: relative; background: #0f0e0c; color: #f4f1ea; }
      .wash { position: absolute; inset: -80px -80px -80px 390px; background: url('${television}') center / cover; opacity: .32; filter: blur(60px); }
      .shade { position: absolute; inset: 0; background: linear-gradient(90deg, #0f0e0c 28%, transparent 88%); }
      .brand { position: absolute; left: 56px; top: 48px; display: flex; align-items: baseline; gap: 14px; }
      .brand img { width: 56px; height: 34px; transform: translateY(1.5px); }
      .brand span { font: 500 42px / 1 Newsreader, serif; letter-spacing: -.6px; }
      h1 { position: absolute; z-index: 2; left: 56px; top: 181px; width: 550px; margin: 0; font: 400 78px / 1.03 Newsreader, serif; letter-spacing: -1.8px; }
      h1 em { font-weight: 400; }
      .copy { position: absolute; z-index: 2; left: 58px; bottom: 49px; margin: 0; color: #d5d0c7; font: 400 21px / 1.5 Arial, sans-serif; }
      .television { position: absolute; left: 570px; top: 124px; width: 690px; padding: 8px; border-radius: 18px; background: #151517; box-shadow: 0 24px 70px #0008; transform: perspective(1300px) rotateY(-9deg) rotateZ(-2deg); }
      .television img { display: block; width: 100%; border-radius: 10px; }
      .phone { position: absolute; z-index: 1; left: 939px; top: 205px; width: 176px; padding: 6px; border-radius: 27px; background: #151517; box-shadow: 0 16px 48px #0009; transform: rotate(5deg); }
      .phone img { display: block; width: 100%; border-radius: 21px; }
    </style></head><body>
      <div class="wash"></div><div class="shade"></div>
      <div class="brand"><img src="${icon}" alt=""><span>Lumina</span></div>
      <h1>${headline}</h1><p class="copy">${copy}</p>
      <div class="television"><img src="${television}" alt="Lumina on Apple TV"></div>
      <div class="phone"><img src="${phone}" alt="Lumina on iPhone"></div>
    </body></html>`);
    await page.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.images].map((image) => image.decode())); });
    await page.screenshot({ path: fileURLToPath(new URL(`lumina-${kind}-v${version}.jpg`, output)), type: 'jpeg', quality: 90 });
  }
} finally { await browser.close(); }
