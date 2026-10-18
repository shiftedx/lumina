import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ post: vi.fn(), del: vi.fn(), start: vi.fn(), pause: vi.fn(), play: vi.fn(), owner: 'alice' }));
vi.mock('../../api', () => ({ post: mocks.post, del: mocks.del, getSessionUserId: () => mocks.owner }));
vi.mock('../../playIntent', () => ({ pauseMediaForUser: mocks.pause, playMediaForUser: mocks.play }));
vi.mock('./chromecast', () => ({ chromecastReady: () => true, chromecastUnavailable: () => null, prepareChromecast: vi.fn(), startChromecast: mocks.start }));
import { ChromecastPanel } from './ChromecastPanel';
const ticket = { ticket_id: 'ticket', media_url: 'https://lumina.invalid/capability?token=scoped', title: 'Video', position_seconds: 42, duration: 90 };
function target() { return { status: vi.fn(async () => ({ position: 42, duration: 90, playing: true })), stop: vi.fn(async () => undefined), play: vi.fn(), pause: vi.fn(), seek: vi.fn() }; }
beforeEach(() => { vi.clearAllMocks(); mocks.owner = 'alice'; mocks.post.mockResolvedValue(ticket); mocks.del.mockResolvedValue(undefined); mocks.play.mockResolvedValue(undefined); });

it('keeps browser playback until target acknowledgement and returns here before stopping the source', async () => {
  const media = document.createElement('video'), output = target();
  let acknowledge: () => void = () => undefined;
  mocks.start.mockImplementation(async (mint) => { await mint(); await new Promise<void>((resolve) => { acknowledge = resolve; }); return output; });
  render(<ChromecastPanel audioIndex={null} getMedia={() => media} itemId="video" subtitle={null} />);
  fireEvent.click(screen.getByText('Chromecast preview'));
  fireEvent.click(screen.getByRole('button', { name: 'Cast to Chromecast' }));
  await waitFor(() => expect(mocks.post).toHaveBeenCalledOnce());
  expect(mocks.pause).not.toHaveBeenCalled();
  acknowledge();
  await screen.findByRole('button', { name: 'Return playback here' });
  expect(mocks.pause).toHaveBeenCalledWith(media);
  fireEvent.click(screen.getByRole('button', { name: 'Return playback here' }));
  await screen.findByRole('button', { name: 'Cast to Chromecast' });
  expect(mocks.play.mock.invocationCallOrder[0]).toBeLessThan(output.stop.mock.invocationCallOrder[0]);
  expect(mocks.del).toHaveBeenCalledWith('/api/chromecast/tickets/ticket');
  expect(media.currentTime).toBe(42);
});

it('revokes a failed target while retaining browser playback', async () => {
  const media = document.createElement('video');
  mocks.start.mockImplementation(async (mint) => { await mint(); throw new Error('Receiver did not acknowledge'); });
  const view = render(<ChromecastPanel audioIndex={null} getMedia={() => media} itemId="video" subtitle={null} />);
  fireEvent.click(screen.getByText('Chromecast preview'));
  fireEvent.click(screen.getByRole('button', { name: 'Cast to Chromecast' }));
  await screen.findByRole('alert');
  expect(mocks.pause).not.toHaveBeenCalled();
  expect(mocks.del).toHaveBeenCalledWith('/api/chromecast/tickets/ticket');
  view.unmount();
});


it('does not pause or take over playback after the signed-in owner changes during transfer', async () => {
  const media = document.createElement('video'), output = target();
  let acknowledge: () => void = () => undefined;
  mocks.start.mockImplementation(async (mint) => { await mint(); await new Promise<void>((resolve) => { acknowledge = resolve; }); return output; });
  const view = render(<ChromecastPanel audioIndex={null} getMedia={() => media} itemId="video" subtitle={null} />);
  fireEvent.click(screen.getByText('Chromecast preview'));
  fireEvent.click(screen.getByRole('button', { name: 'Cast to Chromecast' }));
  await waitFor(() => expect(mocks.post).toHaveBeenCalledOnce());
  mocks.owner = 'bob'; acknowledge();
  await waitFor(() => expect(output.stop).toHaveBeenCalledOnce());
  expect(mocks.del).toHaveBeenCalledWith('/api/chromecast/tickets/ticket');
  expect(mocks.pause).not.toHaveBeenCalled();
  view.unmount();
});


it('continues acknowledged browser playback when receiver-ticket cleanup is temporarily unavailable', async () => {
  const media = document.createElement('video'), output = target();
  mocks.start.mockImplementation(async (mint) => { await mint(); return output; });
  const view = render(<ChromecastPanel audioIndex={null} getMedia={() => media} itemId="video" subtitle={null} />);
  fireEvent.click(screen.getByText('Chromecast preview'));
  fireEvent.click(screen.getByRole('button', { name: 'Cast to Chromecast' }));
  await screen.findByRole('button', { name: 'Return playback here' });
  mocks.del.mockRejectedValueOnce(new Error('offline'));
  fireEvent.click(screen.getByRole('button', { name: 'Return playback here' }));
  await screen.findByText(/Playback returned here/);
  expect(mocks.play).toHaveBeenCalledWith(media);
  expect(mocks.pause).toHaveBeenCalledTimes(1);
  expect(output.stop).toHaveBeenCalledOnce();
  view.unmount();
});
