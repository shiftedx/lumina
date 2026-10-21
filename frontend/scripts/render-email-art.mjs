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
  // Email marks are reproduced with apple/scripts/generate_brand_assets.swift.
} finally { await browser.close(); }
