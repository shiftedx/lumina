import { describe, expect, it, vi } from 'vitest';

import { configureDashThroughput } from './dashThroughput';

function model() {
  const parent = vi.fn();
  let addEntry: (type: string, request: object) => void = () => { throw new Error('Model not installed'); };
  configureDashThroughput({
    extend(name, factory, override) {
      expect(name).toBe('ThroughputModel');
      expect(override).toBe(true);
      if (typeof factory !== 'function') throw new Error('Expected a model factory');
      addEntry = factory.call({ parent: { addEntry: parent } }).addEntry;
    },
  });
  return { parent, addEntry };
}

describe('DASH range throughput', () => {
  it('measures a segment from its own transfer instead of the earlier index at the same URL', () => {
    const { parent, addEntry } = model();
    const request = {
      type: 'MediaSegment', responsecode: 206, _mediaduration: 5, url: '/api/remote-streams/opaque/dash/1/video-0',
      tresponse: new Date(1000), _tfinish: new Date(1100),
      trace: [{ b: [100_000] }, { b: [300_000] }],
      _resourceTimingValues: { transferSize: 1808, responseStart: 20, responseEnd: 80 },
    };
    addEntry('video', request);
    expect(parent).toHaveBeenCalledOnce();
    const measured = parent.mock.calls[0][1];
    const timing = measured._resourceTimingValues;
    expect(timing.transferSize * 8 / (timing.responseEnd - timing.responseStart)).toBe(32_000);
    expect(measured.url).toBe(request.url);
    expect(request._resourceTimingValues.transferSize).toBe(1808); // Keep the original diagnostic metric.
  });

  it('does not count index probes labelled MediaSegment as bandwidth samples', () => {
    const { parent, addEntry } = model();
    addEntry('video', { type: 'MediaSegment', responsecode: 206, trace: [{ b: [1508] }] });
    addEntry('video', { type: 'MediaSegment', responsecode: 206, _mediaduration: Number.NaN, trace: [{ b: [1508] }] });
    expect(parent).not.toHaveBeenCalled();
  });

  it('counts small real segments, including audio, without requiring multiple progress events', () => {
    const { parent, addEntry } = model();
    addEntry('audio', { type: 'MediaSegment', responsecode: 206, _mediaduration: 2, tresponse: new Date(1000), _tfinish: new Date(1020), trace: [{ b: [4000] }] });
    expect(parent).toHaveBeenCalledWith('audio', expect.objectContaining({
      _resourceTimingValues: expect.objectContaining({ transferSize: 4000, responseStart: 1000, responseEnd: 1020 }),
    }));
  });

  it('does not invent throughput for missing or reversed timing and empty bodies', () => {
    const { parent, addEntry } = model();
    const request = { type: 'MediaSegment', responsecode: 206, _mediaduration: 5, tresponse: new Date(1000), _tfinish: new Date(1100), trace: [{ b: [4000] }] };
    addEntry('video', { ...request, tresponse: undefined });
    addEntry('video', { ...request, _tfinish: new Date(900) });
    addEntry('video', { ...request, trace: [] });
    addEntry('video', { ...request, responsecode: 503 });
    addEntry('video', { ...request, responsecode: 0 });
    expect(parent).not.toHaveBeenCalled();
  });

  it('bounds zero-duration clock samples and leaves other request types with dash.js', () => {
    const { parent, addEntry } = model();
    addEntry('video', { type: 'MediaSegment', responsecode: 206, _mediaduration: 5, tresponse: new Date(1000), _tfinish: new Date(1000), trace: [{ b: [4000] }] });
    expect(parent.mock.calls[0][1]._resourceTimingValues.responseEnd).toBe(1001);
    const manifest = { type: 'MPD', trace: [{ b: [4000] }] };
    addEntry('video', manifest);
    expect(parent).toHaveBeenLastCalledWith('video', manifest);
  });
});
