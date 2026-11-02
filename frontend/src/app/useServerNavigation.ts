import { useCallback, useEffect, useState } from 'react';
import { getServerNavigation } from '../api';
import type { ServerNavigation } from '../types';
import type { Surface, VideoLibraryView } from './routes';

/** The server's enabled, member-visible video libraries in navigation order. */
export function videoLibraryViews(navigation: ServerNavigation | null | undefined): VideoLibraryView[] {
  const categories = navigation?.categories ?? [];
  return (['movies', 'shows', 'anime', 'youtube', 'recordings'] as const).filter((view) => categories.some((category) => category === (view === 'shows' ? 'tv' : view)));
}

export function isNavigationSurfaceAvailable(navigation: ServerNavigation | null | undefined, surface: Surface): boolean {
  if (surface === 'streaming' || surface === 'subscriptions') return Boolean(navigation?.features.streaming);
  if (surface === 'requests') return Boolean(navigation?.features.requests);
  if (surface === 'video' || surface === 'library') return Boolean(navigation?.categories.some((category) => ['movies', 'tv', 'anime', 'youtube', 'recordings'].includes(category)));
  if (surface === 'audio') return Boolean(navigation?.categories.includes('music'));
  if (surface === 'books' || surface === 'photos') return Boolean(navigation?.categories.includes(surface));
  return true;
}

export function useServerNavigation(member: string): { navigation: ServerNavigation | null; refresh: () => void } {
  const [state, setState] = useState<{ member: string; navigation: ServerNavigation | null }>({ member, navigation: null });
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    if (!member) { setState({ member, navigation: null }); return; }
    const controller = new AbortController();
    getServerNavigation({ signal: controller.signal }).then((navigation) => { if (!controller.signal.aborted) setState({ member, navigation }); }, () => {
      if (!controller.signal.aborted) setState({ member, navigation: null });
    });
    return () => controller.abort();
  }, [member, revision]);
  useEffect(() => {
    const foreground = () => { if (document.visibilityState === 'visible') refresh(); };
    const timer = member ? window.setInterval(foreground, 30_000) : null;
    document.addEventListener('visibilitychange', foreground);
    return () => { if (timer !== null) window.clearInterval(timer); document.removeEventListener('visibilitychange', foreground); };
  }, [member, refresh]);
  return { navigation: member && state.member === member ? state.navigation : null, refresh };
}
