const handlers = new WeakMap<HTMLMediaElement, (playing: boolean) => void>();

export function registerMediaPlayIntent(media: HTMLMediaElement, handler: (playing: boolean) => void) {
  handlers.set(media, handler);
  return () => {
    if (handlers.get(media) === handler) handlers.delete(media);
  };
}

/** Marks a native play as an explicit member action before starting it. */
export function playMediaForUser(media: HTMLMediaElement) {
  handlers.get(media)?.(true);
  return media.play();
}

/** Marks a native pause as an explicit member action before pausing it. */
export function pauseMediaForUser(media: HTMLMediaElement) {
  handlers.get(media)?.(false);
  media.pause();
}
