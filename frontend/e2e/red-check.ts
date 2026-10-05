/** Hue-based red and pink check (hue <= 20 or >= 330, saturation > 40%) over color, background and border of everything in main, the player included. */
import type { Page } from '@playwright/test';

export async function redOrPinkInMain(page: Page): Promise<string[]> {
  return page.evaluate(() => {
      const redOrPink = (value: string) => {
        const m = value.match(/rgba?\((\d+), (\d+), (\d+)(?:, ([\d.]+))?\)/);
        if (!m || (m[4] !== undefined && Number(m[4]) === 0)) return false;
        const [r, g, b] = [Number(m[1]) / 255, Number(m[2]) / 255, Number(m[3]) / 255];
        const max = Math.max(r, g, b);
        const delta = max - Math.min(r, g, b);
        if (delta === 0) return false;
        const light = (max + Math.min(r, g, b)) / 2;
        const hue = ((max === r ? (g - b) / delta : max === g ? 2 + (b - r) / delta : 4 + (r - g) / delta) * 60 + 360) % 360;
        return delta / (1 - Math.abs(2 * light - 1)) > 0.4 && (hue <= 20 || hue >= 330);
      };
      return [...document.querySelectorAll('main *')].filter((element) => {
        const style = getComputedStyle(element);
        return [style.color, style.backgroundColor, style.borderTopColor].some(redOrPink);
      }).map((element) => `${element.className}`);
    });
}
