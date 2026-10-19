import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

const ROOT = process.env.AUDIT_READERS_ROOT!;
const OWNER = { username: 'auditowner', displayName: 'Audit Owner', password: 'Reader3!Synthesis' };
const OWNER_STATE = path.join(ROOT, 'owner.json');
const MEDIA_ROOT = path.join(ROOT, 'media');

test('owner, synthetic music, and reader fixtures are ready', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Username').fill(OWNER.username);
  await page.getByLabel('Display name').fill(OWNER.displayName);
  await page.getByLabel('Password', { exact: true }).fill(OWNER.password);
  await page.getByRole('button', { name: 'Create household vault' }).click();
  await page.getByLabel('Mounted path').fill(MEDIA_ROOT);
  await page.getByLabel('Folder name').fill('Audit media');
  await page.getByRole('button', { name: 'Add and scan' }).click();
  await expect(page.getByRole('button', { name: 'Continue', exact: true })).toBeEnabled({ timeout: 60_000 });
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await page.getByRole('button', { name: 'Explore without setup' }).click();
  await expect(page.getByRole('main', { name: 'Main content' })).toBeVisible();

  await page.goto('/books');
  const files = ['audit-epub.epub', 'audit-pdf.pdf', 'audit-cbz.cbz', 'audit-cbr.cbr'].map((name) => ({
    name,
    mimeType: name.endsWith('.epub') ? 'application/epub+zip' : name.endsWith('.pdf') ? 'application/pdf' : name.endsWith('.cbr') ? 'application/vnd.comicbook-rar' : 'application/vnd.comicbook+zip',
    buffer: readFileSync(path.join(ROOT, 'books-fixtures', name)),
  }));
  await page.getByLabel('Choose EPUB, PDF, CBZ, and CBR files').setInputFiles(files);
  const importer = page.getByRole('dialog', { name: 'Import into Books' });
  await expect(importer).toContainText('audit-epub.epub');
  await importer.getByRole('button', { name: 'Import 4 files' }).click();
  await expect(importer.getByText('Imported')).toHaveCount(4, { timeout: 60_000 });
  await importer.getByRole('button', { name: 'Done' }).click();
  await page.context().storageState({ path: OWNER_STATE });
});
