import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = (path: string) => readFileSync(path, 'utf8');
const rule = (source: string, selector: string) => {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = source.match(new RegExp(`(?:^|})\\s*${escaped}\\s*\\{([^}]*)}`, 'm'));
  expect(match, `missing ${selector}`).not.toBeNull();
  return match?.[1] ?? '';
};
const expectNoVisibleBorder = (source: string, selector: string) => {
  const border = rule(source, selector).match(/(?:^|;)\s*border:\s*([^;]+)/)?.[1].trim();
  expect(border === undefined || border === '0' || border === 'none').toBe(true);
};

describe('feature surfaces avoid resting decorative outlines', () => {
  it('keeps settings and administration groups open', () => {
    const settings = css('src/features/settings/settings.css');
    const activity = css('src/features/admin/activity.css');

    expectNoVisibleBorder(settings, ':is(.g-settings, .g-invite-form) :is(.g-input, .g-select select)');
    expectNoVisibleBorder(settings, '.g-settings-spaces');
    expectNoVisibleBorder(settings, '.g-settings-rows');
    expectNoVisibleBorder(settings, '.g-settings-local-advanced');
    expectNoVisibleBorder(settings, '.settings-card');
    expectNoVisibleBorder(activity, '.activity-badge');
  });

  it('keeps watch cards, notices, controls, and media frames open', () => {
    const player = css('src/features/watch/player.css');
    const tools = css('src/features/watch/tools.css');
    const watch = css('src/features/watch/watch.css');

    expectNoVisibleBorder(player, '.player-frame');
    expectNoVisibleBorder(player, '.player-card');
    expectNoVisibleBorder(player, '.player-panel');
    expectNoVisibleBorder(tools, '.chat-rail');
    expectNoVisibleBorder(tools, '.summary-card');
    expectNoVisibleBorder(tools, '.watch-queue-thumb');
    expectNoVisibleBorder(watch, '.description-card');
  });

  it('keeps selection, collection, and curation surfaces open', () => {
    const auth = css('src/features/auth/auth.css');
    const onboarding = css('src/features/onboarding/onboarding.css');
    const requests = css('src/features/requests/requests.css');
    const collections = css('src/features/collections/collections.css');
    const palette = css('src/features/palette/palette.css');
    const curation = css('src/libraryCuration.css');

    expectNoVisibleBorder(auth, '.g-picker-tile');
    expectNoVisibleBorder(onboarding, '.g-onboarding-channel');
    expectNoVisibleBorder(requests, '.rq-language-option');
    expect(rule(requests, '.rq-frame')).not.toMatch(/box-shadow:\s*inset\s+0\s+0\s+0/);
    expectNoVisibleBorder(collections, '.collection-row');
    expectNoVisibleBorder(collections, '.inline-workspace-error');
    expectNoVisibleBorder(palette, '.g-palette-art');
    expectNoVisibleBorder(curation, '.library-curation-controls');
  });

  it('keeps acquisition popovers open while their shadow separates layers', () => {
    const downloads = css('src/features/downloads/downloads.css');

    expectNoVisibleBorder(downloads, '.download-menu');
    expect(rule(downloads, '.download-menu')).toMatch(/box-shadow:\s*var\(--g-elev-2\)/);
  });
});
