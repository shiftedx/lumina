import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { type OptionalProvider, StreamingProvidersProvider } from '../streaming/providers';
import { StreamingProvidersCard } from './youCards';

function Harness({ onChange }: { onChange: (next: OptionalProvider[]) => void }) {
  const [enabled, setEnabled] = useState<OptionalProvider[]>(['twitch']);
  return <StreamingProvidersProvider enabled={enabled} onChange={(next) => { setEnabled(next); onChange(next); }}><StreamingProvidersCard /></StreamingProvidersProvider>;
}

describe('StreamingProvidersCard', () => {
  it('shows YouTube always on, Twitch on and Kick off by default', () => {
    render(<Harness onChange={vi.fn()} />);
    expect(screen.getByText(/YouTube/).textContent).toContain('Always on');
    expect((screen.getByRole('switch', { name: 'Twitch' }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('switch', { name: 'Kick' }) as HTMLInputElement).checked).toBe(false);
    expect(screen.getByText(/Kick chat needs an account; playback works/)).toBeTruthy();
  });

  it('reports each toggle so the workspace persists it', async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await userEvent.click(screen.getByRole('switch', { name: 'Kick' }));
    expect(onChange).toHaveBeenLastCalledWith(['twitch', 'kick']);
    await userEvent.click(screen.getByRole('switch', { name: 'Twitch' }));
    expect(onChange).toHaveBeenLastCalledWith(['kick']);
  });
});
