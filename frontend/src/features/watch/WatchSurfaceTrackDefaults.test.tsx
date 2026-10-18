import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useEffect, type ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { watchSurface } from '../../test/watchSurface';
import type { LibraryItem, LocalPlaybackOptions, PlaybackSessionRequest } from '../../types';
import type { CaptionPrefs } from './captionPrefs';

const options: LocalPlaybackOptions = { mode: 'direct', reason: null, facts: null, audio_tracks: [
  { index: 1, label: 'English audio', language: 'eng', codec: 'aac', channels: 2, default: true },
  { index: 4, label: 'Japanese audio', language: 'jpn', codec: 'aac', channels: 2, default: false },
] };
vi.mock('../../localPlayer', async (original) => ({
  ...(await original<typeof import('../../localPlayer')>()),
  LocalLibraryPlayer: ({ onOptions, request, tracks, extensions }: { onOptions?: (next: LocalPlaybackOptions) => void; request?: PlaybackSessionRequest; tracks?: Array<{ id: string; default?: boolean }>; extensions?: { menu?: ReactNode } }) => {
    useEffect(() => { onOptions?.(options); }, [onOptions]);
    return <div><output aria-label="Resolved audio">{request?.audio_index ?? 'default'}</output><output aria-label="Resolved subtitles">{tracks?.find((track) => track.default)?.id ?? 'off'}</output>{extensions?.menu}</div>;
  },
}));
vi.mock('../../api', async (original) => ({
  ...(await original<typeof import('../../api')>()),
  listSubtitleTracks: vi.fn().mockResolvedValue([
    { id: 'e:2', label: 'English subtitles', language: 'eng', origin: 'embedded', format: 'text', forced: true, hearing_impaired: false, default: false, url: '/api/library/l1/subtitle-tracks/e:2.vtt' },
    { id: 'e:5', label: 'French subtitles', language: 'fra', origin: 'embedded', format: 'text', forced: true, hearing_impaired: false, default: false, url: '/api/library/l1/subtitle-tracks/e:5.vtt' },
    { id: 'e:6', label: 'French SDH', language: 'fre', origin: 'embedded', format: 'text', forced: true, hearing_impaired: true, default: false, url: '/api/library/l1/subtitle-tracks/e:6.vtt' },
  ]),
  getMuteRanges: vi.fn().mockResolvedValue([]),
  getLiveDiscovery: vi.fn().mockResolvedValue({ items: [], hero: [] }),
}));

const item = { id: 'l1', title: 'Film', kind: 'video', status: 'available', duration: 600 } as LibraryItem;

describe('member track defaults in playback', () => {
  it('resolves ordered language aliases and forced SDH, then retains explicit audio and Off choices', async () => {
    const captions: CaptionPrefs = { size: 'medium', background: 'shadow', tracks: { audioLanguages: ['ja', 'en'], subtitleLanguages: ['fr', 'en'], subtitlesEnabled: true, forcedOnly: true, preferSdh: true } };
    const props = { selection: { kind: 'library' as const, item }, captions };
    const view = render(watchSurface(props));
    await waitFor(() => expect(screen.getByRole('status', { name: 'Resolved audio' }).textContent).toBe('4'));
    expect(screen.getByRole('status', { name: 'Resolved subtitles' }).textContent).toBe('e:6');
    await userEvent.click(screen.getByRole('button', { name: 'Playback settings' }));
    await userEvent.click(screen.getByRole('radio', { name: 'English audio' }));
    await userEvent.click(screen.getByRole('radio', { name: 'Off' }));
    view.rerender(watchSurface({ ...props, captions: { ...captions, tracks: { ...captions.tracks!, subtitleLanguages: ['en'] } } }));
    expect(screen.getByRole('status', { name: 'Resolved audio' }).textContent).toBe('1');
    expect(screen.getByRole('status', { name: 'Resolved subtitles' }).textContent).toBe('off');
  });
});
