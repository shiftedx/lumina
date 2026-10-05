import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { RemotePlaybackCacheSettings } from './features/settings/youCards';

describe('RemotePlaybackCacheSettings', () => {
  it('autosaves bounded recent-video and storage presets with clear off-state copy', async () => {
    const browser = userEvent.setup();
    const onChange = vi.fn().mockResolvedValue(undefined);
    render(<RemotePlaybackCacheSettings onChange={onChange} value={{ enabled: true, recent_video_limit: 5, storage_limit_mb: 2048 }} />);

    expect(screen.getByText(/watch progress stays saved when this cache is off/i)).toBeTruthy();
    expect((screen.getByRole('radio', { name: '5' }) as HTMLInputElement).checked).toBe(true);
    await browser.click(screen.getByRole('radio', { name: '10' }));
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith({ enabled: true, recent_video_limit: 10, storage_limit_mb: 2048 }));
    await waitFor(() => expect(screen.getByText('Cache preference saved.')).toBeTruthy());

    await browser.selectOptions(screen.getByRole('combobox', { name: 'Maximum storage' }), '5120');
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith({ enabled: true, recent_video_limit: 10, storage_limit_mb: 5120 }));

    await browser.click(screen.getByRole('radio', { name: 'Off' }));
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith({ enabled: false, recent_video_limit: 10, storage_limit_mb: 5120 }));
    expect(screen.getByRole('combobox', { name: 'Maximum storage' }).hasAttribute('disabled')).toBe(true);
  });

  it('restores the prior selection when autosave fails', async () => {
    const browser = userEvent.setup();
    render(<RemotePlaybackCacheSettings onChange={vi.fn().mockRejectedValue(new Error('offline'))} value={{ enabled: true, recent_video_limit: 5, storage_limit_mb: 2048 }} />);

    await browser.click(screen.getByRole('radio', { name: '20' }));
    await waitFor(() => expect(screen.getByText(/could not save this preference/i)).toBeTruthy());
    expect((screen.getByRole('radio', { name: '5' }) as HTMLInputElement).checked).toBe(true);
  });

  it('keeps roving-radio focus available while an autosave is pending', async () => {
    const browser = userEvent.setup();
    render(<RemotePlaybackCacheSettings onChange={() => new Promise(() => undefined)} value={{ enabled: true, recent_video_limit: 5, storage_limit_mb: 2048 }} />);
    const selected = screen.getByRole('radio', { name: '5' });
    selected.focus();

    await browser.keyboard('{ArrowRight}');

    await waitFor(() => expect(screen.getByRole('radio', { name: '10' })).toBe(document.activeElement));
    expect(screen.getByRole('radio', { name: '10' }).hasAttribute('disabled')).toBe(false);
  });

  it('restores the last successful value when a later queued autosave fails', async () => {
    const browser = userEvent.setup();
    let resolveFirst: (() => void) | undefined;
    const first = new Promise<void>((resolve) => { resolveFirst = resolve; });
    const onChange = vi.fn()
      .mockImplementationOnce(() => first)
      .mockRejectedValueOnce(new Error('offline'));
    render(<RemotePlaybackCacheSettings onChange={onChange} value={{ enabled: true, recent_video_limit: 5, storage_limit_mb: 2048 }} />);

    await browser.click(screen.getByRole('radio', { name: '10' }));
    await browser.click(screen.getByRole('radio', { name: '20' }));
    expect(onChange).toHaveBeenCalledTimes(1);

    resolveFirst?.();
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText(/could not save this preference/i)).toBeTruthy());
    expect((screen.getByRole('radio', { name: '10' }) as HTMLInputElement).checked).toBe(true);
  });

  it('uses the styled Settings classes, not the retired stream-cache and settings-note ones', () => {
    const { container } = render(<RemotePlaybackCacheSettings onChange={vi.fn()} value={{ enabled: true, recent_video_limit: 5, storage_limit_mb: 2048 }} />);
    expect(container.querySelector('.g-stream-cache')).not.toBeNull();
    expect(container.querySelector('.g-setting-note')).not.toBeNull();
    expect(container.querySelector('.g-save-status')).not.toBeNull();
    for (const retired of ['.stream-cache-card', '.stream-cache-controls', '.settings-note', '.settings-save-status']) expect(container.querySelector(retired)).toBeNull();
  });
});
