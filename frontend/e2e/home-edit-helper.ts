import { type Page } from '@playwright/test';

export async function openHomeEditor(page: Page, keyboard = false) {
  const profile = page.getByRole('button', { name: /account menu$/ });
  if (keyboard) { await profile.focus(); await page.keyboard.press('Enter'); }
  else await profile.click();
  const edit = page.getByRole('menuitem', { name: 'Edit home', exact: true });
  if (keyboard) { await edit.focus(); await page.keyboard.press('Enter'); }
  else await edit.click();
}
