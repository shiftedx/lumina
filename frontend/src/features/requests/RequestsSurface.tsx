/**
 * Requests: discover films, series and anime, watch trailers inline, and ask the
 * household's Sonarr/Radarr for them. Pages: Discover, Films, Series, Anime, search, a title, My requests and Manage (admins).
 */
import { Search } from 'lucide-react';
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { type AppRoute, parseRoute, type RequestsRoute } from '../../app/routes';
import type { UserProfile } from '../../types';
import { Masthead } from '../../ui';
import { moveFocus } from '../media/focusNav';
import { AnimePage } from './AnimePage';
import { Browse, SearchResults } from './Browse';
import { Discover } from './Discover';
import { type CatalogItem, type CatalogStatus, getQuotas, type Quota } from './requestsApi';
import { RequestsContext, type RequestsActions, type SessionGate } from './requestsContext';
import { catalogId } from './requestsModel';
import { Manage, MyRequests } from './RequestLists';
import { RequestSheet } from './RequestSheet';
import { playTrailerOnOpen, TitlePage } from './TitlePage';
import './requests.css';

export type RequestsSurfaceProps = {
  route: RequestsRoute;
  user: UserProfile;
  session: SessionGate;
  /** Another Requests page; `replace` rewrites the current history entry instead of adding one. */
  onRoute: (route: RequestsRoute, replace?: boolean) => void;
  /** Anywhere else in the app (a Library title, Settings). */
  onOpenRoute: (route: AppRoute) => void;
  onQueueChanged?: () => void;
};

const SEARCH_DEBOUNCE_MS = 350;

export function RequestsSurface({ route, user, session, onRoute, onOpenRoute, onQueueChanged }: RequestsSurfaceProps) {
  const isAdmin = user.role === 'admin';
  const [overrides, setOverrides] = useState<Record<string, CatalogStatus>>({});
  const [asking, setAsking] = useState<CatalogItem | null>(null);
  const [quotas, setQuotas] = useState<Quota[] | null>(null);
  const loadQuotas = useCallback(() => { getQuotas().then((result) => setQuotas(result.quotas), () => setQuotas(null)); }, []);

  const actions = useMemo<RequestsActions>(() => ({
    user, session, isAdmin, onQueueChanged: onQueueChanged ?? (() => undefined),
    go: (next, replace) => { if (!replace) window.scrollTo({ top: 0 }); onRoute(next, replace); },
    openTitle: (item, options) => {
      const id = catalogId(item);
      if (!id) return;
      if (options?.trailer) playTrailerOnOpen(item.key);
      window.scrollTo({ top: 0 });
      onRoute({ surface: 'requests', view: 'title', kind: item.kind, id });
    },
    openLibrary: (titleId) => onOpenRoute({ surface: 'library', titleId }),
    openSettings: () => onOpenRoute(parseRoute('/settings/requests', '')),
    ask: (item) => { setAsking(item); loadQuotas(); },
    statusOf: (item) => overrides[item.key] ?? item.status,
  }), [user, session, isAdmin, onQueueChanged, onRoute, onOpenRoute, overrides, loadQuotas]);

  const setStatus = useCallback((key: string, status: CatalogStatus | null) => setOverrides((current) => {
    const next = { ...current };
    if (status) next[key] = status; else delete next[key];
    return next;
  }), []);

  // Search: debounced and URL-synced; typing rewrites one history entry, Enter goes at once.
  const query = route.view === 'search' ? route.query : '';
  const [draft, setDraft] = useState(query);
  useEffect(() => setDraft(query), [query]);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const search = (text: string, replace: boolean) => {
    const value = text.trim();
    if (value && value !== query) onRoute({ surface: 'requests', view: 'search', query: value }, replace && route.view === 'search');
  };
  const onDraft = (text: string) => {
    setDraft(text);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => search(text, true), SEARCH_DEBOUNCE_MS);
  };
  const submit = (event: FormEvent) => { event.preventDefault(); window.clearTimeout(timer.current); search(draft, false); };

  const tabs: Array<{ view: RequestsRoute['view']; label: string }> = [
    { view: 'discover', label: 'Discover' }, { view: 'movies', label: 'Films' }, { view: 'shows', label: 'Series' }, { view: 'anime', label: 'Anime' },
    { view: 'mine', label: 'My requests' }, ...(isAdmin ? [{ view: 'manage' as const, label: 'Manage' }] : []),
  ];

  let body;
  if (route.view === 'title') body = <TitlePage id={route.id} key={`${route.kind}:${route.id}`} kind={route.kind} />;
  else if (route.view === 'search') body = <SearchResults query={route.query} />;
  else if (route.view === 'movies' || route.view === 'shows') body = <Browse genre={route.genre} section={route.section} view={route.view} />;
  else if (route.view === 'anime') body = <AnimePage genre={route.genre} section={route.section} />;
  else if (route.view === 'mine') body = <MyRequests />;
  else if (route.view === 'manage') body = <Manage />;
  else body = <Discover />;

  const quota = asking ? quotas?.find((entry) => entry.kind === asking.kind) ?? null : null;
  return (
    <RequestsContext.Provider value={actions}>
      <div className={`surface gallery rq-surface is-${route.view}`} onKeyDown={(event) => moveFocus(event)}>
        {route.view === 'title' ? null : (
          <>
            <Masthead
              actions={(
                <form className="g-search" onSubmit={submit} role="search">
                  <Search aria-hidden="true" />
                  <label className="sr-only" htmlFor="rq-search">Search films, series and anime</label>
                  <input className="g-input" id="rq-search" onChange={(event) => onDraft(event.currentTarget.value)} placeholder="Search films, series and anime" type="search" value={draft} />
                </form>
              )}
              title={route.view === 'search' ? `Results for “${route.query}”` : 'Requests'}
            />
            <nav aria-label="Requests pages" className="rq-tabs">
              {tabs.map((tab) => (
                <button aria-current={tab.view === route.view ? 'page' : undefined} key={tab.view} onClick={() => actions.go({ surface: 'requests', view: tab.view } as RequestsRoute)} type="button">{tab.label}</button>
              ))}
            </nav>
          </>
        )}
        {body}
      </div>
      {asking ? <RequestSheet item={asking} key={asking.key} onClose={() => setAsking(null)} onRequested={() => { loadQuotas(); onQueueChanged?.(); }} onStatus={setStatus} quota={quota} userId={user.id} /> : null}
    </RequestsContext.Provider>
  );
}
