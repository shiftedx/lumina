/** The one funnel for "the server said no": api.ts and the players report here; the access hook listens. No imports, so api.ts can use it. */
export const ACCESS_STOP_EVENT = 'lumina:access-stop';
export type AccessStop = 'screen_time_up' | 'outside_hours' | 'downloads_not_allowed' | `streaming_blocked:${string}`;

/** A 403 detail that is one of the member-access codes (the body may be raw JSON from a media request). */
export function accessStopCode(status: number | undefined, detail: string | undefined): AccessStop | null {
  if (status !== 403 || !detail) return null;
  const match = /(screen_time_up|outside_hours|downloads_not_allowed|streaming_blocked:[a-z_]+)/.exec(detail);
  return (match?.[1] as AccessStop | undefined) ?? null;
}

export function reportAccessStop(code: AccessStop): void {
  window.dispatchEvent(new CustomEvent<AccessStop>(ACCESS_STOP_EVENT, { detail: code }));
}
