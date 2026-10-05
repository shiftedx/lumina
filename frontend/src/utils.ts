export function formatDuration(seconds?: number | null): string {
  if (seconds === undefined || seconds === null || Number.isNaN(seconds)) {
    return '—';
  }

  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remaining = total % 60;

  if (hours > 0) {
    return [hours, String(minutes).padStart(2, '0'), String(remaining).padStart(2, '0')].join(':');
  }

  return [minutes, String(remaining).padStart(2, '0')].join(':');
}

export function formatBytes(bytes?: number | null): string {
  if (bytes === undefined || bytes === null || Number.isNaN(bytes)) {
    return '—';
  }

  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let index = 0;

  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }

  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

export function formatSpeed(bytesPerSecond?: number | null): string {
  if (!bytesPerSecond || Number.isNaN(bytesPerSecond)) {
    return '—';
  }
  return `${formatBytes(bytesPerSecond)}/s`;
}

export function formatEta(seconds?: number | null): string {
  if (seconds === undefined || seconds === null || Number.isNaN(seconds)) {
    return '—';
  }
  if (seconds <= 0) {
    return '0s';
  }
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.round(seconds % 60);
  if (minutes === 0) {
    return `${remaining}s`;
  }
  return `${minutes}m ${remaining.toString().padStart(2, '0')}s`;
}

/** Server datetimes without an offset are naive UTC database values. */
export function parseServerTime(timestamp: string): Date {
  return new Date(/^\d{4}-\d{2}-\d{2}T/.test(timestamp) && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(timestamp) ? `${timestamp}Z` : timestamp);
}

export function formatTime(timestamp?: string | null): string {
  if (!timestamp) {
    return '—';
  }
  const date = parseServerTime(timestamp);
  if (Number.isNaN(date.getTime())) {
    return timestamp;
  }
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);
}

export function formatCompactNumber(value?: number | null): string {
  if (value === undefined || value === null || Number.isNaN(value)) {
    return '—';
  }
  return new Intl.NumberFormat(undefined, {
    notation: 'compact',
    maximumFractionDigits: value >= 1000 ? 1 : 0,
  }).format(value);
}

/** "12,400 live" (full) or "12K live" (compact, phones); null when the provider gave no count. */
export function liveCountLabel(count: number | null | undefined, compact: boolean, suffix = 'live'): string | null {
  if (count === null || count === undefined || !Number.isFinite(count) || count < 0) return null;
  return `${compact ? formatCompactNumber(count) : count.toLocaleString()} ${suffix}`;
}

export const errorMessage = (error: unknown, fallback: string): string => (error instanceof Error ? error.message : fallback);

export const formatDateTime = (value: string | null | undefined, empty = '—'): string =>
  value ? parseServerTime(value).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : empty;
