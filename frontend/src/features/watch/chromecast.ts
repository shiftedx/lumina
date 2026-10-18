/** Google Cast Web Sender + Default Media Receiver. Media always uses a revocable Lumina ticket. */
export type ChromecastTicket = {
  ticket_id: string; media_url: string; content_type: string; title: string; duration: number | null;
  position_seconds: number; expires_at: string; qualification: 'unverified_hardware';
  subtitle?: { url: string; language?: string | null; label: string } | null;
};
export type ChromecastStatus = { position: number; duration: number; playing: boolean };
type Info = { contentId: string; contentType?: string; metadata?: object; duration?: number; tracks?: object[] };
type Media = {
  media: Info; currentTime: number; duration?: number; playerState: string;
  getStatus(request: null, done: () => void, failed: () => void): void;
  play(request: null, done: () => void, failed: () => void): void;
  pause(request: null, done: () => void, failed: () => void): void;
  stop(request: null, done: () => void, failed: () => void): void;
  seek(request: { currentTime: number }, done: () => void, failed: () => void): void;
};
type CastSession = { loadMedia(request: object): Promise<void>; getMediaSession(): Media | null };
type Context = { setOptions(options: object): void; requestSession(): Promise<void>; getCurrentSession(): CastSession | null };
type SdkWindow = Window & {
  __onGCastApiAvailable?: (available: boolean) => void;
  cast?: { framework: { CastContext: { getInstance(): Context } } };
  chrome?: { cast?: { AutoJoinPolicy: { ORIGIN_SCOPED: string }; media: {
    DEFAULT_MEDIA_RECEIVER_APP_ID: string; MediaInfo: new(id: string, type: string) => Info;
    LoadRequest: new(info: Info) => { currentTime?: number; autoplay?: boolean; activeTrackIds?: number[] };
    Track: new(id: number, type: string) => { trackContentId?: string; trackContentType?: string; subtype?: string; name?: string; language?: string };
    TrackType: { TEXT: string }; TextTrackType: { SUBTITLES: string };
  } } };
};
let preparing: Promise<void> | null = null;
const sdkWindow = () => window as SdkWindow;
export function chromecastUnavailable(): string | null {
  if (!window.isSecureContext) return 'Chromecast needs Lumina opened over HTTPS.';
  if (!('presentation' in navigator)) return 'Use a Google Cast supported browser, such as Chrome, to cast.';
  return null;
}
export const chromecastReady = (): boolean => Boolean(sdkWindow().cast?.framework && sdkWindow().chrome?.cast);

export function prepareChromecast(): Promise<void> {
  if (chromecastReady()) return Promise.resolve();
  const reason = chromecastUnavailable();
  if (reason) return Promise.reject(new Error(reason));
  if (preparing) return preparing;
  preparing = new Promise<void>((resolve, reject) => {
    const script = document.createElement('script');
    const timeout = window.setTimeout(() => fail(), 15_000);
    function fail() {
      window.clearTimeout(timeout); script.remove(); preparing = null;
      sdkWindow().__onGCastApiAvailable = undefined;
      reject(new Error('Google Cast could not load. Check your connection and browser.'));
    }
    sdkWindow().__onGCastApiAvailable = (available) => {
      if (!available || !chromecastReady()) { fail(); return; }
      window.clearTimeout(timeout);
      const sdk = sdkWindow();
      sdk.cast!.framework.CastContext.getInstance().setOptions({ receiverApplicationId: sdk.chrome!.cast!.media.DEFAULT_MEDIA_RECEIVER_APP_ID,
        autoJoinPolicy: sdk.chrome!.cast!.AutoJoinPolicy.ORIGIN_SCOPED });
      resolve();
    };
    script.src = 'https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1';
    script.async = true;
    script.referrerPolicy = 'no-referrer';
    script.onerror = fail;
    document.head.append(script);
  });
  return preparing;
}

function command(work: (done: () => void, failed: () => void) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error('Chromecast did not acknowledge the command.')), 10_000);
    try { work(() => { window.clearTimeout(timer); resolve(); }, () => { window.clearTimeout(timer); reject(new Error('Chromecast could not complete the command.')); }); }
    catch (error) { window.clearTimeout(timer); reject(error); }
  });
}

export type ChromecastController = {
  status(): Promise<ChromecastStatus>; play(): Promise<void>; pause(): Promise<void>; seek(position: number): Promise<void>; stop(): Promise<void>;
};

/** Call directly from the Cast button: device selection needs the user's browser activation. */
export async function startChromecast(createTicket: () => Promise<ChromecastTicket>): Promise<ChromecastController> {
  if (!chromecastReady()) throw new Error('Enable Chromecast before choosing a device.');
  const sdk = sdkWindow();
  const context = sdk.cast!.framework.CastContext.getInstance();
  await context.requestSession();
  const session = context.getCurrentSession();
  if (!session) throw new Error('No Chromecast session was selected.');
  const ticket = await createTicket();
  const api = sdk.chrome!.cast!.media;
  const info = new api.MediaInfo(ticket.media_url, ticket.content_type);
  info.metadata = { metadataType: 1, title: ticket.title };
  if (ticket.duration) info.duration = ticket.duration;
  if (ticket.subtitle) {
    const track = new api.Track(1, api.TrackType.TEXT);
    Object.assign(track, { trackContentId: ticket.subtitle.url, trackContentType: 'text/vtt', subtype: api.TextTrackType.SUBTITLES,
      name: ticket.subtitle.label, language: ticket.subtitle.language || undefined });
    info.tracks = [track];
  }
  const request = new api.LoadRequest(info);
  request.currentTime = ticket.position_seconds;
  request.autoplay = true;
  request.activeTrackIds = ticket.subtitle ? [1] : [];
  try { await command((done, failed) => { void session.loadMedia(request).then(done, failed); }); }
  catch {
    const partial = session.getMediaSession();
    if (partial?.media.contentId === ticket.media_url) await command((done, failed) => partial.stop(null, done, failed)).catch(() => undefined);
    throw new Error('Chromecast could not load this file. Browser playback is still available.');
  }
  const media = session.getMediaSession();
  if (!media || media.media.contentId !== ticket.media_url) throw new Error('Chromecast did not acknowledge this media.');
  const current = () => {
    if (session.getMediaSession()?.media.contentId !== ticket.media_url) throw new Error('Chromecast is playing different media.');
    return media;
  };
  const controller: ChromecastController = {
    async status() {
      const value = current();
      await command((done, failed) => value.getStatus(null, done, failed));
      if (!Number.isFinite(value.currentTime) || value.currentTime < 0) throw new Error('Chromecast position is unavailable.');
      return { position: value.currentTime, duration: ticket.duration ?? 0, playing: value.playerState === 'PLAYING' };
    },
    play: () => command((done, failed) => current().play(null, done, failed)),
    pause: () => command((done, failed) => current().pause(null, done, failed)),
    seek: (position) => command((done, failed) => current().seek({ currentTime: position }, done, failed)),
    stop: () => command((done, failed) => current().stop(null, done, failed)),
  };
  let acknowledged = await controller.status();
  const deadline = Date.now() + 10_000;
  while (!acknowledged.playing && media.playerState === 'BUFFERING' && Date.now() < deadline) {
    await new Promise((resolve) => window.setTimeout(resolve, 250));
    acknowledged = await controller.status();
  }
  if (!acknowledged.playing || Math.abs(acknowledged.position - ticket.position_seconds) > 5) {
    await controller.stop().catch(() => undefined);
    throw new Error('Chromecast did not acknowledge playback at the requested position.');
  }
  return controller;
}
