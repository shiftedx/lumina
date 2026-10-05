import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

vi.mock('./features/watch/WatchQueue', async (importOriginal) => ({ ...(await importOriginal<object>()), WatchQueuePanel: () => null }));

import type { WatchSelection } from './features/watch/WatchSurface';
import { watchSurface } from './test/watchSurface';
import { capabilityActionMessage } from './sourceCapabilities';

/** A restricted public source keeps its link, explains itself, and never asks for a sign-in. */

const SOURCE = 'https://www.youtube.com/watch?v=lumina00001';

function watch(selection: WatchSelection, sourceProblem: string | null = null): string {
  return renderToStaticMarkup(watchSurface({ selection, sourceProblem }));
}

function expectHonestUnavailable(html: string, reason: string) {
  expect(html).toContain(reason);
  expect(html).toContain(`href="${SOURCE}"`);
  expect(html).toContain('Open original');
  expect(html).toMatch(/<button[^>]*disabled=""[^>]*>.*?Save to library/);
  expect(html).not.toMatch(/cookie|sign in with|log in with|--cookies/i);
  expect(html).not.toContain('You can still download');
}

describe('test_restricted_source_honest', () => {
  it('shows the classified failure, the original link, and no cookie or download promise when inspection fails', () => {
    const problem = 'This source is restricted to signed-in viewers (age, membership, subscription or private). Lumina only plays and saves public media, so open the original link to view it with the provider.';
    const html = watch({ kind: 'remote', item: { id: 'lumina00001', title: 'Age-restricted', webpage_url: SOURCE }, preview: null }, problem);
    expectHonestUnavailable(html, 'restricted to signed-in viewers');
  });

  it('renders a sign-in-gated capability as unavailable with the original link', () => {
    const html = watch({
      kind: 'remote',
      item: { id: 'lumina00001', title: 'Members only', webpage_url: SOURCE },
      preview: {
        kind: 'video', title: 'Members only', webpage_url: SOURCE, entries: [], raw: {}, playback: null,
        capabilities: { provider: 'youtube', lifecycle: 'vod', can_play: false, play_reason: 'sign_in_required', can_acquire: false, acquire_reason: 'sign_in_required', chat: { live: 'unavailable', replay: 'unavailable' } },
      },
    });
    expectHonestUnavailable(html, capabilityActionMessage('sign_in_required', 'play') as string);
  });

  it('keeps a playable public source free of the unavailable link', () => {
    const html = watch({
      kind: 'remote',
      item: { id: 'lumina00001', title: 'Public', webpage_url: SOURCE },
      preview: {
        kind: 'video', title: 'Public', webpage_url: SOURCE, entries: [], raw: {}, playback: null,
        capabilities: { provider: 'youtube', lifecycle: 'vod', can_play: true, can_acquire: true, chat: { live: 'unavailable', replay: 'unavailable' } },
      },
    });
    expect(html).not.toContain('Open original');
  });
});
