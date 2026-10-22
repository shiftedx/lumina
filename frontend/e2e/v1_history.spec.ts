import { navigateApp } from './lumina-mock';
import { expect, test, type Page } from '@playwright/test';
import { mockApi, signIn, user } from './lumina-mock';

/** Server-owned search history on the real app shell (mocked API). */

async function stubSearchResults(page: Page) {
  // The history feature only cares that a search completes; empty result sets
  // keep the Explore surface simple to assert against.
  await page.route('**/api/youtube-search', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) }));
  await page.route('**/api/source-search', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [], errors: [] }) }));
  await page.route('**/api/search?*', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [], matches: [], mode: 'lexical' }) }));
}

const searchBar = (page: Page) => page.getByRole('combobox');
const openPalette = (page: Page) => page.getByRole('button', { name: /^Search Lumina, / }).click();
// The "Recent searches" group only renders when it has at least one entry
// (the palette's recents group), so its presence/absence is the signal.
const recentSearches = (page: Page) => page.getByRole('group', { name: 'Recent searches' });

async function search(page: Page, query: string) {
  await openPalette(page);
  await searchBar(page).fill(query);
  await page.keyboard.press('ControlOrMeta+Enter'); // search everything in Explore
}

async function reopenSearchEmpty(page: Page) {
  await openPalette(page);
}

test('test_remove_one_and_clear_all: single-entry removal and Settings clear both persist server-side', async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await stubSearchResults(page);
  await page.goto('/');
  await signIn(page);

  await search(page, 'quiet music');
  await expect(page.getByRole('heading', { name: 'Results for “quiet music”' })).toBeVisible();

  await reopenSearchEmpty(page);
  await expect(recentSearches(page).getByText('quiet music')).toBeVisible();
  // The UI removes the entry optimistically; wait for the background DELETE to
  // actually land before reloading, or the reload can abort the in-flight
  // request and make this assertion flaky rather than testing persistence.
  const removed = page.waitForResponse((response) => response.url().includes('/api/search/history/') && response.request().method() === 'DELETE');
  await recentSearches(page).getByRole('button', { name: 'Remove “quiet music” from recent searches' }).click();
  await removed;
  await expect(recentSearches(page)).toHaveCount(0);
  await page.keyboard.press('Escape');

  // Leave the deep-linked "/streaming/search?q=quiet+music" URL first: reloading on it
  // would replay that search (route restoration) and legitimately re-add
  // it, which would test the route restoration, not delete persistence.
  await navigateApp(page, 'Home');
  // A reload re-fetches from the server: the removal was persisted, not just client-side.
  await page.reload();
  await reopenSearchEmpty(page);
  await expect(recentSearches(page)).toHaveCount(0);
  await page.keyboard.press('Escape');

  // Settings offers a bulk clear too.
  await search(page, 'another search');
  await page.getByRole('button', { name: /, account menu$/ }).click();
  await page.getByRole('menuitem', { name: 'My Settings' }).click();
  await page.getByRole('navigation', { name: 'Settings sections' }).getByRole('link', { name: 'Privacy & apps' }).click();
  await expect(page.getByRole('heading', { name: 'Search history' })).toBeVisible();
  await page.getByRole('button', { name: 'Clear search history' }).click();
  await expect(page.getByText('No search history yet.')).toBeVisible();
  await reopenSearchEmpty(page);
  await expect(recentSearches(page)).toHaveCount(0);
  await page.keyboard.press('Escape');
});
