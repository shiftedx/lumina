import type { Locator, Page } from '@playwright/test';

/** Open a Lumina select and choose its visible option. */
export async function chooseSelect(page: Page, control: Locator, optionName: string | RegExp): Promise<void> {
  await control.click();
  await page.getByRole('listbox').getByRole('option', { name: optionName }).click();
}
