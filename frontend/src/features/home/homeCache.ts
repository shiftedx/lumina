/**
 * Home's per-member session cache: the last result of each shelf, so a return visit
 * paints at once and revalidates in the background. Memory only; the app forgets it on sign-out or a member switch.
 */
const entries = new Map<string, unknown>();
const keyOf = (userId: string, key: string) => `${userId}\u0000${key}`;

/** The last value stored under (member, key) this session; undefined when none. */
export function cachedShelf<T>(userId: string, key: string): T | undefined {
  return entries.get(keyOf(userId, key)) as T | undefined;
}

export function rememberShelf<T>(userId: string, key: string, value: T): void {
  entries.set(keyOf(userId, key), value);
}

/** Sign-out or member switch: the next member must never see this member's shelves. */
export function forgetHomeCache(): void {
  entries.clear();
}
