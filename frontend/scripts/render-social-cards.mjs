import { readFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

// Static, original artwork only. These previews never depend on a household's media or members.
const root = new URL('../', import.meta.url);
const data = async (path, mime) => `data:${mime};base64,${(await readFile(new URL(path, root))).toString('base64')}`;
const vault = await data('social/share-art.png', 'image/png');
const archive = await data('public/onboarding/film-archive-v1.jpg', 'image/jpeg');
const roman = await data('public/fonts/Newsreader-variable.woff2', 'font/woff2');
const italic = await data('public/fonts/Newsreader-Italic-variable.woff2', 'font/woff2');
const icon = await data('public/media-mark.svg', 'image/svg+xml');
const mark = `<img src="${icon}" alt="" width="64" height="39">`;
const output = new URL('public/social/', root);
await mkdir(output, { recursive: true });
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
  for (const [kind, kicker, headline, copy] of [
    ['share', 'THE HOUSEHOLD MEDIA VAULT', 'Your media.<br>Your vault.', 'Movies, music, and favorites.<br>One household. One media vault.'],
    ['invite', 'YOUR LUMINA INVITATION', 'Step inside<br>the vault.', 'You’re invited to explore<br>a Lumina media vault.'],
  ]) {
    const art = kind === 'share' ? archive : vault;
    const artSize = kind === 'share' ? 'right center / 650px auto' : 'center / cover';
    await page.setContent(`<!doctype html><html><head><meta charset="utf-8"><style>
      @font-face { font-family: Newsreader; src: url('${roman}'); font-style: normal; font-weight: 100 900; }
      @font-face { font-family: Newsreader; src: url('${italic}'); font-style: italic; font-weight: 100 900; }
      * { box-sizing: border-box; } html, body { width: 1200px; height: 630px; margin: 0; overflow: hidden; }
      body { background: #0f0e0c url('${art}') ${artSize} no-repeat; color: #f4f1ea; }
      .brand { position: absolute; left: 64px; top: 59px; display: flex; align-items: center; gap: 17px; }
      .brand img { width: 64px; height: 39px; }
      .brand span { padding-top: 6px; font: 500 34px / 1 Newsreader, serif; letter-spacing: 4px; }
      .kicker { position: absolute; left: 68px; top: 180px; margin: 0; color: #D2A875; font: 600 13px / 1.4 Arial, sans-serif; letter-spacing: 2.3px; }
      h1 { position: absolute; left: 64px; top: 217px; width: 455px; margin: 0; font: italic 700 72px / 1.02 Newsreader, serif; letter-spacing: -1.5px; }
      .copy { position: absolute; left: 68px; top: 430px; margin: 0; color: #ded7c9; font: 400 22px / 1.55 Arial, sans-serif; }
      .horizon { position: absolute; left: 68px; bottom: 74px; width: 64px; height: 3px; background: #D2A875; }
    </style></head><body>
      <div class="brand">${mark}<span>LUMINA</span></div>
      <p class="kicker">${kicker}</p><h1>${headline}</h1><p class="copy">${copy}</p><span class="horizon"></span>
    </body></html>`);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: fileURLToPath(new URL(`lumina-${kind}-v${kind === 'share' ? 3 : 2}.jpg`, output)), type: 'jpeg', quality: 90 });
  }
} finally { await browser.close(); }
