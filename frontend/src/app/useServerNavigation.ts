import { useCallback, useEffect, useState } from 'react';
import { getServerNavigation } from '../api';
import type { ServerNavigation } from '../types';
import type { Surface, VideoLibraryView } from './routes';
import { usePolledSnapshot } from './usePolledSnapshot';

/** The server's enabled, member-visible video libraries in navigation order. */
export function videoLibraryViews(navigation: ServerNavigation | null | undefined): VideoLibraryView[] {
  const categories = navigation?.categories ?? [];
  return (['movies', 'shows', 'anime', 'youtube', 'recordings'] as const).filter((view) => categories.some((category) => category === (view === 'shows' ? 'tv' : view)));
}

export function isNavigationSurfaceAvailable(navigation: ServerNavigation | null | undefined, surface: Surface): boolean {
  if (surface === 'streaming') return Boolean(navigation?.features.streaming || navigation?.categories.includes('youtube'));
  if (surface === 'subscriptions') return Boolean(navigation?.features.streaming);
  if (surface === 'requests') return Boolean(navigation?.features.requests);
  if (surface === 'video' || surface === 'library') return Boolean(navigation?.categories.some((category) => ['movies', 'tv', 'anime', 'youtube', 'recordings'].includes(category)));
  if (surface === 'audio') return Boolean(navigation?.categories.includes('music'));
  if (surface === 'books' || surface === 'photos') return Boolean(navigation?.categories.includes(surface));
  return true;
}

export function useServerNavigation(member: string): { navigation: ServerNavigation | null; refresh: () => void } {
  const [state, setState] = useState<{ member: string; navigation: ServerNavigation | null }>({ member, navigation: null });
  const session = useCallback(() => 0, []);
  const isSessionCurrent = useCallback(() => Boolean(member), [member]);
  const refreshSnapshot = usePolledSnapshot(member || null, {
    captureSessionToken: session,
    isSessionTokenCurrent: isSessionCurrent,
  }, (isCurrent, signal) => getServerNavigation({ signal }).then((navigation) => {
    if (isCurrent()) setState({ member, navigation });
  }, () => {
    if (isCurrent()) setState({ member, navigation: null });
  }), 30_000, true);
  useEffect(() => { if (!member) setState({ member, navigation: null }); }, [member]);
  const refresh = useCallback(() => refreshSnapshot(), [refreshSnapshot]);
  return { navigation: member && state.member === member ? state.navigation : null, refresh };
}
