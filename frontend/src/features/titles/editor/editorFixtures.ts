import type { FieldState, FieldSource, TitleMetadataDoc, TitleType } from '../../../types';

/** Test helpers only; never imported by app code. */
export const fieldState = (value: unknown, source: FieldSource | null = 'tmdb', patch: Partial<FieldState> = {}): FieldState => ({ value, source, locked: source === 'user', kept: null, ...patch });

export function docFixture(type: TitleType = 'series', patch: Partial<TitleMetadataDoc> = {}): TitleMetadataDoc {
  return {
    title_id: 't1', type, name: 'Severance', locked: false, parent: null,
    fields: { name: fieldState('Severance'), year: fieldState(2022), overview: fieldState('Work and life are split.'), genres: fieldState(['Drama']) },
    images: [{ type: 'Primary', index: 0, url: '/api/titles/t1/image/Primary', tag: 'abc', origin: 'tmdb', source: 'tmdb', locked: false, width: 1000, height: 1500 }],
    can_identify: true, tmdb_configured: true, history_count: 0,
    ...patch,
  };
}
