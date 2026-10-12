import { describe, expect, it } from 'vitest';

import {
  acquisitionPlanForSource,
  normalizeOutputFolder,
  outputFolderProblem,
  type AcquisitionDefaults,
} from './sourcePreferences';

const defaults: AcquisitionDefaults = {
  formatPreset: 'best',
  outputContainer: 'mp4',
  downloadSubtitles: true,
  outputFolder: 'family/videos',
};

describe('acquisition defaults', () => {
  it('turns global defaults into the request plan used for acquisition', () => {
    expect(acquisitionPlanForSource({
      ...defaults,
      formatPreset: 'audio_only',
      outputContainer: 'mkv',
      downloadSubtitles: false,
      outputFolder: 'concerts/live',
    })).toEqual({
      formatSelection: {
        preset: 'audio_only',
        custom_format: null,
        extract_audio: true,
        audio_format: 'mp3',
        embed_thumbnail: true,
        embed_metadata: true,
        subtitles: false,
        output_container: 'mkv',
      },
      outputProfile: {
        base_path: null,
        subdir: 'concerts/live',
        template: '%(title)s - %(uploader)s [%(id)s].%(ext)s',
        organize_by: 'downloads',
      },
    });
  });

  it('identifies folders that escape the server-controlled Library', () => {
    const unsafeFolders = [
      '../private',
      '/absolute/path',
      'C:\\private',
      'C:relative',
      '\\\\server\\share',
      'family/%(title)s',
      'family/videos\nprivate',
      'family/videos\n',
      `family/${String.fromCharCode(0)}private`,
      `family/${'x'.repeat(241)}`,
      `family/${String.fromCharCode(127)}private`,
    ];
    for (const folder of unsafeFolders) {
      expect(outputFolderProblem(folder)).not.toBeNull();
      expect(normalizeOutputFolder(folder)).toBe('');
      expect(acquisitionPlanForSource({ ...defaults, outputFolder: folder }).outputProfile.subdir).toBe('');
    }
    expect(outputFolderProblem('channels/mars')).toBeNull();
    expect(normalizeOutputFolder(' channels\\mars/./clips ')).toBe('channels/mars/clips');
  });
});
