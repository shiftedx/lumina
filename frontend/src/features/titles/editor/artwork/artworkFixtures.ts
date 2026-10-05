import type { ArtworkTabDoc } from './artworkModel';
import type { EditableImageType, TitleImageEntry, TitleType } from '../../../../types';

export const image = (type: EditableImageType, index: number, patch: Partial<TitleImageEntry> = {}): TitleImageEntry => ({
  type,
  index,
  url: `/api/titles/t1/images/${type}?tag=tag-${type}-${index}`,
  tag: `tag-${type}-${index}`,
  origin: 'tmdb',
  source: 'tmdb',
  locked: false,
  width: 2000,
  height: 3000,
  ...patch,
});

export const artworkDoc = (type: TitleType = 'movie', images: TitleImageEntry[] = [], patch: Partial<ArtworkTabDoc> = {}): ArtworkTabDoc => ({
  title_id: 't1',
  type,
  locked: false,
  images,
  tmdb_configured: true,
  ...patch,
});
