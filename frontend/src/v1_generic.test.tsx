import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

vi.mock('./features/watch/WatchQueue', async (importOriginal) => ({ ...(await importOriginal<object>()), WatchQueuePanel: () => null }));

import type { WatchSelection } from './features/watch/WatchSurface';
import { watchSurface } from './test/watchSurface';
import { resultFromPreview } from './luminaModel';
import type { MediaSourceCapabilities, PreviewResponse } from './types';

/** Generic public links: truthful per-item actions, site attribution, no provider guess, no auth UI. */

const PAGE = 'https://videos.example.org/watch/42';
const chat = { live: 'unavailable', replay: 'unavailable' } as const;

function preview(capabilities: MediaSourceCapabilities): PreviewResponse {
  return { kind: 'video', title: 'A page with a poster', webpage_url: PAGE, entries: [], raw: { id: '42', extractor: 'generic' }, playback: null, capabilities };
}

function watch(selection: WatchSelection, sourceProblem: string | null = null): string {
  return renderToStaticMarkup(watchSurface({ selection, sourceProblem }));
}

describe('generic public links', () => {
  it('test_generic_metadata_not_play_promise: a resolved title/poster without media has disabled, explained actions', () => {
    const metadataOnly = preview({ provider: 'generic', lifecycle: 'vod', can_play: false, play_reason: 'no_supported_transport', can_acquire: false, acquire_reason: 'no_supported_transport', chat });
    const html = watch({ kind: 'remote', item: resultFromPreview(metadataOnly, { title: 'Loading video…', webpage_url: PAGE }), preview: metadataOnly });
    expect(html).toContain('Lumina cannot prepare playback for this source yet.');
    expect(html).toContain('Lumina cannot prepare acquisition for this source yet.');
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>.*?Save to library/);
    expect(html).toContain(`href="${PAGE}"`);
    // Attributed to its site, never to a guessed provider or an unknown channel.
    expect(html).toContain('videos.example.org');
    expect(html).not.toMatch(/YouTube|Unknown channel/);
  });

  it('labels generic and unknown extractors as Web without inventing a provider', () => {
    for (const provider of ['generic', 'unknown'] as const) {
      const item = resultFromPreview(preview({ provider, lifecycle: 'vod', can_play: true, can_acquire: true, chat }), { title: 'x', webpage_url: PAGE, source: 'youtube' });
      expect([item.source, item.source_label]).toEqual([undefined, 'Web']);
    }
    const kick = resultFromPreview(preview({ provider: 'kick', lifecycle: 'vod', can_play: true, can_acquire: true, chat }));
    expect([kick.source, kick.source_label]).toEqual(['kick', 'Kick']);
  });

  it('test_generic_gated_no_cookie: a gated post is unavailable with its link and no auth UI', () => {
    const problem = 'This source is restricted to signed-in viewers (age, membership, subscription or private). Lumina only plays and saves public media, so open the original link to view it with the provider.';
    const html = watch({ kind: 'remote', item: { title: 'Loading video…', webpage_url: 'https://www.instagram.com/p/C1a2B3c4D5e/' }, preview: null }, problem);
    expect(html).toContain('Unavailable video');
    expect(html).toContain('href="https://www.instagram.com/p/C1a2B3c4D5e/"');
    expect(html).not.toMatch(/cookie|password|username|sign in with|log in/i);
  });
});
