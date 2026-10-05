/**
 * Keeps a Play's side requests (tags, notes, transcripts, tracks, ...) off the wire until its first frame.
 *
 * Over plain http Chrome opens at most 6 connections per host, one of them the event stream. The watch page asks for
 * about a dozen things at mount, and the <video>'s own request queues behind them: with a loaded server (each answer
 * taking 2 s) the first frame came 4 s late. `holdBackground` is taken at Play and released at the first frame, a
 * failure or, at the latest, MAX_HOLD_MS, so a player that never gets a frame can never starve the page.
 */
export const MAX_HOLD_MS = 3_000;

let open: Promise<void> | null = null;
let release: (() => void) | null = null;
let timer: ReturnType<typeof setTimeout> | null = null;

/** Side requests wait here; resolves at once when no Play is holding them. */
export const backgroundReady = (): Promise<void> => open ?? Promise.resolve();

/** A Play starts: hold side requests until `releaseBackground()` or MAX_HOLD_MS. A second hold restarts the clock. */
export function holdBackground(): void {
  if (!open) open = new Promise<void>((resolve) => { release = resolve; });
  if (timer) clearTimeout(timer);
  timer = setTimeout(releaseBackground, MAX_HOLD_MS);
}

export function releaseBackground(): void {
  if (timer) clearTimeout(timer);
  timer = null;
  release?.();
  open = null;
  release = null;
}
