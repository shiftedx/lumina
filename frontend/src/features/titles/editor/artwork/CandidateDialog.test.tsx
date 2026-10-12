import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiRequestError } from '../../../../api';
import { CandidateDialog } from './CandidateDialog';
import { image } from './artworkFixtures';

const api = vi.hoisted(() => ({ listImageCandidates: vi.fn(), chooseTitleImage: vi.fn() }));
vi.mock('../../../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../../../api')>()), ...api }));

const slot = { type: 'Primary' as const, index: 0, key: 'images.Primary', label: 'Poster' };
const cands = [
  { tmdb_path: '/a.jpg', width: 2000, height: 3000, language: 'en', vote: 5, preview_url: '/p/a' },
  { tmdb_path: '/b.jpg', width: 1000, height: 1500, language: null, vote: 4, preview_url: '/p/b' },
];
const setup = (current: ReturnType<typeof image> | null = image('Primary', 0)) => {
  const h = { onChosen: vi.fn(), onStale: vi.fn(), onClose: vi.fn() };
  render(<CandidateDialog image={current} slot={slot} titleId="t1" {...h} />);
  return h;
};
beforeEach(() => { Object.values(api).forEach((fn) => fn.mockReset()); api.listImageCandidates.mockResolvedValue(cands); });

describe('CandidateDialog', () => {
  it('loads candidates for the slot with language and size', async () => {
    setup();
    expect(await screen.findByText('English · 2000 × 3000')).toBeTruthy();
    expect(screen.getByText('No language · 1000 × 1500')).toBeTruthy();
    expect(api.listImageCandidates).toHaveBeenCalledWith('t1', 'Primary', 0);
  });

  it('is a radio group; Use this image is disabled until one is chosen', async () => {
    setup();
    await screen.findByRole('radiogroup', { name: 'Poster candidates' });
    const use = screen.getByRole('button', { name: 'Use this image' }) as HTMLButtonElement;
    expect(use.disabled).toBe(true);
    await userEvent.click(screen.getAllByRole('radio')[0]);
    await userEvent.keyboard('{ArrowDown}');
    expect((screen.getAllByRole('radio')[1] as HTMLInputElement).checked).toBe(true);
    expect(use.disabled).toBe(false);
  });

  it('saves with the base tag and returns the images', async () => {
    api.chooseTitleImage.mockResolvedValue([image('Primary', 0)]);
    const h = setup();
    await userEvent.click((await screen.findAllByRole('radio'))[0]);
    await userEvent.click(screen.getByRole('button', { name: 'Use this image' }));
    await waitFor(() => expect(h.onChosen).toHaveBeenCalled());
    expect(api.chooseTitleImage).toHaveBeenCalledWith('t1', 'Primary', 0, { tmdb_path: '/a.jpg', base_tag: 'tag-Primary-0' });
  });

  it('sends a null base tag for an empty slot', async () => {
    api.chooseTitleImage.mockResolvedValue([]);
    setup(null);
    await userEvent.click((await screen.findAllByRole('radio'))[1]);
    await userEvent.click(screen.getByRole('button', { name: 'Use this image' }));
    await waitFor(() => expect(api.chooseTitleImage).toHaveBeenCalledWith('t1', 'Primary', 0, { tmdb_path: '/b.jpg', base_tag: null }));
  });

  it('shows a skeleton while loading and an empty state', async () => {
    api.listImageCandidates.mockResolvedValue([]);
    setup();
    expect(document.body.textContent).toContain('Loading images from TMDB');
    expect(await screen.findByText('TMDB has no images for this title.')).toBeTruthy();
  });

  it.each([
    ['tmdb_not_configured', 409, 'Add a TMDB key in Settings → Server → Metadata to choose artwork from TMDB.'],
    ['no_tmdb_id', 409, 'Match this title to TMDB first, using Fix match.'],
    ['bad_gateway', 502, 'TMDB did not answer. Try again.'],
  ])('explains %s in words', async (code, status, copy) => {
    api.listImageCandidates.mockRejectedValueOnce(new ApiRequestError(code, status));
    setup();
    expect(await screen.findByText(copy)).toBeTruthy();
    expect(!!screen.queryByRole('button', { name: 'Try again' })).toBe(status === 502);
  });

  it('Try again reloads', async () => {
    api.listImageCandidates.mockRejectedValueOnce(new ApiRequestError('x', 502));
    setup();
    await userEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('radiogroup')).toBeTruthy();
  });

  it('Cancel closes without saving', async () => {
    const h = setup();
    await screen.findByRole('radiogroup');
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(h.onClose).toHaveBeenCalled();
    expect(api.chooseTitleImage).not.toHaveBeenCalled();
  });

  it('a duplicate (409) says the image is already a backdrop instead of going stale', async () => {
    api.chooseTitleImage.mockRejectedValue(new ApiRequestError('duplicate', 409));
    const h = setup();
    await userEvent.click((await screen.findAllByRole('radio'))[0]);
    await userEvent.click(screen.getByRole('button', { name: 'Use this image' }));
    expect(await screen.findByText("That image is already one of this title's backdrops.")).toBeTruthy();
    expect(h.onStale).not.toHaveBeenCalled();
  });

  it('a stale tag (409) reports through onStale', async () => {
    api.chooseTitleImage.mockRejectedValue(new ApiRequestError('conflict', 409));
    const h = setup();
    await userEvent.click((await screen.findAllByRole('radio'))[0]);
    await userEvent.click(screen.getByRole('button', { name: 'Use this image' }));
    await waitFor(() => expect(h.onStale).toHaveBeenCalled());
  });

  it('a rate limit (429) names the artwork-change limit, not the upload one', async () => {
    api.chooseTitleImage.mockRejectedValue(new ApiRequestError('rate_limited', 429));
    setup();
    await userEvent.click((await screen.findAllByRole('radio'))[0]);
    await userEvent.click(screen.getByRole('button', { name: 'Use this image' }));
    expect(await screen.findByText('Too many artwork changes in a minute. Wait a moment, then try again.')).toBeTruthy();
  });
});
