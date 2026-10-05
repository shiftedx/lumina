import AxeBuilder from '@axe-core/playwright';
import type { Locator, Page } from '@playwright/test';

const MAX_TEXT = 240;
const SECRET_ASSIGNMENT = /\b(password|passwd|token|secret|credential|cookie|authorization|api[-_]?key)(?:\s*[:=]\s*|\s+)[^\s,;]+/gi;
const COOKIE_ASSIGNMENT = /\b[A-Za-z0-9_-]*(?:cookie|session|access)[A-Za-z0-9_-]*\s*=\s*[^\s,;]+/gi;
const AUTHORIZATION_VALUE = /\bAuthorization(?:\s*[:=]\s*|\s+)[^\s,;]+(?:\s+[^\s,;]+)?/gi;
const URL_PATTERN = /\bhttps?:\/\/[^\s"'<>]+/gi;
const LONG_SECRET_LIKE_VALUE = /\b[A-Za-z0-9_-]{24,}\b/g;

function redactEvidenceText(value: string): string {
  return value
    .replace(URL_PATTERN, '[url-redacted]')
    .replace(AUTHORIZATION_VALUE, 'Authorization=[redacted]')
    .replace(COOKIE_ASSIGNMENT, '[cookie-redacted]')
    .replace(SECRET_ASSIGNMENT, '$1=[redacted]')
    .replace(LONG_SECRET_LIKE_VALUE, '[value-redacted]')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, MAX_TEXT);
}

export async function assertMediaPlaybackAdvances(
  media: Locator,
  options: { timeoutMs?: number; minimumAdvanceSeconds?: number } = {},
): Promise<{ readyState: number; advancedSeconds: number }> {
  const timeoutMs = options.timeoutMs ?? 10_000;
  const minimumAdvance = options.minimumAdvanceSeconds ?? 0.25;
  await media.waitFor({ state: 'visible', timeout: timeoutMs });
  const startedAt = Date.now();
  let state = await media.evaluate((element) => {
    const player = element as HTMLMediaElement;
    return { currentTime: player.currentTime, readyState: player.readyState, errorCode: player.error?.code || null };
  });
  const initialTime = state.currentTime;

  try {
    await media.evaluate(async (element, playTimeoutMs) => {
      const player = element as HTMLMediaElement;
      player.muted = true;
      await Promise.race([
        player.play(),
        new Promise((_, reject) => setTimeout(() => reject(new Error('playback start timed out')), playTimeoutMs)),
      ]);
    }, timeoutMs);
  } catch (error) {
    throw new Error(`Media did not become ready for playback: ${redactEvidenceText(error instanceof Error ? error.message : String(error))}`);
  }

  while (Date.now() - startedAt < timeoutMs) {
    state = await media.evaluate((element) => {
      const player = element as HTMLMediaElement;
      return { currentTime: player.currentTime, readyState: player.readyState, errorCode: player.error?.code || null };
    });
    if (state.errorCode) throw new Error(`Media playback failed with code ${state.errorCode}.`);
    if (state.readyState >= 2 && state.currentTime - initialTime >= minimumAdvance) {
      return { readyState: state.readyState, advancedSeconds: state.currentTime - initialTime };
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }

  if (state.readyState < 2) throw new Error(`Media did not become ready within ${timeoutMs}ms.`);
  throw new Error(`Media did not advance by ${minimumAdvance}s within ${timeoutMs}ms.`);
}

export async function assertFocusIndicator(locator: Locator): Promise<void> {
  const readIndicator = (element: Element) => {
    const style = getComputedStyle(element);
    const outlineWidth = Number.parseFloat(style.outlineWidth || '0');
    return {
      outline: `${style.outlineStyle}:${outlineWidth}:${style.outlineColor}`,
      shadow: style.boxShadow,
      border: `${style.borderStyle}:${style.borderWidth}:${style.borderColor}`,
      background: style.backgroundColor,
    };
  };
  const focused = await locator.evaluate(readIndicator);
  const unfocused = await locator.evaluate((element) => {
    (element as HTMLElement).blur();
    const style = getComputedStyle(element);
    const outlineWidth = Number.parseFloat(style.outlineWidth || '0');
    return {
      outline: `${style.outlineStyle}:${outlineWidth}:${style.outlineColor}`,
      shadow: style.boxShadow,
      border: `${style.borderStyle}:${style.borderWidth}:${style.borderColor}`,
      background: style.backgroundColor,
    };
  });
  await locator.focus();
  const visibleDifference = focused.outline !== unfocused.outline
    || focused.shadow !== unfocused.shadow
    || focused.border !== unfocused.border
    || focused.background !== unfocused.background;
  if (!visibleDifference) throw new Error('Focused control has no visible focus indicator distinct from its unfocused state.');
}

const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22a', 'wcag22aa'];
const BLOCKING_IMPACTS = new Set(['critical', 'serious', 'moderate']);

export async function auditA11y(page: Page) {
  const result = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
  return {
    violations: result.violations
      .filter((violation) => BLOCKING_IMPACTS.has(violation.impact || ''))
      .map((violation) => ({
        id: violation.id,
        impact: violation.impact,
        nodeCount: violation.nodes.length,
        tags: violation.tags.filter((tag) => tag.startsWith('wcag')),
      })),
  };
}
