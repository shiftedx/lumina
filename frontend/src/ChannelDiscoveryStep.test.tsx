import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ChannelDiscoveryStep } from './features/onboarding/Onboarding';
import type { CategoryChannelSuggestions, ChannelCandidate } from './types';

function candidate(overrides: Partial<ChannelCandidate> & { display_name: string; channel_key: string }): ChannelCandidate {
  return {
    source_url: overrides.channel_key,
    source: 'youtube',
    source_label: 'YouTube',
    artwork_url: null,
    category_keys: [],
    following: false,
    ...overrides,
  };
}

const suggestions: CategoryChannelSuggestions[] = [
  {
    key: 'science-technology',
    label: 'Science & Technology',
    state: 'ranked',
    channels: [
      candidate({ display_name: 'Veritasium', channel_key: 'https://www.youtube.com/@veritasium', following: true }),
      candidate({ display_name: 'Kurzgesagt', channel_key: 'https://www.youtube.com/@kurzgesagt' }),
    ],
  },
  {
    key: 'music',
    label: 'Music',
    state: 'curated',
    channels: [candidate({ display_name: 'NPR Music', channel_key: 'https://www.youtube.com/@nprmusic' })],
  },
];

function renderStep(overrides: Partial<Parameters<typeof ChannelDiscoveryStep>[0]> = {}) {
  const onContinue = vi.fn();
  const view = render(
    <ChannelDiscoveryStep
      suggestions={suggestions}
      onSearch={vi.fn().mockResolvedValue([])}
      onResolveAddress={vi.fn().mockReturnValue(null)}
      onContinue={onContinue}
      onBack={vi.fn()}
      {...overrides}
    />,
  );
  return { onContinue, ...view };
}

describe('channel discovery step', () => {
  it('shows video previews through the artwork boundary and a branded cover when unavailable', () => {
    const illustrated = candidate({ display_name: 'Visual Creator', channel_key: 'visual', preview_artwork_url: '/api/artwork/remote/preview', preview_title: 'Inside the studio' });
    const { container } = renderStep({ suggestions: [{ key: 'music', label: 'Music', state: 'ranked', channels: [illustrated] }] });
    const tile = screen.getByRole('button', { name: 'Follow Visual Creator' });
    expect(within(tile).getByText('Inside the studio')).toBeTruthy();
    const image = tile.querySelector<HTMLImageElement>('img.g-onboarding-channel-image');
    expect(image?.getAttribute('src')).toContain('/api/artwork/remote/preview');
    fireEvent.error(image!);
    expect(tile.querySelector('.g-onboarding-channel-cover')).toBeTruthy();
    expect(container.querySelector<HTMLButtonElement>('.g-onboarding-picks-trigger')?.getAttribute('aria-expanded')).toBe('false');
  });

  it('restores draft picks and lets the member remove them from their collection', async () => {
    const browser = userEvent.setup();
    const onSelectionChange = vi.fn();
    const picked = suggestions[0].channels[1];
    const { onContinue } = renderStep({ initialSelected: [picked], onSelectionChange });
    expect(screen.getByRole('button', { name: 'Kurzgesagt — selected, deselect' }).getAttribute('aria-pressed')).toBe('true');
    await browser.click(screen.getByRole('button', { name: 'Your chosen creators, 1 selected' }));
    expect(within(screen.getByRole('list', { name: 'Your chosen creators' })).getByText('Kurzgesagt')).toBeTruthy();
    await browser.click(screen.getByRole('button', { name: 'Remove Kurzgesagt from your picks' }));
    expect(onSelectionChange).toHaveBeenCalledWith([]);
    await browser.click(screen.getByRole('button', { name: 'Enter my Home' }));
    expect(onContinue).toHaveBeenCalledWith([]);
  });

  it('keeps chosen creators when switching between interest shelves', async () => {
    const browser = userEvent.setup();
    renderStep();
    await browser.click(screen.getByRole('button', { name: 'Follow Kurzgesagt' }));
    await browser.click(within(screen.getByRole('group', { name: 'Browse interests' })).getByRole('button', { name: /^Music$/ }));
    expect(screen.queryByRole('heading', { name: 'Popular in Science & Technology' })).toBeNull();
    await browser.click(screen.getByRole('button', { name: 'Your chosen creators, 1 selected' }));
    expect(within(screen.getByRole('list', { name: 'Your chosen creators' })).getByText('Kurzgesagt')).toBeTruthy();
    await browser.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Your chosen creators' })).toBeNull();
    await browser.click(screen.getByRole('button', { name: 'For you' }));
    expect(screen.getByRole('button', { name: 'Kurzgesagt — selected, deselect' })).toBeTruthy();
  });

  it('explains an empty search and keeps earlier picks through a search failure', async () => {
    const onSearch = vi.fn().mockResolvedValueOnce([]).mockRejectedValueOnce(new Error('offline'));
    renderStep({ onSearch, initialSelected: [suggestions[0].channels[1]] });
    await userEvent.type(screen.getByRole('searchbox'), 'unknown');
    await userEvent.click(screen.getByRole('button', { name: 'Find channels' }));
    expect(await screen.findByText('No channels found for “unknown”')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Find channels' }));
    expect(await screen.findByText(/Your picks are still safe/)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Your chosen creators, 1 selected' }));
    expect(within(screen.getByRole('list', { name: 'Your chosen creators' })).getByText('Kurzgesagt')).toBeTruthy();
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Your chosen creators' })).toBeNull();
  });

  it('presents category suggestions as "Popular in <category>", never trending', () => {
    renderStep();
    expect(screen.getByRole('heading', { name: 'Popular in Science & Technology' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Popular in Music' })).toBeTruthy();
    expect(document.body.textContent?.toLowerCase()).not.toContain('trending');
  });

  it('lands keyboard focus on the step heading', () => {
    renderStep();
    expect(screen.getByRole('heading', { level: 1 })).toBe(document.activeElement);
  });

  it('recognizes channels the member already follows and does not re-select them', () => {
    renderStep();
    const following = screen.getByRole('button', { name: 'Veritasium — already following' }) as HTMLButtonElement;
    expect(following.disabled).toBe(true);
  });

  it('multi-selects and deselects candidates, then completes with the chosen follows', async () => {
    const browser = userEvent.setup();
    const { onContinue } = renderStep();

    await browser.click(screen.getByRole('button', { name: 'Follow Kurzgesagt' }));
    await browser.click(screen.getByRole('button', { name: 'Follow NPR Music' }));
    // Deselect NPR Music again.
    await browser.click(screen.getByRole('button', { name: 'NPR Music — selected, deselect' }));
    await browser.click(screen.getByRole('button', { name: 'Enter my Home with 1 channel' }));

    expect(onContinue).toHaveBeenCalledWith([
      { source_url: 'https://www.youtube.com/@kurzgesagt', display_name: 'Kurzgesagt' },
    ]);
  });

  it('lets a member complete without following any channel', async () => {
    const browser = userEvent.setup();
    const { onContinue } = renderStep();
    await browser.click(screen.getByRole('button', { name: 'Enter my Home' }));
    expect(onContinue).toHaveBeenCalledWith([]);
  });

  it('runs a manual channel search and lists the returned candidates', async () => {
    const browser = userEvent.setup();
    const onSearch = vi.fn().mockResolvedValue([
      candidate({ display_name: 'Found Channel', channel_key: 'https://www.youtube.com/@found' }),
    ]);
    renderStep({ onSearch });

    await browser.type(screen.getByRole('searchbox'), 'found');
    await browser.click(screen.getByRole('button', { name: 'Find channels' }));

    expect(onSearch).toHaveBeenCalledWith('found');
    expect(await screen.findByRole('button', { name: 'Follow Found Channel' })).toBeTruthy();
  });

  it('resolves a pasted channel address into a selectable candidate without searching', async () => {
    const browser = userEvent.setup();
    const onSearch = vi.fn().mockResolvedValue([]);
    const onResolveAddress = vi.fn().mockReturnValue(
      candidate({ display_name: 'pastedchannel', channel_key: 'https://www.youtube.com/@pastedchannel' }),
    );
    const { onContinue } = renderStep({ onSearch, onResolveAddress });

    await browser.type(screen.getByRole('searchbox'), 'https://www.youtube.com/@pastedchannel');
    await browser.click(screen.getByRole('button', { name: 'Find channels' }));

    expect(onSearch).not.toHaveBeenCalled();
    await browser.click(screen.getByRole('button', { name: 'Enter my Home with 1 channel' }));
    expect(onContinue).toHaveBeenCalledWith([
      { source_url: 'https://www.youtube.com/@pastedchannel', display_name: 'pastedchannel' },
    ]);
  });

  it('reads each channel tile state as text', async () => {
    const browser = userEvent.setup();
    renderStep();
    const tile = (name: string) => screen.getByRole('button', { name }).textContent ?? '';
    expect(tile('Veritasium — already following')).toContain('Following');
    expect(tile('Follow Kurzgesagt')).not.toContain('Following'); // 'Following' contains 'Follow', so only the negative can fail
    await browser.click(screen.getByRole('button', { name: 'Follow Kurzgesagt' }));
    expect(tile('Kurzgesagt — selected, deselect')).toContain('Selected');
  });

  it('shows a skeleton while loading, an empty state, and a retryable error', () => {
    const loading = render(<ChannelDiscoveryStep suggestions={[]} suggestionsLoading onSearch={vi.fn()} onResolveAddress={vi.fn()} onContinue={vi.fn()} onBack={vi.fn()} />);
    expect(screen.getByText('Loading channels…')).toBeTruthy();
    loading.unmount();
    const empty = render(<ChannelDiscoveryStep suggestions={[]} onSearch={vi.fn()} onResolveAddress={vi.fn()} onContinue={vi.fn()} onBack={vi.fn()} />);
    expect(screen.getByText('Your favorites belong here')).toBeTruthy();
    expect(screen.getByText('Search for a favorite creator above, or step inside and discover at your own pace.')).toBeTruthy();
    empty.unmount();
    render(<ChannelDiscoveryStep suggestions={[]} suggestionsError="x" onSearch={vi.fn()} onResolveAddress={vi.fn()} onContinue={vi.fn()} onBack={vi.fn()} />);
    expect(screen.getByRole('alert').textContent).toContain('Lumina could not load suggestions.');
  });

  it('says so when saving the follows failed', () => {
    render(<ChannelDiscoveryStep error="We could not save your setup just now. Please try again." suggestions={[]} onSearch={vi.fn()} onResolveAddress={vi.fn()} onContinue={vi.fn()} onBack={vi.fn()} />);
    expect(screen.getByRole('alert').textContent).toContain('We could not save your setup');
  });
});
