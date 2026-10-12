import { act, fireEvent, render, screen } from '@testing-library/react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vitest';

import { Artwork, resolveArtworkUrl } from './Artwork';

describe('Artwork boundary', () => {
  it('accepts only Lumina API capabilities and rejects browser hotlinks', () => {
    expect(resolveArtworkUrl('/api/artwork/remote/opaque')).toContain('/api/artwork/remote/opaque');
    expect(resolveArtworkUrl('https://provider.example/thumb.jpg')).toBeNull();

    const { container } = render(<Artwork alt="Film artwork" src="https://provider.example/thumb.jpg" />);
    expect(screen.getByRole('img', { name: 'Film artwork' }).tagName).toBe('SPAN');
    expect(container.querySelector('img')).toBeNull();
  });

  it('uses the requested initials after an artwork failure', () => {
    const { container } = render(<Artwork alt="" fallback="A" src="/api/artwork/remote/avatar" />);
    const image = container.querySelector('img');
    expect(image).not.toBeNull();
    fireEvent.error(image as HTMLImageElement);
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toBe('A');
  });

  it('decodes cached artwork asynchronously without blocking page layout', () => {
    const { container } = render(<Artwork alt="A poster" src="/api/artwork/remote/cached" />);
    expect(container.querySelector('img')?.getAttribute('decoding')).toBe('async');
    expect(container.querySelector('img')?.getAttribute('loading')).toBe('lazy');
  });

  it('retries a transient artwork failure with bounded backoff', () => {
    vi.useFakeTimers();
    try {
      const { container } = render(<Artwork alt="Cached poster" src="/api/artwork/remote/retry" />);
      fireEvent.error(container.querySelector('img') as HTMLImageElement);
      expect(container.querySelector('img')).toBeNull();
      act(() => vi.advanceTimersByTime(250));
      expect(container.querySelector('img')?.getAttribute('src')).toContain('lumina_retry=1');
    } finally {
      vi.useRealTimers();
    }
  });

  it('never retries artwork the server says does not exist (404)', () => {
    vi.useFakeTimers();
    const lookup = vi.spyOn(performance, 'getEntriesByName').mockReturnValue([{ responseStatus: 404 } as unknown as PerformanceEntry]);
    try {
      const { container } = render(<Artwork alt="Missing poster" src="/api/artwork/library/missing" />);
      fireEvent.error(container.querySelector('img') as HTMLImageElement);
      act(() => vi.advanceTimersByTime(2000));
      expect(container.querySelector('img')).toBeNull();
    } finally {
      lookup.mockRestore();
      vi.useRealTimers();
    }
  });

  it('keeps retrying a rate-limited (429) artwork until the 60s budget window has turned over', () => {
    vi.useFakeTimers();
    const lookup = vi.spyOn(performance, 'getEntriesByName').mockReturnValue([{ responseStatus: 429 } as unknown as PerformanceEntry]);
    try {
      const { container } = render(<Artwork alt="Throttled poster" src="/api/artwork/remote/throttled" />);
      let waited = 0;
      for (const [attempt, delay] of [250, 750, 5_000, 15_000, 45_000].entries()) {
        fireEvent.error(container.querySelector('img') as HTMLImageElement);
        act(() => vi.advanceTimersByTime(delay));
        waited += delay;
        expect(container.querySelector('img')?.getAttribute('src')).toContain(`lumina_retry=${attempt + 1}`);
      }
      expect(waited).toBeGreaterThan(60_000);  // the last retry lands after the whole rate window

      // Bounded: one more failure is final, with no timer left behind.
      fireEvent.error(container.querySelector('img') as HTMLImageElement);
      act(() => vi.advanceTimersByTime(120_000));
      expect(container.querySelector('img')).toBeNull();
      expect(container.innerHTML).not.toContain('lumina_retry=6');
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      lookup.mockRestore();
      vi.useRealTimers();
    }
  });

  it('keeps a poster that loaded before effects ran out of the dimmed loading state', async () => {
    const actEnvironment = (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT;
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = false;
    const container = document.createElement('div');
    document.body.append(container);
    const loadedAtCommit = new Promise<void>((resolve) => {
      const observer = new MutationObserver(() => {
        const image = container.querySelector('img');
        if (!image) return;
        observer.disconnect();
        image.dispatchEvent(new Event('load'));  // cached-image load task, ahead of React's passive-effect task
        resolve();
      });
      observer.observe(container, { childList: true, subtree: true });
    });
    const root = createRoot(container);
    try {
      root.render(<Artwork alt="" src="/api/titles/t/images/Primary?tag=x" />);
      await loadedAtCommit;
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(container.querySelector('img')?.getAttribute('data-artwork-loading')).toBeNull();
    } finally {
      root.unmount();
      container.remove();
      (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = actEnvironment;
    }
  });

  it('keeps a poster that errored before effects ran out of the image and shows the placeholder', async () => {
    const actEnvironment = (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT;
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = false;
    const container = document.createElement('div');
    document.body.append(container);
    const erroredAtCommit = new Promise<void>((resolve) => {
      const observer = new MutationObserver(() => {
        const image = container.querySelector('img');
        if (!image) return;
        observer.disconnect();
        image.dispatchEvent(new Event('error'));  // same race as a load, ahead of React's passive-effect task
        resolve();
      });
      observer.observe(container, { childList: true, subtree: true });
    });
    const root = createRoot(container);
    try {
      root.render(<Artwork alt="" src="/api/titles/t/images/Primary?tag=y" />);
      await erroredAtCommit;
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(container.querySelector('img')?.tagName).toBeUndefined();
    } finally {
      root.unmount();
      container.remove();
      (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = actEnvironment;
    }
  });

  it('forgets a failure when the source changes, so returning to it loads and retries again (deferred minor 1)', () => {
    vi.useFakeTimers();
    const lookup = vi.spyOn(performance, 'getEntriesByName').mockReturnValue([{ responseStatus: 404 } as unknown as PerformanceEntry]);
    try {
      const { container, rerender } = render(<Artwork alt="" src="/api/titles/a/images/Primary?tag=a" />);
      fireEvent.error(container.querySelector('img') as HTMLImageElement);
      expect(container.querySelector('img')).toBeNull();
      rerender(<Artwork alt="" src="/api/titles/b/images/Primary?tag=b" />);
      rerender(<Artwork alt="" src="/api/titles/a/images/Primary?tag=a" />);
      expect(container.querySelector('img')?.getAttribute('src')).toContain('/api/titles/a/images/Primary?tag=a');
      lookup.mockReturnValue([{ responseStatus: 503 } as unknown as PerformanceEntry]);
      fireEvent.error(container.querySelector('img') as HTMLImageElement);
      act(() => vi.advanceTimersByTime(250));
      expect(container.querySelector('img')?.getAttribute('src')).toContain('lumina_retry=1');
    } finally {
      lookup.mockRestore();
      vi.useRealTimers();
    }
  });
});
