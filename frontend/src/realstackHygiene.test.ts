/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const perf = readFileSync('e2e/realstack/perf.spec.ts', 'utf8');
const metrics = readFileSync('../backend/app/services/client_metrics.py', 'utf8');
const ttff = perf.slice(perf.indexOf("test('the app reports its own time to first frame to Diagnostics'"));
const num = (text: string) => Number(text.replace(/_/g, ''));

describe('realstack hygiene (a65e388): the TTFF-to-Diagnostics beacon is not lost to the metrics quota', () => {
  const windowMs = num(metrics.match(/RateLimitRule\(max_requests=\d+, window_seconds=(\d+)\)/)![1]) * 1000;
  it('waits out the whole client-metrics window before it reads its baseline', () => {
    const wait = num(ttff.match(/waitForTimeout\(([\d_]+)\)/)![1]);
    expect(wait).toBeGreaterThanOrEqual(windowMs + 1000);
    expect(ttff.indexOf('waitForTimeout(')).toBeLessThan(ttff.indexOf('await directSamples()'));
  });
  it('leaves the test enough time for the wait, the page and the poll', () => {
    const budget = num(ttff.match(/test\.setTimeout\(([\d_]+)\)/)![1]);
    expect(budget).toBeGreaterThanOrEqual(num(ttff.match(/waitForTimeout\(([\d_]+)\)/)![1]) + 60_000);
  });
  it('still names the cause in the test, so a failure is read as a quota problem first', () => {
    expect(ttff).toMatch(/client-metrics quota/);
  });
});
