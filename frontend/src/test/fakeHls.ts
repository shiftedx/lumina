type HlsErrorHandler = (event: string, data: { fatal: boolean; type?: string }) => void;

/** Records what the player asked of hls.js. Use with `vi.mock('hls.js', () => import('./test/fakeHls'))`. */
export const hlsCalls = {
  attached: [] as unknown[],
  configs: [] as unknown[],
  destroyed: 0,
  handlers: [] as HlsErrorHandler[],
  levelLoaded: [] as Array<() => void>,
  manifestParsed: [] as Array<() => void>,
  instances: [] as FakeHls[],
  listeners: {} as Record<string, Array<(event: string, data: never) => void>>,
  onManifestParsed: [] as Array<(event: string, data: { levels: unknown[] }) => void>,
  mediaRecoveries: 0,
  networkRecoveries: 0,
  sources: [] as string[],
  startLoads: [] as unknown[],
  stopped: 0,
};

export function resetHlsCalls() {
  Object.assign(hlsCalls, {
    attached: [], configs: [], destroyed: 0, handlers: [], levelLoaded: [], manifestParsed: [], instances: [], listeners: {}, onManifestParsed: [],
    mediaRecoveries: 0, networkRecoveries: 0, sources: [], startLoads: [], stopped: 0,
  });
}

export default class FakeHls {
  static Events = { ERROR: 'error', FRAG_BUFFERED: 'hlsFragBuffered', FRAG_LOADED: 'hlsFragLoaded', LEVEL_LOADED: 'hlsLevelLoaded', MANIFEST_PARSED: 'hlsManifestParsed' };
  static ErrorTypes = { MEDIA_ERROR: 'mediaError', NETWORK_ERROR: 'networkError' };
  static isSupported() { return true; }
  latestLevelDetails = { edge: 12 };
  startLevel = -1;
  constructor(config?: unknown) { hlsCalls.configs.push(config); hlsCalls.instances.push(this); }
  attachMedia(data: unknown) { hlsCalls.attached.push(data); }
  destroy() { hlsCalls.destroyed += 1; }
  loadSource(url: string) { hlsCalls.sources.push(url); }
  on(event: string, handler: HlsErrorHandler) {
    if (event === FakeHls.Events.MANIFEST_PARSED) hlsCalls.onManifestParsed.push(handler as never);
    else if (event === FakeHls.Events.ERROR) hlsCalls.handlers.push(handler);
    else (hlsCalls.listeners[event] ||= []).push(handler as never);
  }
  once(event: string, callback: () => void) {
    if (event === FakeHls.Events.MANIFEST_PARSED) hlsCalls.manifestParsed.push(callback);
    if (event === FakeHls.Events.LEVEL_LOADED) hlsCalls.levelLoaded.push(callback);
  }
  recoverMediaError() { hlsCalls.mediaRecoveries += 1; }
  startLoad(position?: number) { hlsCalls.networkRecoveries += 1; hlsCalls.startLoads.push(position); }
  stopLoad() { hlsCalls.stopped += 1; }
}
