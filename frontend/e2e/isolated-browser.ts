import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { BrowserType, LaunchOptions } from '@playwright/test';

/** Firefox's -profile leaves shared app data behind; macOS 27 protects that directory.
 * Give each launch its own home until the pinned browser includes Firefox 158.
 * https://github.com/microsoft/playwright/issues/42768
 */
export async function launchIsolatedBrowser(type: BrowserType, options: LaunchOptions = {}) {
  const home = process.platform === 'darwin' && type.name() === 'firefox'
    ? mkdtempSync(path.join(tmpdir(), 'lumina-firefox-home-')) : undefined;
  const cleanup = () => {
    if (!home) return;
    rmSync(home, { recursive: true, force: true });
  };
  try {
    // Precreate these so macOS does not add deny-delete ACLs to its standard directories.
    if (home) for (const name of ['Library/Application Support', 'Library/Caches', 'Downloads']) {
      mkdirSync(path.join(home, name), { recursive: true });
    }
    const browser = await type.launch({ ...options, ...(home ? {
      env: { ...process.env, ...options.env, CFFIXED_USER_HOME: home },
    } : {}) });
    return { browser, close: async () => {
      try { await browser.close(); } finally { cleanup(); }
    } };
  } catch (error) {
    cleanup();
    throw error;
  }
}
