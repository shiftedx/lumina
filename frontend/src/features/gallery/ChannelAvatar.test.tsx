import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ChannelAvatar } from './ChannelAvatar';

describe('ChannelAvatar', () => {
  it('shows the proxied image, a monogram on failure or for a provider URL, and a gold ring only when live', () => {
    const { container, rerender } = render(<ChannelAvatar name="Harbor Films" size={40} url="/api/artwork/remote/a" />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.getAttribute('aria-hidden')).toBe('true');
    expect(root.style.width).toBe('40px');
    expect(root.classList.contains('is-live')).toBe(false);
    fireEvent.error(root.querySelector('img') as HTMLImageElement);
    expect(root.querySelector('img')).toBeNull();
    expect(root.textContent).toBe('HF');
    rerender(<ChannelAvatar live name="Harbor Films" size={120} url="https://yt3.ggpht.com/a" />);
    expect(container.querySelector('img')).toBeNull();
    expect((container.firstElementChild as HTMLElement).classList.contains('is-live')).toBe(true);
  });
});
