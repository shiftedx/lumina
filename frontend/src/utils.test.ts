import { describe, expect, it } from 'vitest';
import { parseServerTime } from './utils';

describe('parseServerTime', () => {
  it('reads naive server datetimes as UTC and keeps explicit offsets', () => {
    expect(parseServerTime('2026-09-25T06:02:09.123').toISOString()).toBe('2026-09-25T06:02:09.123Z');
    expect(parseServerTime('2026-09-25T06:02:09+02:00').toISOString()).toBe('2026-09-25T04:02:09.000Z');
    expect(parseServerTime('2026-09-25T06:02:09Z').toISOString()).toBe('2026-09-25T06:02:09.000Z');
  });
});
