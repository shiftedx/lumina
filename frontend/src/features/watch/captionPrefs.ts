/** Caption style preferences, stored in ui_prefs.captions. */
export type CaptionSize = 'small' | 'medium' | 'large' | 'xlarge';
export type CaptionBackground = 'none' | 'shadow' | 'box' | 'solid';
export interface CaptionPrefs { size: CaptionSize; background: CaptionBackground }

export const DEFAULT_CAPTIONS: CaptionPrefs = { size: 'medium', background: 'shadow' };
export const CAPTION_SIZES: readonly { value: CaptionSize; label: string; scale: number }[] = [
  { value: 'small', label: 'Small', scale: 0.8 },
  { value: 'medium', label: 'Medium', scale: 1 },
  { value: 'large', label: 'Large', scale: 1.3 },
  { value: 'xlarge', label: 'Extra large', scale: 1.6 },
];
export const CAPTION_BACKGROUNDS: readonly { value: CaptionBackground; label: string }[] = [
  { value: 'none', label: 'None' },
  { value: 'shadow', label: 'Shadow' },
  { value: 'box', label: 'Box' },
  { value: 'solid', label: 'Solid' },
];

export function captionPrefsFromUiPrefs(uiPrefs: Record<string, unknown> | null | undefined): CaptionPrefs {
  const stored = uiPrefs?.captions;
  if (!stored || typeof stored !== 'object' || Array.isArray(stored)) return DEFAULT_CAPTIONS;
  const { size, background } = stored as Record<string, unknown>;
  return {
    size: CAPTION_SIZES.find((option) => option.value === size)?.value ?? DEFAULT_CAPTIONS.size,
    background: CAPTION_BACKGROUNDS.find((option) => option.value === background)?.value ?? DEFAULT_CAPTIONS.background,
  };
}

export function captionStyle(prefs: CaptionPrefs): Record<'--caption-scale' | '--caption-bg' | '--caption-shadow', string> {
  return {
    '--caption-scale': String(CAPTION_SIZES.find((option) => option.value === prefs.size)?.scale ?? 1),
    '--caption-bg': prefs.background === 'box' ? 'var(--g-caption-box)' : prefs.background === 'solid' ? 'var(--g-caption-solid)' : 'transparent',
    '--caption-shadow': prefs.background === 'shadow' ? 'var(--g-caption-shadow)' : 'none',
  };
}
