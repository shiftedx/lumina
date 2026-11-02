import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ChapterSeekMarkers, DescriptionWithTimestamps } from './chapters';

describe('ChapterSeekMarkers', () => {
  it('places one marker per distinct time, marks the active one and carries the moment origin', () => {
    const { container } = render(
      <ChapterSeekMarkers
        chapters={[{ start_time: 60, title: 'Main', origin: 'source' }, { start_time: 0, title: 'Opening' }, { start_time: 60, title: 'Duplicate' }, { start_time: 90, title: 'Mine', origin: 'you' }]}
        currentTime={75}
        duration={120}
        interactive={false}
        onSeek={vi.fn()}
      />,
    );
    const markers = [...container.querySelectorAll('[data-chapter-marker]')];
    expect(markers.map((marker) => marker.getAttribute('data-chapter-position'))).toEqual(['0', '50', '75']);
    expect(markers[1].className).toBe('active');
    expect(markers[2].getAttribute('data-moment-origin')).toBe('you');
  });
});

describe('DescriptionWithTimestamps', () => {
  it('renders description timestamps as safe text and keyboard-accessible seek links', async () => {
    const user = userEvent.setup();
    const onSeek = vi.fn();
    const description = 'Read <img data-testid="unsafe" src=x> from 1:23 onward.';
    const start = description.indexOf('1:23');
    render(<DescriptionWithTimestamps description={description} onSeek={onSeek} timestamps={[{ start, end: start + 4, seconds: 83, label: '1:23' }]} />);

    const timestamp = screen.getByRole('button', { name: 'Seek to 1:23' });
    timestamp.focus();
    await user.keyboard('{Enter}');
    await user.keyboard(' ');

    expect(onSeek).toHaveBeenNthCalledWith(1, 83);
    expect(onSeek).toHaveBeenNthCalledWith(2, 83);
    expect(screen.queryByTestId('unsafe')).toBeNull();
    expect(screen.getByText(/Read <img/)).toBeTruthy();
  });

  it('ignores malformed timestamp spans rather than slicing untrusted description data', () => {
    render(<DescriptionWithTimestamps description="Listen at 1:23." onSeek={vi.fn()} timestamps={[{ start: 0, end: 4, seconds: 83, label: '1:23' }]} />);

    expect(screen.queryByRole('button', { name: 'Seek to 1:23' })).toBeNull();
    expect(screen.getByText('Listen at 1:23.')).toBeTruthy();
  });
});
