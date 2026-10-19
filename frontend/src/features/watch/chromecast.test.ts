import { afterEach, expect, it, vi } from 'vitest';
import { startChromecast, type ChromecastTicket } from './chromecast';

const ticket: ChromecastTicket = { ticket_id: 'ticket', media_url: 'https://lumina.invalid/api/chromecast/ticket/media?token=scoped', content_type: 'video/mp4',
  title: 'Video', duration: 90, position_seconds: 42, expires_at: '2026-10-17T20:00:00Z', qualification: 'unverified_hardware',
  subtitle: { url: 'https://lumina.invalid/api/chromecast/ticket/subtitle.vtt?token=scoped', label: 'French', language: 'fr' } };

function sdk() {
  const media = { media: { contentId: ticket.media_url }, currentTime: 42, playerState: 'PLAYING',
    getStatus: vi.fn((_request, done) => done()), play: vi.fn((_request, done) => done()), pause: vi.fn((_request, done) => done()),
    stop: vi.fn((_request, done) => done()), seek: vi.fn((request, done) => { media.currentTime = request.currentTime; done(); }) };
  const session = { getMediaSession: () => media, loadMedia: vi.fn(async () => undefined) };
  const context = { requestSession: vi.fn(async () => undefined), getCurrentSession: () => session };
  Object.defineProperty(window, 'cast', { configurable: true, value: { framework: { CastContext: { getInstance: () => context } } } });
  Object.defineProperty(window, 'chrome', { configurable: true, value: { cast: { media: {
    MediaInfo: class { constructor(public contentId: string, public contentType: string) {} }, LoadRequest: class { constructor(public media: object) {} },
    Track: class { constructor(public trackId: number, public type: string) {} }, TrackType: { TEXT: 'TEXT' }, TextTrackType: { SUBTITLES: 'SUBTITLES' },
  } } } });
  return { media, session, context };
}
afterEach(() => { Reflect.deleteProperty(window, 'cast'); Reflect.deleteProperty(window, 'chrome'); });

it('selects the receiver from the user action before minting scoped media and acknowledges position/text tracks', async () => {
  const { media, session, context } = sdk();
  const mint = vi.fn(async () => ticket);
  const target = await startChromecast(mint);
  expect(context.requestSession.mock.invocationCallOrder[0]).toBeLessThan(mint.mock.invocationCallOrder[0]);
  expect(session.loadMedia).toHaveBeenCalledWith(expect.objectContaining({ currentTime: 42, autoplay: true, activeTrackIds: [1], media: expect.objectContaining({
    contentId: ticket.media_url, tracks: [expect.objectContaining({ trackContentId: ticket.subtitle!.url, trackContentType: 'text/vtt', language: 'fr' })],
  }) }));
  expect(await target.status()).toMatchObject({ position: 42, playing: true });
  await target.pause(); await target.seek(10); await target.play();
  expect(media.pause).toHaveBeenCalledOnce();
  expect(await target.status()).toMatchObject({ position: 10 });
  media.media.contentId = 'https://different.invalid/video';
  await expect(target.stop()).rejects.toThrow('different media');
});

it('refuses an incomplete load acknowledgement and stops only the just-loaded media', async () => {
  const { media } = sdk();
  media.currentTime = 0;
  await expect(startChromecast(async () => ticket)).rejects.toThrow('requested position');
  expect(media.stop).toHaveBeenCalledOnce();
});
