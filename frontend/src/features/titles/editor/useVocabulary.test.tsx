import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getMetadataVocabulary: vi.fn(), getSessionUserId: vi.fn() }));
vi.mock('../../../api', () => api);
const { clearVocabularyCache, useVocabulary } = await import('./useVocabulary');

afterEach(() => { vi.resetAllMocks(); clearVocabularyCache(); });

describe('useVocabulary', () => {
  it('never serves one member the previous member\'s suggestions', async () => {
    api.getSessionUserId.mockReturnValue('u1');
    api.getMetadataVocabulary.mockResolvedValue([{ value: 'Secret genre', count: 1 }]);
    const first = renderHook(() => useVocabulary('genres'));
    await waitFor(() => expect(first.result.current).toEqual(['Secret genre']));
    first.unmount();

    api.getSessionUserId.mockReturnValue('u2');
    api.getMetadataVocabulary.mockResolvedValue([{ value: 'Mine', count: 1 }]);
    const second = renderHook(() => useVocabulary('genres'));
    expect(second.result.current).toEqual([]);
    await waitFor(() => expect(second.result.current).toEqual(['Mine']));
    expect(api.getMetadataVocabulary).toHaveBeenCalledTimes(2);
  });

  it('reuses the cache for the same member', async () => {
    api.getSessionUserId.mockReturnValue('u1');
    api.getMetadataVocabulary.mockResolvedValue([{ value: 'A', count: 1 }]);
    const first = renderHook(() => useVocabulary('tags'));
    await waitFor(() => expect(first.result.current).toEqual(['A']));
    renderHook(() => useVocabulary('tags'));
    expect(api.getMetadataVocabulary).toHaveBeenCalledTimes(1);
  });
});
