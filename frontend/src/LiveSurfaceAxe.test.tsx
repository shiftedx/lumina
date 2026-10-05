import axe from 'axe-core';
import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { LiveSurface } from './features/live/LiveSurface';
import { endedEntry, liveEntry, liveSnapshot, upcomingEntry } from './test/remoteFixtures';

describe('LiveSurface accessibility', () => {
  it('has no ARIA, name, list or heading-order violations', async () => {
    const snapshot = liveSnapshot({ hero: [liveEntry('f1')], items: [...liveSnapshot().items, upcomingEntry('u1', null), endedEntry('e1')] });
    const { container } = render(
      <LiveSurface error={null} isQueueing={() => false} library={[]} onOpen={vi.fn()} onQueue={vi.fn()} snapshot={snapshot} />,
    );
    const results = await axe.run(container, { runOnly: ['aria-prohibited-attr', 'aria-allowed-attr', 'aria-required-attr', 'button-name', 'link-name', 'list', 'listitem', 'heading-order'] });
    expect(results.violations).toEqual([]);
  });
});
