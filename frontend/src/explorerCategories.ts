/**
 * The approved Explorer taxonomy. Keys are durable UI identifiers; queries are
 * intentionally focused prompts for Lumina's existing discovery search.
 */
export const EXPLORER_CATEGORIES = [
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
] as const;

export type ExplorerCategory = (typeof EXPLORER_CATEGORIES)[number];
