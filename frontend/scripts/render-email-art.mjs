import { readFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

// Reuse Lumina's original vault drawing; message text and actions are live HTML in the email.
const root = new URL('../', import.meta.url);
const art = `data:image/png;base64,${(await readFile(new URL('social/share-art.png', root))).toString('base64')}`;
const output = new URL('public/email/', root);
await mkdir(output, { recursive: true });
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 480 }, deviceScaleFactor: 1 });
  await page.setContent(`<html><body style="margin:0;background:#0f0e0c"><img src="${art}" style="display:block;width:1280px;height:480px;object-fit:cover;object-position:center 48%;transform:scale(2);transform-origin:100% 50%"></body></html>`);
  await page.locator('img').evaluate(image => image.decode());
  await page.screenshot({ path: fileURLToPath(new URL('vault-v1.jpg', output)), type: 'jpeg', quality: 88 });
  for (const name of ['film-archive', 'security-keys']) {
    const source = `data:image/jpeg;base64,${(await readFile(new URL(`public/onboarding/${name}-v1.jpg`, root))).toString('base64')}`;
    await page.setContent(`<html><body style="margin:0;background:#0f0e0c"><img src="${source}" style="display:block;width:1280px;height:480px;object-fit:cover;object-position:center"></body></html>`);
    await page.locator('img').evaluate(image => image.decode());
    await page.screenshot({ path: fileURLToPath(new URL(`${name}-v1.jpg`, output)), type: 'jpeg', quality: 88 });
  }
  await page.setViewportSize({ width: 144, height: 112 });
  await page.setContent(`<html style="background:transparent"><body style="margin:0;background:transparent"><svg xmlns="http://www.w3.org/2000/svg" width="144" height="112" viewBox="8 8 48 37" fill="none"><g stroke="#e5a00d" stroke-width="1.5" stroke-linecap="round"><path d="M11 40H53 M21 40A11 11 0 0 1 43 40 M32 11V21 M18.5 16.5L24.5 23.5 M45.5 16.5L39.5 23.5 M12.5 28L21.5 31 M51.5 28L42.5 31"/></g></svg></body></html>`);
  await page.screenshot({ path: fileURLToPath(new URL('mark-v1.png', output)), omitBackground: true });
} finally { await browser.close(); }
