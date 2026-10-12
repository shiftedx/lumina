import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { episodeSummary, keyScenes, titleDetail } from '../../test/galleryFixtures';

const api = vi.hoisted(() => ({ getKeyScenes: vi.fn(), getTitle: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));
const { KeyScenes } = await import('./KeyScenes');
const { forgetTitle } = await import('./titleCache');

afterEach(() => { vi.resetAllMocks(); forgetTitle('ep-2-3'); });

describe('KeyScenes', () => {
  it('quotes a finished movie and plays from the quote’s second', async () => {
    api.getKeyScenes.mockResolvedValue({ ...keyScenes, scenes: [...keyScenes.scenes, { start_ms: 3_725_999, quote: 'The lamp is out.', caption: 'Night falls.' }] });
    const onPlay = vi.fn();
    render(<KeyScenes onPlay={onPlay} title={{ id: 'movie-1', type: 'movie' }} />);
    expect(await screen.findByRole('heading', { level: 2, name: 'Key scenes' })).toBeTruthy();
    expect(screen.getByText('“Carry it until the ice sings.”')).toBeTruthy();
    expect(screen.getByText('The grandfather explains the lamp.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Play from 12:41' }));
    expect(onPlay).toHaveBeenCalledWith('item-movie-1', 761);
    fireEvent.click(screen.getByRole('button', { name: 'Play from 1:02:05' }));
    expect(onPlay).toHaveBeenLastCalledWith('item-movie-1', 3725);
    expect(api.getKeyScenes).toHaveBeenCalledWith('movie-1', { timeoutMs: 8000 });
    expect(api.getTitle).not.toHaveBeenCalled();
  });

  it('names the episode the scenes come from on a show', async () => {
    api.getKeyScenes.mockResolvedValue({ ...keyScenes, title_id: 'ep-2-3', item_id: 'item-ep-2-3' });
    api.getTitle.mockResolvedValue(titleDetail(episodeSummary(2, 3)));
    render(<KeyScenes onPlay={vi.fn()} title={{ id: 'series-1', type: 'series' }} />);
    expect(await screen.findByRole('heading', { level: 2, name: 'Key scenes from S2 · E3' })).toBeTruthy();
    expect(api.getTitle).toHaveBeenCalledWith('ep-2-3');
  });

  it('still shows a show’s scenes when the episode cannot be named', async () => {
    api.getKeyScenes.mockResolvedValue({ ...keyScenes, title_id: 'ep-2-3' });
    api.getTitle.mockRejectedValue(new Error('boom'));
    render(<KeyScenes onPlay={vi.fn()} title={{ id: 'series-1', type: 'series' }} />);
    expect(await screen.findByRole('heading', { level: 2, name: 'Key scenes' })).toBeTruthy();
  });

  it.each([
    ['it is switched off or nothing is finished', () => api.getKeyScenes.mockResolvedValue({ available: false, scenes: [] })],
    ['there are no scenes', () => api.getKeyScenes.mockResolvedValue({ ...keyScenes, scenes: [] })],
    ['the request fails or times out', () => api.getKeyScenes.mockRejectedValue(new Error('timeout'))],
  ])('renders nothing when %s', async (_case, arrange) => {
    arrange();
    const { container } = render(<KeyScenes onPlay={vi.fn()} title={{ id: 'movie-1', type: 'movie' }} />);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(container.innerHTML).toBe('');
  });

  it('renders quotes and captions as text, never as markup', async () => {
    api.getKeyScenes.mockResolvedValue({ ...keyScenes, scenes: [{ start_ms: 1000, quote: '<b>Run</b>', caption: '<img src=x onerror=alert(1)>' }] });
    const { container } = render(<KeyScenes onPlay={vi.fn()} title={{ id: 'movie-1', type: 'movie' }} />);
    expect(await screen.findByText('“<b>Run</b>”')).toBeTruthy();
    expect(container.querySelector('b, img')).toBeNull();
  });
});
