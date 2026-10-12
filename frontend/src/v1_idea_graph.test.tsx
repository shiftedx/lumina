import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ ApiRequestError: class extends Error { status = 0; }, getLatestIdeaGraph: vi.fn(), requestIdeaGraph: vi.fn(), getIdeaGraph: vi.fn() }));
vi.mock('./api', () => api);

import { IdeaGraphPanel, radialLayout } from './features/watch/IdeaGraphPanel';
import type { IdeaGraph, Summary } from './api';

afterEach(() => { vi.clearAllMocks(); });

const summary = { id: 'sum' } as Summary;
const graph: IdeaGraph = {
  id: 'g', library_item_id: 'film', summary_id: 'sum', transcript_id: 't', transcript_revision: 1, model_id: 'm', state: 'succeeded', dropped: 1, error: null, created_at: '', completed_at: '',
  nodes: [
    { id: 'n0', label: 'Stone bridge', cue_ordinals: [2], start_ms: 2000 },
    { id: 'n1', label: 'Spring floods', cue_ordinals: [3], start_ms: 3000 },
    { id: 'n2', label: 'Terraces', cue_ordinals: [9], start_ms: 9000 },
  ],
  edges: [{ source: 'n1', target: 'n0', label: 'washes away', cue_ordinals: [3], start_ms: 3000 }],
};

describe('IdeaGraphPanel', () => {
  it('test_graph_keyboard_list_equivalence: every idea and connection seeks to its evidence from the list', async () => {
    const browser = userEvent.setup();
    const onSeek = vi.fn();
    api.getLatestIdeaGraph.mockResolvedValue(graph);
    render(<IdeaGraphPanel itemId="film" onSeek={onSeek} summary={summary} />);
    await screen.findByText('Stone bridge');
    expect(screen.getAllByRole('button', { name: /^Play evidence for/ })).toHaveLength(4);
    expect(screen.getByText('Spring floods — washes away → Stone bridge')).toBeTruthy();
    expect(screen.getByText('1 idea or connection omitted: no supporting transcript evidence.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Map ideas' })).toBeNull(); // current, nothing to regenerate

    screen.getByRole('button', { name: 'Play evidence for Spring floods — washes away → Stone bridge at 0:03' }).focus();
    await browser.keyboard('{Enter}');
    expect(onSeek).toHaveBeenCalledWith(3);

    await browser.click(screen.getByRole('button', { name: 'Map' }));
    const map = document.querySelector('.idea-map') as HTMLElement;
    expect(within(map).getAllByRole('button').map((b) => b.textContent)).toEqual(['1 Stone bridge', '2 Spring floods', '3 Terraces']); // numbered dots on phones
    await browser.click(within(map).getByRole('button', { name: '3 Terraces' }));
    expect(within(map).getByRole('button', { name: '1 Stone bridge' }).className).toBe('dim');
    expect(screen.queryByText('Spring floods — washes away → Stone bridge')).toBeNull(); // details follow the selection
  });

  it('offers regeneration only for a stale graph and honest empty state', async () => {
    api.getLatestIdeaGraph.mockResolvedValueOnce({ ...graph, summary_id: 'older' });
    render(<IdeaGraphPanel itemId="film" onSeek={vi.fn()} summary={summary} />);
    expect(await screen.findByRole('button', { name: 'Map the latest summary' })).toBeTruthy();
    cleanup();
    api.getLatestIdeaGraph.mockRejectedValueOnce(new Error('No idea graph yet'));
    render(<IdeaGraphPanel itemId="film" onSeek={vi.fn()} summary={summary} />);
    expect(await screen.findByText(/No idea graph yet/)).toBeTruthy();
  });

  it('test_graph_bounded_render: radial layout is stable and stays inside the box', () => {
    const layout = radialLayout(24);
    expect(radialLayout(24)).toEqual(layout);
    expect(layout.every(({ x, y }) => x >= 12 && x <= 88 && y >= 12 && y <= 88)).toBe(true);
    expect(radialLayout(1)).toEqual([{ x: 50, y: 50 }]);
    // Even 24 ideas keep ~44px between centres on a 322px phone map (13.7% of the box).
    const gaps = layout.flatMap((a, i) => layout.slice(i + 1).map((b) => Math.hypot(a.x - b.x, a.y - b.y)));
    expect(Math.min(...gaps)).toBeGreaterThanOrEqual(13.5);
  });
});
