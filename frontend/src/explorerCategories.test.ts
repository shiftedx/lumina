import { describe, expect, it } from 'vitest';

import { EXPLORER_CATEGORIES } from './explorerCategories';

describe('Explorer category catalog', () => {
  it('contains the complete approved 25-category taxonomy in its intentional order', () => {
    expect(EXPLORER_CATEGORIES).toEqual([
      { key: 'documentaries', label: 'Documentaries', query: 'documentary films' },
      { key: 'music', label: 'Music', query: 'music videos' },
      { key: 'live-performances', label: 'Live Performances', query: 'live music performances' },
      { key: 'gaming', label: 'Gaming', query: 'gaming' },
      { key: 'news', label: 'News', query: 'news' },
      { key: 'sports', label: 'Sports', query: 'sports highlights' },
      { key: 'film', label: 'Film', query: 'short films and movie trailers' },
      { key: 'comedy', label: 'Comedy', query: 'comedy' },
      { key: 'education', label: 'Education', query: 'educational videos' },
      { key: 'science-technology', label: 'Science & Technology', query: 'science and technology' },
      { key: 'podcasts', label: 'Podcasts', query: 'podcasts' },
      { key: 'cooking', label: 'Cooking', query: 'cooking recipes' },
      { key: 'travel', label: 'Travel', query: 'travel' },
      { key: 'diy-crafts', label: 'DIY & Crafts', query: 'DIY crafts' },
      { key: 'home-garden', label: 'Home & Garden', query: 'home and garden' },
      { key: 'cars', label: 'Cars', query: 'cars and automotive' },
      { key: 'fitness', label: 'Fitness', query: 'fitness workouts' },
      { key: 'fashion-beauty', label: 'Fashion & Beauty', query: 'fashion and beauty' },
      { key: 'nature', label: 'Nature', query: 'nature' },
      { key: 'history', label: 'History', query: 'history' },
      { key: 'business-finance', label: 'Business & Finance', query: 'business and finance' },
      { key: 'photography', label: 'Photography', query: 'photography' },
      { key: 'animation', label: 'Animation', query: 'animation' },
      { key: 'family', label: 'Family', query: 'family videos' },
      { key: 'culture', label: 'Culture', query: 'arts and culture' },
    ]);
  });

  it('uses one non-empty stable key and curated query for every category', () => {
    const keys = EXPLORER_CATEGORIES.map((category) => category.key);

    expect(new Set(keys)).toHaveLength(EXPLORER_CATEGORIES.length);
    expect(EXPLORER_CATEGORIES).toHaveLength(25);
    expect(EXPLORER_CATEGORIES.every((category) => category.label.trim().length > 0 && category.query.trim().length > 0)).toBe(true);
  });
});
