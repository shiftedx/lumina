import type {
  FormatSelection,
  OutputContainer,
  OutputProfile,
} from './types';

export type SourceFormatPreset = 'best' | 'best_1080p' | 'best_editable' | 'audio_only';

export type AcquisitionDefaults = {
  formatPreset: SourceFormatPreset;
  outputContainer: OutputContainer;
  downloadSubtitles: boolean;
  outputFolder: string;
};

export type SourceAcquisitionPlan = {
  formatSelection: FormatSelection;
  outputProfile: OutputProfile;
};

export function acquisitionPlanForSource(preferences: AcquisitionDefaults): SourceAcquisitionPlan {
  const audioOnly = preferences.formatPreset === 'audio_only';
  return {
    formatSelection: {
      preset: preferences.formatPreset,
      custom_format: null,
      extract_audio: audioOnly,
      audio_format: audioOnly ? 'mp3' : null,
      embed_thumbnail: true,
      embed_metadata: true,
      subtitles: preferences.downloadSubtitles,
      output_container: preferences.outputContainer,
    },
    outputProfile: {
      base_path: null,
      subdir: normalizeOutputFolder(preferences.outputFolder),
      template: '%(title)s - %(uploader)s [%(id)s].%(ext)s',
      organize_by: 'downloads',
    },
  };
}

export function normalizeOutputFolder(value: string): string {
  if (outputFolderProblem(value)) return '';
  const normalized = value.trim().replaceAll('\\', '/');
  return normalized.split('/').filter((segment) => segment && segment !== '.').join('/');
}

export function outputFolderProblem(value: string): string | null {
  if ([...value].some((character) => {
    const codePoint = character.codePointAt(0) ?? 0;
    return codePoint < 32 || (codePoint >= 127 && codePoint <= 159);
  })) {
    return 'Remove control characters and line breaks from the folder name.';
  }
  const normalized = value.trim().replaceAll('\\', '/');
  if (normalized.length > 240) {
    return 'Choose a folder name with no more than 240 characters.';
  }
  if (normalized.includes('%')) {
    return 'Folder names cannot contain a percent sign.';
  }
  if (normalized.startsWith('/') || /^[A-Za-z]:/.test(normalized)) {
    return 'Choose a relative folder rather than an absolute system path.';
  }
  if (normalized.split('/').includes('..')) {
    return 'Choose a folder that stays inside the Library.';
  }
  return null;
}
