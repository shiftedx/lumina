import { readFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

// Static, original artwork only. These previews never depend on a household's media or members.
const root = new URL('../', import.meta.url);
const data = async (path, mime) => `data:${mime};base64,${(await readFile(new URL(path, root))).toString('base64')}`;
const art = await data('social/share-art.png', 'image/png');
const roman = await data('public/fonts/Newsreader-variable.woff2', 'font/woff2');
const italic = await data('public/fonts/Newsreader-Italic-variable.woff2', 'font/woff2');
const output = new URL('public/social/', root);
await mkdir(output, { recursive: true });
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
  for (const [kind, kicker, headline, copy] of [
    ['share', 'THE HOUSEHOLD MEDIA VAULT', 'Your media.<br>Your vault.', 'Movies, music, and favorites.<br>One household. One media vault.'],
    ['invite', 'YOUR LUMINA INVITATION', 'Step inside<br>the vault.', 'You’re invited to explore<br>a Lumina media vault.'],
  ]) {
    await page.setContent(`<!doctype html><html><head><meta charset="utf-8"><style>
      @font-face { font-family: Newsreader; src: url('${roman}'); font-style: normal; font-weight: 100 900; }
      @font-face { font-family: Newsreader; src: url('${italic}'); font-style: italic; font-weight: 100 900; }
      * { box-sizing: border-box; } html, body { width: 1200px; height: 630px; margin: 0; overflow: hidden; }
      body { background: #0f0e0c url('${art}') center / cover no-repeat; color: #f4f1ea; }
      .brand { position: absolute; left: 64px; top: 59px; display: flex; align-items: center; gap: 17px; }
      .brand svg { width: 59px; height: 43px; color: #edb71b; }
      .brand span { padding-top: 6px; font: 500 34px / 1 Newsreader, serif; letter-spacing: 4px; }
      .kicker { position: absolute; left: 68px; top: 180px; margin: 0; color: #edb71b; font: 600 13px / 1.4 Arial, sans-serif; letter-spacing: 2.3px; }
      h1 { position: absolute; left: 64px; top: 217px; width: 455px; margin: 0; font: italic 700 72px / 1.02 Newsreader, serif; letter-spacing: -1.5px; }
      .copy { position: absolute; left: 68px; top: 430px; margin: 0; color: #ded7c9; font: 400 22px / 1.55 Arial, sans-serif; }
      .horizon { position: absolute; left: 68px; bottom: 74px; width: 64px; height: 3px; background: #edb71b; }
    </style></head><body>
      <div class="brand"><svg viewBox="8 10 48 33" fill="none" aria-hidden="true"><g stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M11 40H53 M21 40A11 11 0 0 1 43 40 M32 11V21 M18.5 16.5L24.5 23.5 M45.5 16.5L39.5 23.5 M12.5 28L21.5 31 M51.5 28L42.5 31"/></g></svg><span>LUMINA</span></div>
      <p class="kicker">${kicker}</p><h1>${headline}</h1><p class="copy">${copy}</p><span class="horizon"></span>
    </body></html>`);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: fileURLToPath(new URL(`lumina-${kind}-v1.jpg`, output)), type: 'jpeg', quality: 90 });
  }
} finally { await browser.close(); }
