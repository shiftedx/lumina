export type PhotosRoute = { surface: 'photos'; view: 'timeline' | 'albums' | 'people' | 'trash'; q?: string; favorite?: boolean; albumId?: string; personId?: string; sort?: 'captured' | 'added'; camera?: string; dateFrom?: string; dateTo?: string };
const ID = /^[A-Za-z0-9_-]{1,64}$/;
export function parsePhotosRoute(path: string, params: URLSearchParams): PhotosRoute | null {
  if (path !== '/photos' && !path.startsWith('/photos/')) return null;
  const view = path === '/photos/albums' ? 'albums' : path === '/photos/people' ? 'people' : path === '/photos/trash' ? 'trash' : 'timeline';
  const q = (params.get('q') ?? '').trim().slice(0, 200);
  const camera = (params.get('camera') ?? '').trim().slice(0, 200);
  const dateFrom = params.get('from') ?? ''; const dateTo = params.get('to') ?? '';
  const validDate = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
  const albumId = params.get('album') ?? ''; const personId = params.get('person') ?? '';
  return { surface: 'photos', view, ...(q ? { q } : {}), ...(camera ? { camera } : {}), ...(validDate(dateFrom) ? { dateFrom } : {}), ...(validDate(dateTo) ? { dateTo } : {}), ...(params.get('favorite') === 'true' ? { favorite: true } : {}), ...(ID.test(albumId) ? { albumId } : {}), ...(ID.test(personId) ? { personId } : {}), ...(params.get('sort') === 'added' ? { sort: 'added' } : {}) };
}
export function photosRoutePath(route: PhotosRoute): string {
  const query = new URLSearchParams();
  if (route.q) query.set('q', route.q);
  if (route.camera) query.set('camera', route.camera);
  if (route.dateFrom) query.set('from', route.dateFrom);
  if (route.dateTo) query.set('to', route.dateTo);
  if (route.favorite) query.set('favorite', 'true');
  if (route.albumId) query.set('album', route.albumId);
  if (route.personId) query.set('person', route.personId);
  if (route.sort === 'added') query.set('sort', 'added');
  return `/photos${route.view === 'timeline' ? '' : `/${route.view}`}${query.size ? `?${query}` : ''}`;
}
