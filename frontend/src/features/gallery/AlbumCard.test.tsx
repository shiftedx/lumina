import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { albumSummary, artistSummary, userData } from '../../test/galleryFixtures';
import { AlbumCard } from './AlbumCard';
import { resetImageLoader } from './imageLoader';
import { summaryFor } from './titleCache';

beforeEach(() => resetImageLoader());

describe('AlbumCard', () => {
  it('draws a square cover and opens its title', async () => {
    const onOpen = vi.fn();
    const album = albumSummary();
    const { container } = render(<AlbumCard onOpen={onOpen} priority={2} sizes="168px" title={album} />);
    expect(container.querySelector('.g-art-square')).not.toBeNull();
    await userEvent.click(screen.getByRole('button'));
    expect(onOpen).toHaveBeenCalledWith(album);
  });
  it('names an album by artist, year and tracks, and captions it "Artist · 2019", with no state marker', () => {
    const { container } = render(<AlbumCard onOpen={vi.fn()} priority={2} sizes="168px" title={albumSummary('album-1', { user_data: userData({ played: false }) })} />);
    expect(screen.getByRole('button', { name: 'Album One by Artist A, 2019, 12 tracks' })).toBeTruthy();
    expect(screen.getByText('Album One')).toBeTruthy();
    expect(screen.getByText('Artist A · 2019')).toBeTruthy();
    expect(container.querySelector('[class^="g-marker"]')).toBeNull();
  });

  it('captions an artist with its album count, and the artist page’s own albums with the year only', () => {
    const artist = render(<AlbumCard onOpen={vi.fn()} priority={2} sizes="168px" title={artistSummary()} />);
    expect(screen.getByRole('button', { name: 'Artist A, 4 albums' })).toBeTruthy();
    expect(screen.getByText('4 albums')).toBeTruthy();
    artist.unmount();
    render(<AlbumCard onOpen={vi.fn()} priority={2} showArtist={false} sizes="168px" title={albumSummary()} />);
    expect(screen.getByText('2019')).toBeTruthy();
    expect(screen.queryByText('Artist A · 2019')).toBeNull();
  });

  it('remembers the summary it opens, so the album page paints at once', async () => {
    const album = albumSummary('album-remembered');
    render(<AlbumCard onOpen={vi.fn()} priority={2} sizes="168px" title={album} />);
    await userEvent.click(screen.getByRole('button'));
    expect(summaryFor('album-remembered')).toEqual(album);
  });
});

it('surfaces source quality on an artist album card and names mixed or partial metadata honestly', async () => {
  const onOpen = vi.fn();
  const album = albumSummary('mixed-album', { audio_quality: { track_count: 8, codecs: ['aac', 'flac'], bit_depths: [24], sample_rates: [44100, 96000], bitrate_kbps: 1468, formats_complete: true } });
  const view = render(<AlbumCard onOpen={onOpen} priority={2} showArtist={false} sizes="200px" title={album} />);
  expect(screen.getByText(/Mixed formats · AAC \+ FLAC/)).toBeTruthy();
  const disclosure = screen.getByLabelText(/File details for Album One/);
  expect(disclosure.textContent).toContain('1,468 kbps');
  await userEvent.click(disclosure);
  expect(disclosure.parentElement?.hasAttribute('open')).toBe(true);
  expect(screen.getByText('Average bitrate')).toBeTruthy();
  expect(onOpen).not.toHaveBeenCalled();
  await userEvent.click(disclosure);
  await userEvent.click(screen.getByRole('button', { name: /^Album One by Artist A/ }));
  expect(onOpen).toHaveBeenCalledWith(album);
  view.rerender(<AlbumCard onOpen={onOpen} priority={2} sizes="200px" title={{ ...album, audio_quality: { ...album.audio_quality!, bitrate_kbps: null, formats_complete: false } }} />);
  expect(screen.getByText(/Partial file metadata/)).toBeTruthy();
  expect(screen.getByText('Bitrate unavailable')).toBeTruthy();
});
