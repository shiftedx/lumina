import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiRequestError, createJob, getUpNext, previewUrl } from './api';
import { acquisitionFormatOptions, useMediaAcquisition } from './mediaAcquisition';
import type { DownloadJob, MemberRecommendationSnapshot, PreviewResponse, YouTubeSearchResult } from './types';
import { DEFAULT_AUTO_SKIP, DEFAULT_PROFANITY, type WorkspaceState } from './workspace';

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  createJob: vi.fn(),
  previewUrl: vi.fn(),
  getUpNext: vi.fn(),
}));

function upNextSnapshot(items: YouTubeSearchResult[]): MemberRecommendationSnapshot {
  return {
    items, categories: [], state: 'ready', refreshing: false, stale: false, error: null,
  };
}

const remote: YouTubeSearchResult = {
  id: 'video-1',
  title: 'Remote film',
  thumbnail: 'https://images.example.test/video.jpg',
  webpage_url: 'https://example.test/video-1',
};

const preview: PreviewResponse = {
  kind: 'video',
  title: 'Inspected film',
  webpage_url: remote.webpage_url,
  entries: [],
  raw: { id: remote.id, thumbnail: remote.thumbnail },
};

const job: DownloadJob = {
  id: 'job-1',
  source_url: remote.webpage_url as string,
  status: 'queued',
};

function acquisitionWorkspace() {
  const state = {
    currentUser: {
      id: 'member-one',
      username: 'one',
      display_name: 'Member One',
      role: 'viewer',
      is_active: true,
    },
    jobs: [],
    library: [],
    preferences: {
      formatPreset: 'best',
      outputFolder: 'family/videos',
      downloadSubtitles: true,
      theaterMode: false,
      streamingProviders: ['twitch'],
      autoplayUpNext: true,
      theme: 'system',
      outputContainer: 'mp4',
      sidebarCollapsed: false,
      playerVolume: 1,
      playbackMaxHeight: null,
      normalizeLoudness: true,
      autoSkip: DEFAULT_AUTO_SKIP,
      captions: { size: 'medium', background: 'shadow' },
      profanity: DEFAULT_PROFANITY,
      homeShelves: null,
      settingsAdvanced: false,
    },
  } as Pick<WorkspaceState, 'currentUser' | 'jobs' | 'library' | 'preferences'>;
  return {
    state,
    dispatch: vi.fn(),
    captureSessionToken: () => 1,
    isSessionTokenCurrent: () => true,
  };
}

describe('media acquisition interface', () => {
  beforeEach(() => {
    vi.mocked(previewUrl).mockReset().mockResolvedValue(preview);
    vi.mocked(createJob).mockReset().mockResolvedValue(job);
    vi.mocked(getUpNext).mockReset().mockResolvedValue(upNextSnapshot([]));
  });

  it('owns inspection, format choices, and audio queue submission', async () => {
    const workspace = acquisitionWorkspace();
    const onMessage = vi.fn();
    const { result } = renderHook(() => useMediaAcquisition({
      workspace,
      onMessage,
    }));

    await act(async () => { await result.current.open(remote); });
    act(() => {
      result.current.chooseFormat('audio_only');
    });
    await act(async () => { await result.current.queueSelection(); });

    expect(previewUrl).toHaveBeenCalledWith(expect.objectContaining({
      source_url: remote.webpage_url,
      format_selection: expect.objectContaining({ preset: 'best', extract_audio: false }),
    }));
    expect(createJob).toHaveBeenCalledWith(expect.objectContaining({
      format_selection: expect.objectContaining({ preset: 'audio_only', extract_audio: true, audio_format: 'mp3' }),
      output_profile: expect.objectContaining({ base_path: null, subdir: 'family/videos' }),
    }));
    expect(workspace.dispatch).toHaveBeenCalledWith({ type: 'jobs/upsert', job, select: true });
    expect(onMessage).toHaveBeenCalledWith('Audio added to the download queue.');
    expect(result.current.state.selection?.preview?.title).toBe('Inspected film');
    expect(result.current.state.format).toBe('audio_only');
    expect(result.current.state.queueingSourceUrls).toEqual([]);
    expect(result.current.state.failure).toBeNull();
  });

  it('reinspects before queueing when the format choice changed', async () => {
    const workspace = acquisitionWorkspace();
    const refreshedPreview = { ...preview, title: 'Audio access confirmed' };
    vi.mocked(previewUrl).mockResolvedValueOnce(preview).mockResolvedValueOnce(refreshedPreview);
    const { result } = renderHook(() => useMediaAcquisition({
      workspace,
      onMessage: vi.fn(),
    }));

    await act(async () => { await result.current.open(remote); });
    act(() => {
      result.current.chooseFormat('audio_only');
    });
    await act(async () => { await result.current.queueSelection(); });

    expect(previewUrl).toHaveBeenNthCalledWith(2, expect.objectContaining({
      format_selection: expect.objectContaining({ preset: 'audio_only', extract_audio: true }),
    }));
    expect(createJob).toHaveBeenCalledWith(expect.objectContaining({
      preview_snapshot: expect.objectContaining({ title: 'Audio access confirmed' }),
    }));
  });

  it('does not create a job when the session changes during queue reinspection', async () => {
    const workspace = acquisitionWorkspace();
    let sessionCurrent = true;
    workspace.isSessionTokenCurrent = () => sessionCurrent;
    let finishReinspection: ((value: PreviewResponse) => void) | undefined;
    vi.mocked(previewUrl)
      .mockResolvedValueOnce(preview)
      .mockImplementationOnce(() => new Promise((resolve) => { finishReinspection = resolve; }));
    const { result } = renderHook(() => useMediaAcquisition({ workspace, onMessage: vi.fn() }));

    await act(async () => { await result.current.open(remote); });
    act(() => { result.current.chooseFormat('audio_only'); });
    let queued: Promise<DownloadJob | null> | undefined;
    act(() => { queued = result.current.queueSelection(); });
    await vi.waitFor(() => expect(finishReinspection).toBeTypeOf('function'));
    sessionCurrent = false;
    await act(async () => {
      finishReinspection?.({ ...preview, title: 'Too late' });
      await queued;
    });

    expect(createJob).not.toHaveBeenCalled();
    expect(result.current.state.selection?.preview?.title).toBe('Inspected film');
  });

  it('tells Up Next which channel the current video is on', async () => {
    const channel = 'UCabcdefghijklmnopqrstuv';
    vi.mocked(previewUrl).mockResolvedValue({ ...preview, raw: { channel_id: channel, channel_url: `https://www.youtube.com/channel/${channel}` } });
    const { result } = renderHook(() => useMediaAcquisition({ workspace: acquisitionWorkspace(), onMessage: vi.fn() }));
    await act(async () => { await result.current.open(remote); });
    expect(getUpNext).toHaveBeenCalledWith(expect.objectContaining({ channel_id: channel, channel_url: `https://www.youtube.com/channel/${channel}` }));
  });

  it('sends no channel when the video names none, and never a too-long address or a non-UC id', async () => {
    vi.mocked(previewUrl).mockResolvedValue({ ...preview, raw: { channel_id: 'not-a-uc-id', channel_url: `https://example.test/${'c'.repeat(3000)}` } });
    const { result } = renderHook(() => useMediaAcquisition({ workspace: acquisitionWorkspace(), onMessage: vi.fn() }));
    await act(async () => { await result.current.open(remote); });
    const request = vi.mocked(getUpNext).mock.calls[0][0];
    expect(request).not.toHaveProperty('channel_id');
    expect(request).not.toHaveProperty('channel_url');
  });

  it('keeps late related results from an earlier inspection out of the current selection', async () => {
    const workspace = acquisitionWorkspace();
    const first = { ...remote, id: 'video-a', title: 'Film A', uploader: 'Maker A', webpage_url: 'https://example.test/video-a' };
    const second = { ...remote, id: 'video-b', title: 'Film B', uploader: 'Maker B', webpage_url: 'https://example.test/video-b' };
    let finishFirstRelated: ((value: MemberRecommendationSnapshot) => void) | undefined;
    vi.mocked(previewUrl)
      .mockResolvedValueOnce({ ...preview, title: 'Film A', webpage_url: first.webpage_url })
      .mockResolvedValueOnce({ ...preview, title: 'Film B', webpage_url: second.webpage_url });
    vi.mocked(getUpNext)
      .mockImplementationOnce(() => new Promise((resolve) => { finishFirstRelated = resolve; }))
      .mockResolvedValueOnce(upNextSnapshot([{ ...second, id: 'related-b', title: 'Related B', webpage_url: 'https://example.test/related-b' }]));
    const { result } = renderHook(() => useMediaAcquisition({ workspace, onMessage: vi.fn() }));

    await act(async () => { await result.current.open(first); });
    await vi.waitFor(() => expect(finishFirstRelated).toBeTypeOf('function'));
    await act(async () => { await result.current.open(second); });
    await vi.waitFor(() => expect(result.current.state.related?.[0]?.title).toBe('Related B'));
    await act(async () => {
      finishFirstRelated?.(upNextSnapshot([{ ...first, id: 'related-a', title: 'Related A', webpage_url: 'https://example.test/related-a' }]));
      await Promise.resolve();
    });

    expect(result.current.state.selection?.item.title).toBe('Film B');
    expect(result.current.state.related.map((item) => item.title)).toEqual(['Related B']);
  });

  it('tracks distinct queue requests independently and suppresses concurrent duplicates', async () => {
    const workspace = acquisitionWorkspace();
    const second = { ...remote, id: 'video-2', title: 'Remote sequel', webpage_url: 'https://example.test/video-2' };
    const finishes = new Map<string, (value: DownloadJob) => void>();
    vi.mocked(previewUrl).mockImplementation(async (payload) => ({ ...preview, webpage_url: payload.source_url, title: payload.source_url }));
    vi.mocked(createJob).mockImplementation((payload) => new Promise((resolve) => {
      finishes.set(String(payload.source_url), resolve);
    }));
    const onMessage = vi.fn();
    const { result } = renderHook(() => useMediaAcquisition({ workspace, onMessage }));

    let firstQueue: Promise<DownloadJob | null> | undefined;
    act(() => { firstQueue = result.current.queue(remote); });
    await vi.waitFor(() => expect(finishes.has(remote.webpage_url as string)).toBe(true));
    let duplicateQueue: Promise<DownloadJob | null> | undefined;
    act(() => { duplicateQueue = result.current.queue(remote); });
    await act(async () => { expect(await duplicateQueue).toBeNull(); });

    let secondQueue: Promise<DownloadJob | null> | undefined;
    act(() => { secondQueue = result.current.queue(second); });
    await vi.waitFor(() => expect(finishes.has(second.webpage_url as string)).toBe(true));
    expect(result.current.state.queueingSourceUrls).toEqual(expect.arrayContaining([remote.webpage_url, second.webpage_url]));
    expect(createJob).toHaveBeenCalledTimes(2);

    await act(async () => {
      finishes.get(remote.webpage_url as string)?.({ ...job, source_url: remote.webpage_url as string });
      await firstQueue;
    });
    expect(result.current.state.queueingSourceUrls).toEqual([second.webpage_url]);

    await act(async () => {
      finishes.get(second.webpage_url as string)?.({ ...job, id: 'job-2', source_url: second.webpage_url as string });
      await secondQueue;
    });
    expect(result.current.state.queueingSourceUrls).toEqual([]);
    expect(onMessage).toHaveBeenCalledWith('This video is already being added to the download queue.');
  });

  it('preserves a live queue guard and busy identity across presentation reset', async () => {
    const workspace = acquisitionWorkspace();
    let finishQueue: ((value: DownloadJob) => void) | undefined;
    vi.mocked(createJob)
      .mockImplementationOnce(() => new Promise((resolve) => { finishQueue = resolve; }))
      .mockResolvedValueOnce({ ...job, id: 'duplicate-job' });
    const { result } = renderHook(() => useMediaAcquisition({ workspace, onMessage: vi.fn() }));

    let firstQueue: Promise<DownloadJob | null> | undefined;
    act(() => { firstQueue = result.current.queue(remote); });
    await vi.waitFor(() => expect(finishQueue).toBeTypeOf('function'));
    act(() => { result.current.reset(); });

    expect(result.current.isQueueing(remote)).toBe(true);
    expect(result.current.state.queueingSourceUrls).toEqual([remote.webpage_url]);
    await act(async () => { expect(await result.current.queue(remote)).toBeNull(); });
    expect(createJob).toHaveBeenCalledTimes(1);

    await act(async () => {
      finishQueue?.(job);
      await firstQueue;
    });
    expect(result.current.isQueueing(remote)).toBe(false);
  });

  it('hides prior-session queue presentation without releasing its live duplicate guard', async () => {
    let currentSession = 1;
    const workspace = {
      ...acquisitionWorkspace(),
      captureSessionToken: () => currentSession,
      isSessionTokenCurrent: (token: number) => token === currentSession,
    };
    let finishQueue: ((value: DownloadJob) => void) | undefined;
    vi.mocked(createJob)
      .mockImplementationOnce(() => new Promise((resolve) => { finishQueue = resolve; }))
      .mockResolvedValueOnce({ ...job, id: 'duplicate-job' });
    const { result } = renderHook(() => useMediaAcquisition({ workspace, onMessage: vi.fn() }));

    let oldQueue: Promise<DownloadJob | null> | undefined;
    act(() => { oldQueue = result.current.queue(remote); });
    await vi.waitFor(() => expect(finishQueue).toBeTypeOf('function'));
    currentSession = 2;
    act(() => { result.current.resetSession(); });

    expect(result.current.state.queueingSourceUrls).toEqual([]);
    expect(result.current.isQueueing(remote)).toBe(false);
    await act(async () => { expect(await result.current.queue(remote)).toBeNull(); });
    expect(createJob).toHaveBeenCalledTimes(1);

    await act(async () => {
      finishQueue?.(job);
      expect(await oldQueue).toBeNull();
    });
    expect(workspace.dispatch).not.toHaveBeenCalledWith(expect.objectContaining({ type: 'jobs/upsert' }));
  });

  it('rejects a missing source inside the feature without creating a partial selection', async () => {
    const workspace = acquisitionWorkspace();
    const { result } = renderHook(() => useMediaAcquisition({ workspace, onMessage: vi.fn() }));

    await act(async () => { await result.current.open({ title: 'Broken result' }); });

    expect(result.current.state.selection).toBeNull();
    expect(result.current.state.failure).toEqual({
      category: null,
      message: 'This result does not include a playable address.',
      phase: 'preview',
    });
    expect(previewUrl).not.toHaveBeenCalled();
  });

  it('guards saved and already-queued sources before inspection', async () => {
    const workspace = acquisitionWorkspace();
    const onMessage = vi.fn();
    const { result } = renderHook(() => useMediaAcquisition({ workspace, onMessage }));

    workspace.state.library = [{ id: 'saved-1', remote_id: remote.id, title: remote.title || 'Remote film', status: 'available', metadata_json: {} }];
    await act(async () => { expect(await result.current.queue(remote)).toBeNull(); });
    workspace.state.library = [];
    workspace.state.jobs = [{ ...job, status: 'running' }];
    await act(async () => { expect(await result.current.queue(remote)).toBeNull(); });

    expect(previewUrl).not.toHaveBeenCalled();
    expect(createJob).not.toHaveBeenCalled();
    expect(onMessage).toHaveBeenNthCalledWith(1, 'This video is already in your vault.');
    expect(onMessage).toHaveBeenNthCalledWith(2, 'This video is already in the download queue.');
  });

  it('keeps typed acquisition failures on the feature interface', async () => {
    const workspace = acquisitionWorkspace();
    vi.mocked(previewUrl).mockRejectedValueOnce(
      new ApiRequestError('No matching media format is available.', 400, null, 'format_unavailable'),
    );
    const { result } = renderHook(() => useMediaAcquisition({
      workspace,
      onMessage: vi.fn(),
    }));

    await act(async () => { await result.current.open(remote); });

    expect(result.current.state.failure).toEqual({
      category: 'format_unavailable',
      message: 'No matching media format is available.',
      phase: 'preview',
      retryItem: remote,
    });
    expect(result.current.state.previewing).toBe(false);
  });

  it('preserves the default video queue plan without redundant reinspection', async () => {
    const workspace = acquisitionWorkspace();
    const onMessage = vi.fn();
    const { result } = renderHook(() => useMediaAcquisition({ workspace, onMessage }));

    await act(async () => { await result.current.open(remote); });
    await act(async () => { await result.current.queueSelection(); });

    expect(previewUrl).toHaveBeenCalledTimes(1);
    expect(createJob).toHaveBeenCalledWith(expect.objectContaining({
      format_selection: expect.objectContaining({ preset: 'best', extract_audio: false, audio_format: null }),
    }));
    expect(onMessage).toHaveBeenCalledWith('Added to the download queue.');
  });

  it('queues the Editable (H.264) preset as a video download', async () => {
    expect(acquisitionFormatOptions.find(([value]) => value === 'best_editable')?.slice(1)).toEqual(['Editable (H.264)', 'Opens in QuickTime, Preview, and editing apps']);
    const { result } = renderHook(() => useMediaAcquisition({ workspace: acquisitionWorkspace(), onMessage: vi.fn() }));

    await act(async () => { await result.current.queue(remote, preview, 'best_editable'); });

    expect(createJob).toHaveBeenCalledWith(expect.objectContaining({
      format_selection: expect.objectContaining({ preset: 'best_editable', extract_audio: false, audio_format: null }),
    }));
  });

  it.each(['../outside', 'family/%name', 'family/videos\nprivate', 'family/videos\n', 'x'.repeat(241)])(
    'clears an unsafe programmatic folder %j before job creation',
    async (outputFolder) => {
    const workspace = acquisitionWorkspace();
    workspace.state.preferences.outputFolder = outputFolder;
    const { result } = renderHook(() => useMediaAcquisition({ workspace, onMessage: vi.fn() }));

    await act(async () => { await result.current.queue(remote); });

    expect(createJob).toHaveBeenCalledWith(expect.objectContaining({
      output_profile: expect.objectContaining({ subdir: '' }),
    }));
    },
  );

});
