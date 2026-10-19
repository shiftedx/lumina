/** Cross-browser reader qualification with real generated book files. */
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { firefox, request as requestFactory, webkit, type APIRequestContext, type Browser, type Page, type TestInfo } from '@playwright/test';

import { OWNER_STATE, expect, test } from './realstack';

type Format = 'epub' | 'pdf' | 'cbz' | 'cbr';

const formats: Format[] = ['epub', 'pdf', 'cbz', 'cbr'];
const units = 8;
const engines = [
  { name: 'firefox', browserType: firefox },
  { name: 'webkit', browserType: webkit },
] as const;

let fixtureRoot = '';
let importedBooks: Record<Format, string>;

async function mutationHeaders(request: APIRequestContext, origin: string) {
  const session = await request.get('/api/session/me');
  const body = await session.text();
  expect(session.ok(), `${session.status()} ${body}`).toBe(true);
  const state = JSON.parse(body) as { csrf_token: string };
  return { Origin: origin, 'X-CSRF-Token': state.csrf_token };
}

async function writeEvidence(info: TestInfo, name: string, value: object) {
  const target = info.outputPath(name);
  writeFileSync(target, `${JSON.stringify(value, null, 2)}\n`);
  await info.attach(name, { path: target, contentType: 'application/json' });
}

async function contentReady(page: Page, format: Format, index: number) {
  await expect(page.getByLabel('Reading position')).toHaveValue(String(index));
  if (format === 'epub') {
    await expect(page.locator('.books-prose')).toContainText(`Chapter ${index + 1}`);
    return;
  }

  const image = page.getByAltText(`Page ${index + 1} of ${units}`);
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((node: HTMLImageElement) => node.complete && node.naturalWidth > 0), { timeout: 15_000 }).toBe(true);
  expect(await image.getAttribute('src')).toMatch(/^blob:/);
  expect(await page.locator('.books-reader-page img').count()).toBeLessThanOrEqual(2);
  const visibleImageState = () => page.locator('.books-reader-page img').evaluateAll((nodes) => nodes.filter((node) => {
    const style = getComputedStyle(node);
    return style.display !== 'none' && style.visibility !== 'hidden' && node.getClientRects().length > 0;
  }).map((node) => ({ complete: (node as HTMLImageElement).complete, width: (node as HTMLImageElement).naturalWidth })));
  await expect.poll(async () => {
    const visible = await visibleImageState();
    return visible.length > 0 && visible.every(({ complete, width }) => complete && width > 0);
  }, { timeout: 15_000 }).toBe(true);
  const visibleImages = await visibleImageState();
  expect(visibleImages.length).toBeGreaterThan(0);
  expect(visibleImages.length).toBeLessThanOrEqual(2);
  expect(visibleImages.every(({ complete, width }) => complete && width > 0)).toBe(true);
}

async function jumpToUnitFour(page: Page, format: Format) {
  const contents = page.getByRole('button', { name: format === 'epub' ? 'Chapters' : 'Pages', exact: true });
  if (await contents.getAttribute('aria-expanded') === 'false') await contents.click();
  if (format === 'epub') {
    await page.getByLabel('Table of contents').locator('ol button').nth(4).click();
  } else {
    await page.getByLabel('Go to page', { exact: true }).fill('5');
    await page.getByRole('button', { name: 'Go to page', exact: true }).click();
  }
  await contentReady(page, format, 4);
}

async function selectSpread(page: Page) {
  await page.getByRole('button', { name: 'Reading appearance', exact: true }).click();
  const spread = page.getByRole('button', { name: 'Spread', exact: true });
  await spread.click();
  await expect(spread).toHaveAttribute('aria-pressed', 'true');
}

test.describe('real books reader in installed Firefox and WebKit', () => {
  test.beforeAll(async ({ baseURL }) => {
    const setupRequest = await requestFactory.newContext({ baseURL, storageState: OWNER_STATE });
    try {
      const headers = await mutationHeaders(setupRequest, baseURL!);
      fixtureRoot = mkdtempSync(path.join(tmpdir(), 'lumina-books-browsers-'));
      execFileSync('../backend/.venv/bin/python', ['../scripts/perf/audit_books.py', '--fixtures-only', fixtureRoot, '--pages', String(units)], { timeout: 60_000 });
      importedBooks = {} as Record<Format, string>;
      for (const format of formats) {
        const imported = await setupRequest.put(`/api/books/import?filename=browser-audit.${format}`, {
          data: readFileSync(path.join(fixtureRoot, `audit.${format}`)),
          headers: { 'Content-Type': 'application/octet-stream', ...headers },
        });
        const body = await imported.text();
        expect(imported.status(), body).toBe(201);
        importedBooks[format] = (JSON.parse(body) as { id: string }).id;
      }
    } finally {
      await setupRequest.dispose();
    }
  });

  test.afterAll(() => {
    if (fixtureRoot) rmSync(fixtureRoot, { recursive: true, force: true });
  });

  for (const engine of engines) {
    test(`${engine.name} opens, turns, jumps, resumes and lays out every book format`, async ({ request, baseURL }, info) => {
      test.setTimeout(180_000);
      const headers = await mutationHeaders(request, baseURL!);
      const observations: object[] = [];
      let browser: Browser | undefined;
      let version: string | null = null;
      let installed = true;
      try {
        try {
          // The journeys project supplies Chromium audio flags; explicit engines use their own defaults.
          browser = await engine.browserType.launch({ args: [], timeout: 30_000 });
          version = browser.version();
        } catch (error) {
          if (error instanceof Error && /Executable doesn't exist/.test(error.message)) {
            installed = false;
            test.skip(true, `BLOCKED: Playwright ${engine.name} executable is not installed`);
          }
          throw error;
        }

        for (const format of formats) {
          const bookId = importedBooks[format];
          const reset = await request.patch(`/api/books/${bookId}/state`, {
            data: { unit_index: 0, offset: 0, completed: false },
            headers,
          });
          expect(reset.ok(), `${reset.status()} ${await reset.text()}`).toBe(true);

          const context = await browser.newContext({ baseURL, storageState: OWNER_STATE, viewport: { width: 1440, height: 900 } });
          const errors: string[] = [];
          const serverErrors: string[] = [];
          try {
            const reader = await context.newPage();
            reader.on('pageerror', (error) => errors.push(error.message));
            reader.on('response', (response) => {
              if (response.status() >= 500) serverErrors.push(`${response.status()} ${response.request().method()} ${new URL(response.url()).pathname}`);
            });

            const startupAt = performance.now();
            await reader.goto(`/books/read/${bookId}`, { waitUntil: 'domcontentloaded' });
            await contentReady(reader, format, 0);
            const startupMs = performance.now() - startupAt;

            const turnAt = performance.now();
            await reader.getByRole('button', { name: 'Next', exact: true }).click();
            await contentReady(reader, format, 1);
            const turnMs = performance.now() - turnAt;

            await jumpToUnitFour(reader, format);
            if (format !== 'epub') {
              await selectSpread(reader);
              await contentReady(reader, format, 4);
            }

            await expect.poll(async () => {
              const response = await reader.request.get(`/api/books/${bookId}/reader`);
              if (!response.ok()) return -1;
              const body = await response.json() as { book: { progress: { unit_index: number } | null } };
              return body.book.progress?.unit_index ?? -1;
            }, { timeout: 15_000 }).toBe(4);

            const resumeAt = performance.now();
            await reader.reload({ waitUntil: 'domcontentloaded' });
            await contentReady(reader, format, 4);
            const resumeMs = performance.now() - resumeAt;

            expect(errors, 'uncaught reader page errors').toEqual([]);
            expect(serverErrors, 'reader server 5xx responses').toEqual([]);
            observations.push({ format, units, startup_ms: startupMs, first_turn_ms: turnMs, resume_ms: resumeMs, resumed_unit: 4, native_layout: format === 'epub' ? null : 'spread' });
          } finally {
            await context.close();
          }
        }
      } finally {
        await browser?.close();
        await writeEvidence(info, `books-${engine.name}.json`, {
          browser: engine.name,
          browser_version: version,
          installed,
          network: 'unshaped loopback',
          timing: 'raw observations; no host-dependent pass threshold',
          observations,
        });
      }
    });
  }
});
