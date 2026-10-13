import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ArtistPhotoCredit } from './ArtistPhotoCredit';
import type { MusicTitleInfo } from './musicTypes';

const music = (url: string) => ({ artwork_credits: [{ image_type: 'Primary', source_url: url, author: '<script>Author</script>', license: 'CC BY-SA 4.0' }] }) as MusicTitleInfo;
describe('ArtistPhotoCredit', () => {
  it('shows attribution as text with a link to the original photograph', () => {
    render(<ArtistPhotoCredit music={music('https://commons.wikimedia.org/wiki/File:Portrait.jpg')} />);
    const credit = screen.getByRole('link', { name: 'Photo · <script>Author</script> · CC BY-SA 4.0' });
    expect(credit.getAttribute('href')).toContain('commons.wikimedia.org/wiki/File:');
    expect(document.querySelector('script')).toBeNull();
  });
  it('does not link to an untrusted provider URL', () => {
    render(<ArtistPhotoCredit music={music('https://commons.wikimedia.org.evil.invalid/wiki/File:A')} />);
    expect(screen.queryByRole('link')).toBeNull();
  });
});
