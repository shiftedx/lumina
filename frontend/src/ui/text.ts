/** Server messages are shown as text, never HTML, and cut at 240 characters. */
export const clampServerText = (text: string, limit = 240): string => (text.length > limit ? `${text.slice(0, limit)}…` : text);
