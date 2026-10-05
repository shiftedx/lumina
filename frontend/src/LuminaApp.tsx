import { WatchFullscreenHost } from './fullscreenHost';
import { AccessProvider, allStreamingBlocked, BlockedSurface, providerBlocked, TimeLeftNotice, useMyAccess, watchGate, WatchStopState } from './features/access/access';
import { StreamingProvidersProvider, type OptionalProvider } from './features/streaming/providers';
import { SurfaceBoundary } from './app/SurfaceBoundary';
import { type ComponentType, type ReactNode, lazy, Suspense, useCallback, useEffect, useInsertionEffect, useMemo, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { ApiRequestError, keepaliveWritesSettled, updateDisplayName, clearSearchHistory, deleteSearchHistoryEntry, getSearchHistory, recordSearchHistory, redeemInvitation, redeemPasswordReset, cancelJob, clearCompletedJobs, clearPlaybackProgress, completeOnboarding, createAutomation, createInitialAdmin, createLibraryTag, deleteAutomation, deleteLibraryTag, getChannelSuggestions, getHomeRecommendations, getLibraryItem, getLiveDiscovery, getMemberInterests, getPlaybackProgress, getPopularDiscovery, getSuppressions, hideContinueWatching, listAutomations, listLibraryTags, loginSession, logoutSession, pauseAutomation, restoreSuppression, resumeAutomation, retryAcquisitionEntry, retryJob, runAutomation, searchChannels, searchLibrary, setAutomationAutoDownload, sourceSearch, skipOnboarding, suppressRecommendation, unhideContinueWatching, updateLibraryItemVisibility, updateMemberInterests, updateMySettings, updatePlaybackProgress } from './api';
import { type SearchHistoryEntry, type CategoryChannelSuggestions, type ChannelCandidate, type DownloadJob, type FollowChannelRequest, type LibraryItem, type LiveSnapshot, type SearchSourceError, type WatchQueueEntry, type MemberInterests, type MemberOnboardingState, type MemberRecommendationSnapshot, type OnboardingStatus, type PlaybackProgress, type PopularSnapshot, type RemotePlaybackCachePreferences, type SourceAutomation, type SuppressRecommendationInput, type SuppressionList, type TitleCategory, type TitleSummary, type TitleType, type UserProfile, type UserTag, type YouTubeSearchResult } from './types';
import { flushRecoEvents, recordRecoOpen, resetRecoEvents } from './features/reco/recoEvents';
import { RecoFeedbackProvider } from './features/reco/recoFeedback';
import { ArtActionsProvider } from './features/gallery/ArtMenu';
import { followHadSuppression, type RecoFeedback, type RecoTarget, relatedDropPredicate } from './features/reco/recoModel';
import { classifySessionLoadProblem, type CollectionLoadProblem, type CollectionLoadState, type PlaybackPrefs, useAuthenticatedWorkspace } from './workspace';
import { isUrl, readString, withHistoryEntry } from './luminaModel';
import { createSessionGuardedChannelSubscriptionCommands, findChannelBySource, normalizeChannelAddress, resolveChannelAddressCandidate } from './channelSubscriptions';
import { LibraryCurationControls } from './LibraryCurationControls';
import { SourceSettings } from './SourceSettings';
import { type AcquisitionDefaults, acquisitionPlanForSource } from './sourcePreferences';
import { CollectionsPage, type CollectionsRoute } from './features/collections/HouseholdCollections';
import { AcquisitionBatchActivity, PlaylistAcquisitionPanel } from './features/downloads/PlaylistAcquisition';
import { remoteSourceIdentity } from './playbackModel';
import { buildAcquisitionFormatSelection, useMediaAcquisition } from './mediaAcquisition';
import { useSubscriptionAcquisitionFeed } from './subscriptionFeed';
import { applyTheme } from './theme';
import { type AppRoute, type RequestsRoute, type StreamingProvider, type StreamingView, type ChannelPageTab, type EditorTab, libraryBackRoute, parseRoute, routeFocusKey, routePath, type SettingsSectionId, type Surface } from './app/routes';
import { AppShell } from './app/AppShell';
import { onCommand, openMemberPicker, PALETTE_EVENT, type PaletteMode, type PaletteRequest } from './app/commands';
import type { PaletteContext } from './features/palette/actions';
import { Skeleton, useToast } from './ui';
import { usePolledSnapshot } from './app/usePolledSnapshot';
import { HomeSurface } from './features/home/HomeSurface';
import { forgetHomeCache } from './features/home/homeCache';
import { normalizeHomeShelves, type HomeShelfPref } from './features/home/homeShelves';
import { DownloadsSurface, LiveRecordingActivity } from './features/downloads/DownloadsSurface';
import { StreamingSurface } from './features/streaming/StreamingSurface';
import { ChannelsSurface } from './features/channels/ChannelsSurface';
import { ChannelPage } from './features/channels/ChannelPage';
import { ChannelResolver } from './features/channels/ChannelResolver';
import { clearAlbumQueue } from './features/gallery/albumQueue';
import { forgetAllStore } from './features/gallery/allStore';
import { forgetStoredSections, type LibraryPlace } from './features/gallery/LibraryTabs';
import { LENS_LABELS, type LibraryLens } from './features/gallery/libraryLens';
import { forgetWallStores } from './features/gallery/wallPages';
import { LibraryBrowser } from './features/library/LibraryBrowser';
import { confirmLeaveSettings } from './features/settings/unsavedChanges';
import { titleDestination } from './features/titles/titleModel';
import { listRequests } from './features/requests/requestsApi';
import { cachedTitle, forgetTitles, rememberSummary, summaryFor } from './features/gallery/titleCache';
import type { WatchSelection } from './features/watch/WatchSurface';
import { queuedAsRemote, resetWatchQueue } from './features/watch/WatchQueue';
import { holdBackground } from './backgroundGate';
import { markNavigation, notePlayIntent, takePlayIntent } from './perfMetrics';
import { cancelSpeculativeStart, claimSpeculativeStart, forgetPlaybackWarmup, prefetchPlaybackOptions } from './playbackPrefetch';
import { AuthScreen, readAccountLink } from './features/auth/AuthScreen';
import { resetDeviceRing, ringCall } from './features/auth/deviceRing';
import { MemberPickerHost } from './features/auth/memberPicker';

// Heavy, non-landing surfaces load on first use so the player stack (hls.js,
// dash.js) and settings/onboarding code stay out of the initial bundle.
// React.lazy suspends on first render even when the chunk is already fetched, and React then
// holds the fallback for ~300ms; a surface preloaded at idle renders synchronously instead.
function preloadableSurface<P extends object>(load: () => Promise<ComponentType<P>>) {
  let loaded: ComponentType<P> | null = null;
  const preload = () => load().then((component) => { loaded = component; return component; });
  const Lazy = lazy(() => preload().then((component) => ({ default: component })));
  const Surface = (props: P) => { const Loaded = loaded; return Loaded ? <Loaded {...props} /> : <Lazy {...props} />; };
  return Object.assign(Surface, { preload: () => { void preload().catch(() => undefined); } });
}
const WatchSurface = preloadableSurface(() => import('./features/watch/WatchSurface').then((module) => module.WatchSurface));
// A watch link opened cold loads its chunk beside the sign-in requests, so the route renders without a Suspense fallback.
if (typeof window !== 'undefined' && window.location.pathname.startsWith('/watch')) WatchSurface.preload();
const SettingsSurface = preloadableSurface(() => import('./features/settings/SettingsSurface').then((module) => module.SettingsSurface));
const OnboardingSurface = lazy(() => import('./features/onboarding/Onboarding').then((module) => ({ default: module.OnboardingSurface })));
const ChannelDiscoveryStep = lazy(() => import('./features/onboarding/Onboarding').then((module) => ({ default: module.ChannelDiscoveryStep })));
const TitleEditorPage = preloadableSurface(() => import('./features/titles/editor/EditorPage').then((module) => module.EditorPage));
const RequestsSurface = preloadableSurface(() => import('./features/requests/RequestsSurface').then((module) => module.RequestsSurface));
const TitleDetailPage = preloadableSurface(() => import('./features/gallery/GalleryTitlePage').then((module) => module.GalleryTitlePage));
/** After the landing surface settles: the next likely surfaces, so opening one never shows the Suspense fallback. */
/** Toasts a shell error. With `clear` the error is cleared once toasted so identical text toasts again; without it the error stays (inline surfaces keep reading it) and `identity` marks a new occurrence. */
export function useShellErrorToast(error: string | null, clear?: () => void, identity?: unknown): void {
  const toast = useToast();
  const clearRef = useRef(clear);
  clearRef.current = clear;
  useEffect(() => {
    if (!error) return;
    toast({ tone: 'error', message: error });
    clearRef.current?.();
  }, [error, identity, toast]);
}

export function preloadLikelySurfaces(): void {
  WatchSurface.preload();
  SettingsSurface.preload();
  TitleDetailPage.preload();
}
const surfaceLoading = <div className="g-route-loading"><div aria-hidden="true" className="g-route-loading-masthead" /><Skeleton count={3} label="Loading…" shape="row" /></div>;
const CommandPalette = preloadableSurface(() => import('./features/palette/CommandPalette').then((module) => module.default));

/** Where inside Library the member is: a lens (All, Movies, Shows, …) with its wall query, or one title page. */
/** Where inside Library the member is: a lens with its wall query (none: All), or one title page. */
type LibraryLocation = { view?: LibraryLens; wall?: string; titleId?: string; season?: number; edit?: boolean; tab?: EditorTab; collections?: 'list' | 'detail'; collectionId?: string; create?: 'collection' | 'smart' };
function libraryLocationOf(route: AppRoute): LibraryLocation {
  if (route.surface !== 'library') return {};
  if ('titleId' in route && 'edit' in route) return route.tab ? { titleId: route.titleId, edit: true, tab: route.tab } : { titleId: route.titleId, edit: true };
  if ('titleId' in route) return route.season === undefined ? { titleId: route.titleId } : { titleId: route.titleId, season: route.season };
  if ('collections' in route) {
    if (route.collections === 'detail') return { collections: 'detail', collectionId: route.collectionId };
    return route.create ? { collections: 'list', create: route.create } : { collections: 'list' };
  }
  if (!('view' in route)) return {};
  return route.wall ? { view: route.view, wall: route.wall } : { view: route.view };
}



export function collectionLoadAnnouncement(label: string, state: CollectionLoadState, retrying = false): string {
  if (retrying) return `Retrying ${label}.`;
  switch (state) {
    case 'loading': return `Loading ${label}.`;
    case 'empty': return `${label} loaded with no items.`;
    case 'ready': return `${label} loaded.`;
    case 'stale': return `${label} is showing last-known data while recovery is available.`;
    case 'offline': return `${label} is offline. A retry is available.`;
    case 'failed': return `${label} failed to load. A recovery action is available.`;
  }
}

export function CollectionAnnouncer({ libraryState, libraryRetrying, jobsState, jobsRetrying }: {
  libraryState: CollectionLoadState;
  libraryRetrying: boolean;
  jobsState: CollectionLoadState;
  jobsRetrying: boolean;
}) {
  const [announcement, setAnnouncement] = useState('');
  const previousRef = useRef<{ library: string; jobs: string } | null>(null);
  useEffect(() => {
    const next = {
      library: collectionLoadAnnouncement('Library', libraryState, libraryRetrying),
      jobs: collectionLoadAnnouncement('Downloads', jobsState, jobsRetrying),
    };
    const previous = previousRef.current;
    previousRef.current = next;
    const changes = previous
      ? [previous.library !== next.library ? next.library : null, previous.jobs !== next.jobs ? next.jobs : null].filter(Boolean)
      : [next.library, next.jobs];
    if (changes.length) setAnnouncement(changes.join(' '));
  }, [jobsRetrying, jobsState, libraryRetrying, libraryState]);
  return <div aria-atomic="true" aria-live="polite" className="sr-only" role="status">{announcement}</div>;
}

// Returns a referentially stable wrapper that always calls the latest callback,
// so handlers closing over changing state can be passed to memoized rows without
// defeating their memoization. The latest callback is captured in an insertion
// effect (the useEvent RFC pattern) rather than during render, so a discarded
// concurrent render can never leave a stale callback behind. The wrapper is only
// invoked from event handlers, after the effect has committed.
export function useStableCallback<A extends unknown[], R>(callback: (...args: A) => R): (...args: A) => R {
  const ref = useRef(callback);
  useInsertionEffect(() => {
    ref.current = callback;
  });
  return useCallback((...args: A) => ref.current(...args), []);
}


function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setMatches(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, [query]);
  return matches;
}


// Explore asks for one page, then "More results" asks once for the server maximum.
const EXPLORE_PAGE = 12;
const EXPLORE_MAX = 24;
type ExploreResults = { items: YouTubeSearchResult[]; errors: SearchSourceError[]; library: LibraryItem[]; titles: TitleSummary[]; limit: number };
const NO_EXPLORE_RESULTS: ExploreResults = { items: [], errors: [], library: [], titles: [], limit: EXPLORE_PAGE };

export default function LuminaApp() {
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const workspace = useAuthenticatedWorkspace({ onError: setError });
  const { state } = workspace;
  const accessValue = useMyAccess(state.currentUser?.id, setMessage);
  const { access } = accessValue;
  const openSearchBlocked = providerBlocked(access, 'open_search');
  const [authBusy, setAuthBusy] = useState(false);
  const [accountLink, setAccountLink] = useState(readAccountLink);
  useEffect(() => { if (readAccountLink()) window.history.replaceState(null, '', window.location.pathname + window.location.search); }, []);
  const signedInRole = state.currentUser?.role;
  useEffect(() => {
    if (!signedInRole) return undefined;
    // After the landing surface settles, fetch the next likely surfaces so opening them never waits on a chunk.
    // The Watch chunk goes at once: a Play inside the first second or two used to wait on it and then on React's ~300 ms fallback hold.
    WatchSurface.preload();
    const timer = window.setTimeout(preloadLikelySurfaces, 1500);
    return () => window.clearTimeout(timer);
  }, [signedInRole]);
  // The browser location is read once at startup; afterwards app state is the
  // single source of truth and the URL follows it (popstate re-applies a route).
  const [initialRoute] = useState(() => parseRoute(window.location.pathname, window.location.search));
  const pendingRouteRef = useRef<AppRoute | null>(initialRoute.surface === 'watch' || (initialRoute.surface === 'streaming' && initialRoute.query) ? initialRoute : null);
  const [surface, setSurface] = useState<Surface>(initialRoute.surface);
  const [channelId, setChannelId] = useState<string | null>('channelId' in initialRoute ? initialRoute.channelId : null);
  const [channelPage, setChannelPage] = useState<{ id: string; tab: ChannelPageTab } | { url: string } | null>(
    'youtubeChannelId' in initialRoute ? { id: initialRoute.youtubeChannelId, tab: initialRoute.tab ?? 'videos' } : 'channelUrl' in initialRoute ? { url: initialRoute.channelUrl } : null,
  );
  const [streamingView, setStreamingView] = useState<StreamingView>(initialRoute.surface === 'streaming' ? initialRoute.view : 'home');
  const [streamingProvider, setStreamingProvider] = useState<StreamingProvider>(initialRoute.surface === 'streaming' ? initialRoute.provider ?? 'youtube' : 'youtube');
  const [exploreRail, setExploreRail] = useState<string | null>(initialRoute.surface === 'streaming' && initialRoute.view === 'home' ? initialRoute.rail ?? null : null);
  const [liveGeneration, setLiveGeneration] = useState(0);
  const [liveRail, setLiveRail] = useState<string | null>(initialRoute.surface === 'streaming' && initialRoute.view === 'live' ? initialRoute.rail ?? null : null);
  const [previousSurface, setPreviousSurface] = useState<Surface>('home');
  const [settingsSection, setSettingsSection] = useState<SettingsSectionId | null>(initialRoute.surface === 'settings' ? initialRoute.section ?? null : null);
  const [settingsMember, setSettingsMember] = useState<string | null>(initialRoute.surface === 'settings' && 'memberId' in initialRoute ? initialRoute.memberId : null);
  const [requestsRoute, setRequestsRoute] = useState<RequestsRoute>(initialRoute.surface === 'requests' ? initialRoute : { surface: 'requests', view: 'discover' });
  const [pendingRequests, setPendingRequests] = useState(0);
  const [requestsQueueGeneration, setRequestsQueueGeneration] = useState(0);
  const [libraryLocation, setLibraryLocation] = useState<LibraryLocation>(() => libraryLocationOf(initialRoute));
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const isMobile = useMediaQuery('(max-width: 680px)');
  // The sidebar is a drawer below 960px, the tab bar below 680px.
  const isDrawer = useMediaQuery('(max-width: 960px)');
  const isCompact = useMediaQuery('(max-width: 379px)');
  const toast = useToast();
  const [palette, setPalette] = useState<{ open: boolean; mode: PaletteMode; key: number }>({ open: false, mode: 'search', key: 0 });
  const [searchHistory, setSearchHistory] = useState<SearchHistoryEntry[]>([]);
  const [exploreError, setExploreError] = useState<string | null>(null);
  const [exploreQuery, setExploreQuery] = useState('');
  const [exploreResults, setExploreResults] = useState<ExploreResults>(NO_EXPLORE_RESULTS);
  const [exploreLoading, setExploreLoading] = useState(false);
  const exploreRequestRef = useRef(0);
  const [popularDiscovery, setPopularDiscovery] = useState<PopularSnapshot | null>(null);
  const [popularDiscoveryError, setPopularDiscoveryError] = useState<string | null>(null);
  const [liveDiscovery, setLiveDiscovery] = useState<LiveSnapshot | null>(null);
  const [liveDiscoveryError, setLiveDiscoveryError] = useState<string | null>(null);
  const [memberInterests, setMemberInterests] = useState<MemberInterests | null>(null);
  const [homeRecommendations, setHomeRecommendations] = useState<MemberRecommendationSnapshot | null>(null);
  const [homeRecommendationsError, setHomeRecommendationsError] = useState<string | null>(null);
  const [homeRecommendationsLoading, setHomeRecommendationsLoading] = useState(false);
  const [suppressions, setSuppressions] = useState<SuppressionList>({ items: [], channels: [] });
  const [recoEpoch, setRecoEpoch] = useState(0);
  const [onboarding, setOnboarding] = useState<{ phase: 'setup' | 'discover' | 'preparing'; keys: string[] } | null>(null);
  const [channelSuggestions, setChannelSuggestions] = useState<CategoryChannelSuggestions[]>([]);
  const [channelSuggestionsLoading, setChannelSuggestionsLoading] = useState(false);
  const [channelSuggestionsError, setChannelSuggestionsError] = useState<string | null>(null);
  const [onboardingError, setOnboardingError] = useState<string | null>(null);
  const prefersReducedMotion = useMediaQuery('(prefers-reduced-motion: reduce)');
  const prefersLightScheme = useMediaQuery('(prefers-color-scheme: light)');
  useEffect(() => applyTheme(state.preferences.theme, prefersLightScheme), [state.preferences.theme, prefersLightScheme]);
  const [homeRecommendationRefreshGeneration, setHomeRecommendationRefreshGeneration] = useState(0);
  const [subscriptionRefreshGeneration, setSubscriptionRefreshGeneration] = useState(0);
  const [batchRefreshGeneration, setBatchRefreshGeneration] = useState(0);
  const [libraryWatchItem, setLibraryWatchItem] = useState<LibraryItem | null>(null);
  const [downloadMenuOpen, setDownloadMenuOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [activePlayback, setActivePlayback] = useState<PlaybackProgress | null>(null);
  const [playbackLoading, setPlaybackLoading] = useState(false);
  const [watchStart, setWatchStart] = useState<{ libraryId: string; seconds: number; nonce: number } | null>(null); // the watch link's `t`
  const [curationTags, setCurationTags] = useState<UserTag[]>([]);
  const [curationBusy, setCurationBusy] = useState(false);
  const [curationLoading, setCurationLoading] = useState(false);
  const [curationError, setCurationError] = useState<string | null>(null);
  const [curationDataItemId, setCurationDataItemId] = useState<string | null>(null);
  const curationMutationGenerationRef = useRef(0);
  const playbackLoadRequestRef = useRef(0);
  const playbackMutationRef = useRef<Promise<void>>(Promise.resolve());
  const activeLibraryItemIdRef = useRef<string | null>(null);
  const appMainRef = useRef<HTMLElement>(null);
  const mobileMenuButtonRef = useRef<HTMLButtonElement>(null);
  const authFocusRequestedRef = useRef(false);
  const [twoFactorChallenge, setTwoFactorChallenge] = useState<string | null>(null);
  const restoreDrawerTriggerRef = useRef(false);
  const subscriptionRetryFocusRequestedRef = useRef(false);

  const closeMobileNavigation = useCallback(() => {
    restoreDrawerTriggerRef.current = true;
    setMobileNavOpen(false);
  }, []);

  const focusSurfaceHeading = useCallback(() => {
    requestAnimationFrame(() => {
      const heading = appMainRef.current?.querySelector<HTMLElement>('.surface h1');
      if (!heading) return void appMainRef.current?.focus();
      heading.tabIndex = -1;
      heading.focus();
    });
  }, []);

  useEffect(() => {
    // After sign-in the page's own heading takes focus, never the skip link.
    if (state.authStage === 'ready' && authFocusRequestedRef.current) {
      focusSurfaceHeading();
      authFocusRequestedRef.current = false;
    }
  }, [focusSurfaceHeading, state.authStage]);

  useEffect(() => {
    if (!mobileNavOpen && restoreDrawerTriggerRef.current) {
      restoreDrawerTriggerRef.current = false;
      requestAnimationFrame(() => mobileMenuButtonRef.current?.focus());
    }
  }, [mobileNavOpen]);

  useEffect(() => {
    if (!isDrawer && mobileNavOpen) {
      restoreDrawerTriggerRef.current = false;
      setMobileNavOpen(false);
    }
  }, [isDrawer, mobileNavOpen]);

  const channelAutomations = useMemo(() => state.sourceAutomations.filter((automation) => automation.source_type === 'channel'), [state.sourceAutomations]);
  const subscriptionContextVisible = surface === 'home' || surface === 'streaming' || surface === 'subscriptions';
  const activeDownloads = state.jobs.filter((job) => ['queued', 'running', 'postprocessing'].includes(job.status)).length;

  const reportMessage = useCallback((next: string) => { setMessage(next); setError(null); }, []);
  const acquisition = useMediaAcquisition({ workspace, onMessage: reportMessage });
  const subscriptionCommands = useMemo(() => createSessionGuardedChannelSubscriptionCommands({
    pause: pauseAutomation,
    resume: resumeAutomation,
    setAutomaticAcquisition: (automationId, enabled) => setAutomationAutoDownload(automationId, { enabled }),
    remove: deleteAutomation,
  }, { capture: workspace.captureSessionToken, isCurrent: workspace.isSessionTokenCurrent }), [workspace.captureSessionToken, workspace.isSessionTokenCurrent]);
  const subscriptionFeed = useSubscriptionAcquisitionFeed({
    channels: channelAutomations,
    enabled: state.authStage === 'ready' && subscriptionContextVisible,
    refreshGeneration: subscriptionRefreshGeneration,
    sessionIdentity: state.currentUser?.id || null,
    captureSessionToken: workspace.captureSessionToken,
    isSessionTokenCurrent: workspace.isSessionTokenCurrent,
    onFailure: (failure) => setError(failure instanceof Error ? failure.message : 'Unable to update followed channels.'),
    onSessionExpired: () => { subscriptionRetryFocusRequestedRef.current = false; void handleLogout(); },
    onSettled: (outcomes) => {
      if (!subscriptionRetryFocusRequestedRef.current) return;
      subscriptionRetryFocusRequestedRef.current = false;
      const target = outcomes.some((outcome) => outcome.status === 'authentication-required' || outcome.status === 'failed') ? '[data-subscription-retry]' : '.surface h1';
      requestAnimationFrame(() => appMainRef.current?.querySelector<HTMLElement>(target)?.focus());
    },
  });
  const subscriptionOutcomes = subscriptionFeed.outcomes;
  const subscriptionRefreshing = subscriptionFeed.refreshing;
  const subscriptionVideos = subscriptionFeed.videos;
  const watchSelection: WatchSelection | null = libraryWatchItem
    ? { kind: 'library', item: libraryWatchItem }
    : acquisition.state.selection
      ? { kind: 'remote', ...acquisition.state.selection }
      : null;
  const openPaletteMode = useCallback((mode: PaletteMode = 'search') => mode === 'link' && openSearchBlocked ? undefined : setPalette((value) => ({ open: true, mode, key: value.open ? value.key : value.key + 1 })), [openSearchBlocked]);
  const closePalette = useCallback(() => setPalette((value) => (value.open ? { ...value, open: false } : value)), []);
  useEffect(() => onCommand<PaletteRequest | undefined>(PALETTE_EVENT, (request) => openPaletteMode(request?.mode ?? 'search')), [openPaletteMode]);
  // Prefetch the palette chunk once the member is in.
  useEffect(() => {
    if (!state.currentUser) return undefined;
    const idle = window.requestIdleCallback ?? ((callback: () => void) => window.setTimeout(callback, 1));
    const handle = idle(() => CommandPalette.preload());
    return () => (window.cancelIdleCallback ?? window.clearTimeout)(handle as number);
  }, [state.currentUser?.id]);
  // Stable references for the callbacks passed into memoized list rows, so a
  // realtime progress redraw of the root never re-renders every card.
  const openLibraryStable = useStableCallback((item: LibraryItem) => openLibrary(item));
  const openRemoteStable = useStableCallback((item: YouTubeSearchResult) => { void openRemote(item); });
  const openQueuedStable = useStableCallback((entry: WatchQueueEntry) => { if (entry.ref.library_item_id) openRoute({ surface: 'watch', libraryId: entry.ref.library_item_id }); else void openRemote(queuedAsRemote(entry)); });
  // acquisition.queue is a new reference every render (its deps include the fresh
  // workspace and options literals), so it must be stabilized before reaching the
  // memoized StillCards or every remote grid card re-renders on each root redraw.
  const queueRemoteStable = useStableCallback((item: YouTubeSearchResult) => { void acquisition.queue(item); });
  const handleCancelStable = useStableCallback((job: DownloadJob) => { void handleCancel(job); });
  const handleRetryStable = useStableCallback((job: DownloadJob) => handleRetry(job));
  const libraryItemChangedStable = useStableCallback((item: LibraryItem) => workspace.dispatch({ type: 'library/upsert', item }));
  const loadMoreJobsStable = useStableCallback(() => { void workspace.loadMoreJobs(); });
  // Page props keep one identity so the memoized pages skip header keystrokes, toasts and poll ticks.
  const navigateStable = useStableCallback((next: Surface) => navigate(next));
  const paletteNavigate = useStableCallback((path: string) => { const url = new URL(path, window.location.origin); openRoute(parseRoute(url.pathname, url.search)); });
  const paletteSignOut = useStableCallback(() => { void handleLogout(); });
  const paletteContext = useMemo<PaletteContext | null>(() => state.currentUser ? ({
    user: state.currentUser,
    mobile: isDrawer, // the "Collapse sidebar" action is meaningless while the sidebar is a drawer
    sidebarCollapsed: state.preferences.sidebarCollapsed,
    theme: state.preferences.theme,
    // openRoute runs the unsaved-changes guard for every palette destination.
    navigate: paletteNavigate,
    setTheme: (theme) => workspace.dispatch({ type: 'preferences/patch', patch: { theme } }),
    toggleSidebar: () => workspace.dispatch({ type: 'preferences/patch', patch: { sidebarCollapsed: !state.preferences.sidebarCollapsed } }),
    signOut: paletteSignOut,
    openLinkMode: () => openPaletteMode('link'),
    titleId: surface === 'library' ? libraryLocation.titleId : undefined,
    titleType: surface === 'library' && libraryLocation.titleId ? (cachedTitle(libraryLocation.titleId)?.type ?? summaryFor(libraryLocation.titleId)?.type) : undefined,
  }) : null, [state.currentUser, isDrawer, state.preferences.sidebarCollapsed, state.preferences.theme, surface, libraryLocation.titleId, palette.open]); // eslint-disable-line react-hooks/exhaustive-deps -- dispatch is stable; surface and the open title gate the edit actions; the rest read through stable callbacks
  const openInterestSettingsStable = useStableCallback(() => openRoute({ surface: 'settings', section: 'discovery' }));
  const retryRecommendations = useCallback(() => setHomeRecommendationRefreshGeneration((generation) => generation + 1), []);
  const runExploreStable = useStableCallback((query: string) => runExplore(query));
  const loadMoreExploreStable = useStableCallback(() => { void runExplore(exploreQuery, EXPLORE_MAX); });
  const retryLibrary = useStableCallback(() => { void (state.libraryProblem?.retryOperation === 'refresh' ? handleRefresh() : workspace.retryCollection('library')); });
  const retryJobs = useStableCallback(() => { void workspace.retryCollection('jobs'); });
  const returnToSignIn = useStableCallback(() => { void handleLogout(); });
  const saveMemberInterests = useCallback(async (keys: string[]) => {
    const token = workspace.captureSessionToken();
    const updated = await updateMemberInterests(keys);
    if (!workspace.isSessionTokenCurrent(token)) return;
    setMemberInterests(updated);
    setHomeRecommendations(null);
    setHomeRecommendationsError(null);
    reportMessage(updated.selected_keys.length ? 'Your Home interests were saved.' : 'Your Home interests were cleared.');
  }, [reportMessage, workspace.captureSessionToken, workspace.isSessionTokenCurrent]);
  const updateDisplayNameStable = useCallback(async (displayName: string) => {
    const token = workspace.captureSessionToken();
    const { user } = await updateDisplayName(displayName);
    if (!workspace.isSessionTokenCurrent(token)) return;
    workspace.dispatch({ type: 'currentUser/replace', user });
  }, [workspace.captureSessionToken, workspace.dispatch, workspace.isSessionTokenCurrent]);
  const refreshSuppressions = useCallback(async () => {
    const token = workspace.captureSessionToken();
    try {
      const listing = await getSuppressions();
      if (workspace.isSessionTokenCurrent(token)) setSuppressions(listing);
    } catch { /* the Settings surface degrades to its last-known list */ }
  }, [workspace.captureSessionToken, workspace.isSessionTokenCurrent]);
  const refreshSearchHistory = useCallback(async () => {
    const token = workspace.captureSessionToken();
    try {
      const listing = await getSearchHistory();
      if (workspace.isSessionTokenCurrent(token)) setSearchHistory(Array.isArray(listing) ? listing : []);
    } catch { /* the search popover degrades to its last-known list */ }
  }, [workspace.captureSessionToken, workspace.isSessionTokenCurrent]);
  // A search write is fire-and-forget: the search flow (runExplore/openRemote)
  // never awaits this, so a failed or slow history write can never block or
  // abort a search.
  const recordSearchHistoryStable = useStableCallback((query: string) => {
    const token = workspace.captureSessionToken();
    recordSearchHistory(query).then((entry) => {
      if (entry && workspace.isSessionTokenCurrent(token)) setSearchHistory((current) => withHistoryEntry(entry, current));
    }).catch(() => { /* history not saved; the search itself already completed */ });
  });
  const removeSearchHistoryEntryStable = useStableCallback((entryId: string) => {
    setSearchHistory((current) => current.filter((entry) => entry.id !== entryId));
    void deleteSearchHistoryEntry(entryId).catch(() => void refreshSearchHistory());
  });
  const clearSearchHistoryStable = useStableCallback(() => {
    void (async () => {
      try {
        await clearSearchHistory();
        setSearchHistory([]);
        reportMessage('Search history cleared.');
      } catch { setError('Could not clear search history. Try again.'); }
    })();
  });
  const restoreSuppressionStable = useStableCallback((id: string) => {
    void (async () => {
      try {
        await restoreSuppression(id);
        setRecoEpoch((value) => value + 1);
        setHomeRecommendationRefreshGeneration((generation) => generation + 1);
        void refreshSuppressions();
        reportMessage('Restored. It can appear in future recommendations again.');
      } catch { setError('Could not restore that suppression. Try again.'); }
    })();
  });
  // Recommendation feedback. The provider owns each card's Undo window; these are the
  // calls it makes. A suppression is filtered centrally by the shared policy, so the client saves the intent and, when
  // the Undo window closes, drops the item from what is on screen and pulls a replacement.
  const recoSuppress = useStableCallback(async (input: SuppressRecommendationInput) => {
    const saved = await suppressRecommendation(input);
    void refreshSuppressions();
    return saved;
  });
  const recoRestore = useStableCallback(async (id: string) => {
    await restoreSuppression(id);
    void refreshSuppressions();
  });
  const recoExpired = useStableCallback(({ feedback, target }: { feedback: RecoFeedback; target: RecoTarget }) => {
    acquisition.dropRelated(relatedDropPredicate(feedback, target));
    setHomeRecommendationRefreshGeneration((generation) => generation + 1);
    if (surface === 'streaming') {
      const token = workspace.captureSessionToken();
      void getPopularDiscovery().then((snapshot) => { if (workspace.isSessionTokenCurrent(token)) setPopularDiscovery(snapshot); }).catch(() => undefined);
    }
  });
  const playbackPrefs = useMemo<PlaybackPrefs>(() => ({ normalizeLoudness: state.preferences.normalizeLoudness, autoSkip: state.preferences.autoSkip, profanity: state.preferences.profanity }), [state.preferences.normalizeLoudness, state.preferences.autoSkip, state.preferences.profanity]);
  const patchPlaybackPrefsStable = useStableCallback((patch: Partial<PlaybackPrefs>) => workspace.dispatch({ type: 'preferences/patch', patch }));
  // Shell messages and errors are toasts: once per distinct text, and the error is cleared once toasted so a repeat of the same text toasts again.
  const lastToasted = useRef<string | null>(null);
  useEffect(() => {
    if (message && message !== lastToasted.current) toast({ tone: 'success', message });
    lastToasted.current = message;
    if (message) setMessage(null);
  }, [message, toast]);
  // The auth screens render `error` inline (role=alert), and Watch reads the acquisition failure inline, so neither is cleared by toasting.
  useShellErrorToast(state.authStage === 'ready' ? error : null, () => setError(null));
  useShellErrorToast(acquisition.state.failure?.message ?? null, undefined, acquisition.state.failure);
  const offlineToasted = useRef<number | null>(null);
  useEffect(() => {
    if (state.jobsState === 'offline') {
      if (offlineToasted.current === null) offlineToasted.current = toast({ tone: 'error', message: "Lumina can't reach the server. Trying again…", action: { label: 'Try again', onAction: () => void handleRefresh() } });
    } else if (offlineToasted.current !== null) {
      toast.remove(offlineToasted.current);
      offlineToasted.current = null;
    }
  }, [state.jobsState, toast]); // eslint-disable-line react-hooks/exhaustive-deps -- handleRefresh is a hoisted function over current workspace
  useEffect(() => { if (!subscriptionContextVisible) subscriptionRetryFocusRequestedRef.current = false; }, [subscriptionContextVisible]);

  useEffect(() => {
    const handler = (event: globalThis.KeyboardEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      const canOpen = state.authStage === 'ready' && Boolean(state.currentUser) && !document.querySelector('dialog[open]');
      const typing = Boolean(target?.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]'));
      if (canOpen && (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); openPaletteMode('search'); return; }
      if (canOpen && event.key === '/' && !typing && !event.metaKey && !event.ctrlKey && !event.altKey) { event.preventDefault(); openPaletteMode('search'); return; }
      if (event.key === 'Escape') {
        if (!document.querySelector('.g-palette')) closePalette(); // the lazy palette chunk has not mounted, so nothing of its own can take this Escape: cancel the pending open
        setDownloadMenuOpen(false);
        if (mobileNavOpen) closeMobileNavigation();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [closeMobileNavigation, closePalette, mobileNavOpen, openPaletteMode, state.authStage, state.currentUser]);

  usePolledSnapshot(surface === 'streaming' && !exploreQuery && state.currentUser ? `explore:${state.currentUser.id}` : null, workspace, (isCurrent) => getPopularDiscovery().then((snapshot) => {
    if (!isCurrent()) return;
    setPopularDiscovery(snapshot);
    setPopularDiscoveryError(null);
  }).catch((loadError) => {
    if (isCurrent()) setPopularDiscoveryError(loadError instanceof Error ? loadError.message : 'Popular discovery is temporarily unavailable.');
  }));

  usePolledSnapshot(surface === 'streaming' || (surface === 'subscriptions' && !channelPage) ? `live:${state.currentUser?.id}:${liveGeneration}` : null, workspace, (isCurrent) => getLiveDiscovery().then((snapshot) => {
    if (!isCurrent()) return;
    setLiveDiscovery(snapshot);
    setLiveDiscoveryError(null);
  }).catch((loadError) => {
    if (isCurrent()) setLiveDiscoveryError(loadError instanceof Error ? loadError.message : 'Live discovery is temporarily unavailable.');
  }));

  useEffect(() => {
    if (state.authStage !== 'ready' || !state.currentUser) {
      setLiveDiscovery(null);
      setLiveDiscoveryError(null);
    }
  }, [state.authStage, state.currentUser?.id]);

  useEffect(() => {
    if (state.authStage !== 'ready' || !state.currentUser) {
      setMemberInterests(null);
      return undefined;
    }
    let active = true;
    const token = workspace.captureSessionToken();
    void getMemberInterests().then((interests) => {
      if (active && workspace.isSessionTokenCurrent(token)) setMemberInterests({
        categories: Array.isArray(interests.categories) ? interests.categories : [],
        selected_keys: Array.isArray(interests.selected_keys) ? interests.selected_keys : [],
      });
    }).catch(() => {
      if (active && workspace.isSessionTokenCurrent(token)) setMemberInterests(null);
    });
    return () => { active = false; };
  }, [state.authStage, state.currentUser?.id, workspace.captureSessionToken, workspace.isSessionTokenCurrent]);

  useEffect(() => {
    if (state.authStage !== 'ready' || !state.currentUser) {
      setSuppressions({ items: [], channels: [] });
      return;
    }
    void refreshSuppressions();
  }, [state.authStage, state.currentUser?.id, refreshSuppressions]);

  // Server-owned history follows the signed-in member: cleared immediately on
  // logout/account switch (never carried over to the next account in this
  // browser) and reloaded for whoever is ready next.
  useEffect(() => {
    if (state.authStage !== 'ready' || !state.currentUser) {
      setSearchHistory([]);
      return;
    }
    void refreshSearchHistory();
  }, [state.authStage, state.currentUser?.id, refreshSearchHistory]);

  // A newly created member resumes first-run setup on every device and reload
  // until they complete or skip; the durable status closes the gate for good.
  useEffect(() => {
    if (state.authStage === 'ready' && state.currentUser?.onboarding_status === 'pending') {
      setOnboarding((current) => current ?? { phase: 'setup', keys: [] });
    }
  }, [state.authStage, state.currentUser?.id, state.currentUser?.onboarding_status]);

  const homeInterestKeys = memberInterests?.selected_keys?.join('|') || '';
  // Picks stay cached while away from Home, so returning shows them at once.
  // A hidden Picked for you shelf never asks for (or polls) picks; showing it again starts the fetch.
  const pickedHidden = normalizeHomeShelves(state.preferences.homeShelves).some((shelf) => shelf.id === 'picked_for_you' && !shelf.visible);
  usePolledSnapshot(surface === 'home' && state.authStage === 'ready' && !pickedHidden ? `${homeRecommendationRefreshGeneration}:${homeInterestKeys}` : null, workspace, (isCurrent) => {
    setHomeRecommendationsLoading(true);
    return getHomeRecommendations().then((snapshot) => {
      if (!isCurrent()) return false;
      setHomeRecommendations(snapshot);
      setHomeRecommendationsError(null);
      // Poll only while discovery is still warming up; a settled snapshot needs no refetch.
      return snapshot.state === 'ready' && !snapshot.refreshing;
    }).catch((loadError) => {
      if (isCurrent()) setHomeRecommendationsError(loadError instanceof Error ? loadError.message : 'Personalized discovery is temporarily unavailable.');
      return false;
    }).finally(() => {
      if (isCurrent()) setHomeRecommendationsLoading(false);
    });
  });

  // The next member inherits no buffered recommendation event, no dedupe (the provider is keyed the same way), no Explore
  // snapshot (its For you names what the last member watched) and no Home picks (these were cleared when the interests went away; picks no longer need interests).
  useEffect(() => { resetRecoEvents(); setPopularDiscovery(null); setPopularDiscoveryError(null); setHomeRecommendations(null); setHomeRecommendationsError(null); setHomeRecommendationsLoading(false); }, [state.currentUser?.id]);

  // Session revocation or a user switch stops the player: the next member never inherits it.
  useEffect(() => { resetWatchQueue(); resetWatch(); }, [state.currentUser?.id]);

  useEffect(() => {
    const item = watchSelection?.kind === 'library' ? watchSelection.item : null;
    if (!item || !state.currentUser) {
      setCurationDataItemId(null);
      setCurationTags([]);
      setCurationError(null);
      setCurationLoading(false);
      return;
    }
    let active = true;
    const token = workspace.captureSessionToken();
    setCurationDataItemId(item.id);
    setCurationTags([]);
    setCurationBusy(false);
    setCurationLoading(true);
    setCurationError(null);
    void listLibraryTags(item.id).then((tags) => {
      if (!active || !workspace.isSessionTokenCurrent(token)) return;
      setCurationTags(tags);
    }).catch((loadError) => {
      if (active && workspace.isSessionTokenCurrent(token)) setCurationError(loadError instanceof Error ? loadError.message : 'Unable to load Library details.');
    }).finally(() => {
      if (active && workspace.isSessionTokenCurrent(token)) setCurationLoading(false);
    });
    return () => { active = false; };
  }, [state.currentUser?.id, watchSelection?.kind, watchSelection?.kind === 'library' ? watchSelection.item.id : null, workspace.captureSessionToken, workspace.isSessionTokenCurrent]);

  async function finishTwoFactor() { setTwoFactorChallenge(null); authFocusRequestedRef.current = true; resetDeviceRing(); await workspace.loadAuthenticated(); }
  const twoFactor = twoFactorChallenge ? { challenge: twoFactorChallenge, onVerified: finishTwoFactor, onBack: (message?: string) => { setTwoFactorChallenge(null); setError(message ?? null); } } : null;

  async function handleLogin(username: string, password: string, rememberOnDevice = false) {
    setAuthBusy(true); setError(null);
    authFocusRequestedRef.current = true;
    try {
      const result = await ringCall(() => loginSession({ username: username.trim(), password, remember_on_device: rememberOnDevice }));
      if ('two_factor_required' in result) { setTwoFactorChallenge(result.challenge); authFocusRequestedRef.current = false; return; }
      resetDeviceRing(); await workspace.loadAuthenticated();
    } catch (loginError) {
      authFocusRequestedRef.current = false;
      setError(loginError instanceof ApiRequestError && loginError.status === 429 ? 'Too many attempts. Try again in a minute.' : loginError instanceof Error ? loginError.message : 'Unable to sign in');
    } finally { setAuthBusy(false); }
  }

  async function handleSetup(username: string, displayName: string, password: string) {
    setAuthBusy(true); setError(null);
    authFocusRequestedRef.current = true;
    try { await createInitialAdmin({ username: username.trim(), display_name: displayName.trim() || username.trim(), password }); await loginSession({ username: username.trim(), password }); await workspace.loadAuthenticated(); }
    catch (setupError) { authFocusRequestedRef.current = false; setError(setupError instanceof Error ? setupError.message : 'Unable to create the vault'); }
    finally { setAuthBusy(false); }
  }

  async function handleAccountLink(username: string, displayName: string, password: string) {
    if (!accountLink) return;
    setAuthBusy(true); setError(null);
    try {
      if (accountLink.kind === 'reset') await redeemPasswordReset(accountLink.token, password);
      else await redeemInvitation({ token: accountLink.token, username: username.trim(), display_name: displayName.trim() || username.trim(), password });
    } catch (redeemError) { setError(redeemError instanceof Error ? redeemError.message : 'Unable to use this link'); setAuthBusy(false); return; }
    setAccountLink(null);
    if (state.currentUser) await handleLogout();
    if (accountLink.kind === 'reset') setAuthBusy(false);
    else await handleLogin(username, password);
  }

  async function handleRetrySession() {
    setAuthBusy(true);
    authFocusRequestedRef.current = true;
    workspace.dispatch({ type: 'session/retrying' });
    try {
      await workspace.loadAuthenticated();
    } catch (sessionError) {
      authFocusRequestedRef.current = false;
      const unauthorized = sessionError instanceof ApiRequestError && sessionError.status === 401;
      if (unauthorized) workspace.reset();
      try { await workspace.loadPublic(); } catch { /* The durable recovery message remains useful offline. */ }
      if (!unauthorized) {
        workspace.dispatch({ type: 'session/loadFailed', problem: classifySessionLoadProblem(sessionError) });
      }
    } finally {
      setAuthBusy(false);
    }
  }

  /** Everything per-member the next member must not inherit (the workspace, acquisition, refs, surface, search). */
  function resetMemberState() {
    offlineToasted.current = null; toast.clear(); // the leaving member's toasts and their Undo must not reach the next member
    workspace.reset();
    acquisition.resetSession();
    playbackMutationRef.current = Promise.resolve();
    activeLibraryItemIdRef.current = null; setSurface('home'); setLibraryLocation({}); setLibraryWatchItem(null); setActivePlayback(null); closePalette();
  }

  async function handleLogout() {
    if (state.currentUser) forgetStoredSections(state.currentUser.id); // a sign-out clears the member's stored tabs
    const warmupStopped = forgetPlaybackWarmup(); // an early session's stop needs the session this ends
    resetMemberState();
    await warmupStopped;
    try { await ringCall(() => logoutSession()); } catch { /* Best effort. */ }
    resetDeviceRing();
    await workspace.loadPublic();
  }

  /** Runs while the leaving member's cookie is still current (so the last checkpoint is not lost): the player closes and its last checkpoint
   * (keepalive) is answered, the /api/events stream closes, and the warm-up stop and the buffered recommendation events
   * (1.9.0, features/reco/recoEvents.ts) go out, all under the leaving member's own session, never the next member's.
   * Returns how to hand the stream back when the change then fails; the closed player stays closed. */
  async function prepareMemberChange() {
    if (!leaveSettingsOk()) throw new Error('Staying in Settings: your changes are not saved yet.'); // ask before anything is torn down
    const earlierWrites = playbackMutationRef.current;
    // flushSync commits now, so the player's unmount checkpoint and the stream's close run before any switch request.
    flushSync(() => { resetWatch(); workspace.holdEvents(true); });
    try {
      await new Promise((resolve) => { globalThis.setTimeout(resolve, 0); }); // a stream release is deferred one task
      await Promise.all([earlierWrites.catch(() => undefined), playbackMutationRef.current.catch(() => undefined), keepaliveWritesSettled(), forgetPlaybackWarmup(), flushRecoEvents()]);
    } catch (error) {
      workspace.holdEvents(false); // a failed prepare must not leave the leaving member without realtime updates
      throw error;
    }
    return () => workspace.holdEvents(false);
  }

  /** A remembered member was chosen (picker) or someone signed in over the current member. */
  async function handleSwitched() {
    resetMemberState();
    resetDeviceRing();
    workspace.holdEvents(false);
    authFocusRequestedRef.current = true;
    try { await workspace.loadAuthenticated(); } catch { await workspace.loadPublic(); }
    navigate('home');
  }

  // A poster click starts the title page's hero clock itself, before any state update or Suspense
  // fallback commits; the address sync to that path then keeps the mark. Any later navigation clears it.
  const navigationMarked = useRef<string | null>(null);
  // Every exit from Settings that does not go through navigate asks about unsaved edits first.
  const leaveSettingsOk = () => confirmLeaveSettings();
  function resetWatch() { playbackLoadRequestRef.current += 1; curationMutationGenerationRef.current += 1; acquisition.reset(); setLibraryWatchItem(null); activeLibraryItemIdRef.current = null; setPlaybackLoading(false); }
  // Leaving Watch keeps the selection: its surface docks as the mini-player.
  function navigate(next: Surface) { navigationMarked.current = null; if (!confirmLeaveSettings()) return; if (surface !== 'watch') setPreviousSurface(surface); if (surface === next) focusSurfaceHeading(); else window.scrollTo({ top: 0 }); setSurface(next); setStreamingView('home'); setStreamingProvider('youtube'); setChannelId(null); setChannelPage(null); setExploreRail(null); setLiveRail(null); setRequestsRoute({ surface: 'requests', view: 'discover' }); if (next !== 'watch') setLibraryLocation({}); closePalette(); setMobileNavOpen(false); }
  // Watch's Back returns where the member came from, including the title page and season.
  function leaveWatch() { const back = previousSurface === 'watch' ? 'home' : previousSurface; const location = libraryLocation; const page = channelPage; const view = streamingView; const provider = streamingProvider; navigate(back); if (back === 'streaming') { setStreamingView(view); setStreamingProvider(provider); } if (back === 'library') setLibraryLocation(location); if (back === 'subscriptions') setChannelPage(page); }

  function openRoute(route: AppRoute) {
    navigationMarked.current = null; // a poster click's mark is for its own navigation only
    if (route.surface === 'streaming') {
      const provider = route.provider ?? 'youtube';
      if (route.view === 'search' && route.query) {
        setStreamingProvider(provider);
        if (route.query !== exploreQuery || surface !== 'streaming' || streamingView !== 'search') void runExplore(route.query);
        return;
      }
      if (route.view === 'home') { setExploreQuery(''); setExploreResults(NO_EXPLORE_RESULTS); }
    }
    // Switching tabs inside the open editor keeps the draft: no unsaved-changes prompt.
    if (route.surface === 'library' && 'edit' in route && route.edit && surface === 'library' && libraryLocation.edit && libraryLocation.titleId === route.titleId) { setLibraryLocation(libraryLocationOf(route)); return; }
    if (route.surface === 'settings') { setSettingsSection(route.section ?? null); setSettingsMember('memberId' in route ? route.memberId : null); }
    if (route.surface !== 'watch') {
      navigate(route.surface);
      if ('channelId' in route) setChannelId(route.channelId);
      if ('youtubeChannelId' in route) setChannelPage({ id: route.youtubeChannelId, tab: route.tab ?? 'videos' });
      if ('channelUrl' in route) setChannelPage({ url: route.channelUrl });
      if (route.surface === 'streaming') {
        setStreamingView(route.view === 'search' ? 'home' : route.view); setStreamingProvider(route.provider ?? 'youtube');
        if (route.rail) (route.view === 'live' ? setLiveRail : setExploreRail)(route.rail);
      }
      if (route.surface === 'library') setLibraryLocation(libraryLocationOf(route));
      if (route.surface === 'requests') setRequestsRoute(route);
      return;
    }
    if ('url' in route) { if (watchSelection?.kind === 'remote' && watchSelection.item.webpage_url === route.url) navigate('watch'); else void openRemote({ title: 'Loading video…', webpage_url: route.url }); return; }
    const start = route.startSeconds === undefined ? null : { libraryId: route.libraryId, seconds: route.startSeconds, nonce: performance.now() };
    if (libraryWatchItem?.id === route.libraryId) { setWatchStart(start); navigate('watch'); return; }
    // Show the Watch loading state (no selection => no URL write) while the item loads.
    if (!leaveSettingsOk()) return;
    setWatchStart(start);
    // Time to first frame starts at this Play, an early session is kept for the player (one started at
    // the resume point is useless for a `t`, and so is one for another item: any unclaimed one stops now), and the
    // decision loads beside the item.
    notePlayIntent();
    if (start) cancelSpeculativeStart(); else claimSpeculativeStart(route.libraryId);
    prefetchPlaybackOptions(route.libraryId);
    resetWatch(); if (surface !== 'watch') setPreviousSurface(surface); setSurface('watch'); closePalette();
    const request = playbackLoadRequestRef.current;
    const token = workspace.captureSessionToken();
    // The resume point is fetched beside the item, not after it: the player mounts one round trip sooner.
    const progress = getPlaybackProgress(route.libraryId);
    progress.catch(() => undefined); // openLibrary reports a failure; a Play that never gets that far must not leave it unhandled
    void getLibraryItem(route.libraryId).then((item) => {
      if (request === playbackLoadRequestRef.current && workspace.isSessionTokenCurrent(token)) openLibrary(item, true, progress);
    }).catch(() => {
      if (request !== playbackLoadRequestRef.current || !workspace.isSessionTokenCurrent(token)) return;
      takePlayIntent(); // a failed Play must not time the next one's first frame
      setError('That Library item is unavailable.');
      navigate('home');
    });
  }
  const openRouteStable = useStableCallback(openRoute);
  // In-tab Requests moves: no heading refocus; `replace` (search typing, chip changes) keeps one history entry.
  const requestsRouteStable = useStableCallback((route: RequestsRoute, replace?: boolean) => { if (replace) replaceHistoryRef.current = true; setRequestsRoute(route); });
  const requestsQueueChangedStable = useStableCallback(() => setRequestsQueueGeneration((value) => value + 1));
  // The admin's Requests badge: how many requests wait for a decision, every 30 s while signed in as an admin.
  usePolledSnapshot(state.currentUser?.role === 'admin' ? `requests-pending:${state.currentUser.id}:${requestsQueueGeneration}` : null, workspace, (isCurrent) => listRequests({ scope: 'all', status: 'pending' }).then((page) => {
    if (isCurrent()) setPendingRequests(page.counts.pending ?? page.items.length);
  }, () => undefined));
  const openLibraryIdStable = useStableCallback((libraryId: string, startSeconds?: number) => openRoute({ surface: 'watch', libraryId, ...(startSeconds === undefined ? {} : { startSeconds }) }));
  // What the hover menu on any cover art may do (ArtMenu); the callbacks are stable, so the value moves only with the member.
  const artActions = useMemo(() => ({ user: state.currentUser, navigate: paletteNavigate, playLibrary: openLibraryIdStable, openRemote: openRemoteStable, saveRemote: queueRemoteStable }), [state.currentUser, paletteNavigate, openLibraryIdStable, openRemoteStable, queueRemoteStable]);
  const homeShelvesChangeStable = useStableCallback((homeShelves: HomeShelfPref[] | null) => workspace.dispatch({ type: 'preferences/patch', patch: { homeShelves } }));
  const openTitleStable = useStableCallback((title: TitleSummary) => {
    if (title.reco) recordRecoOpen(title.reco);
    const { id, season } = titleDestination(title);
    const route: AppRoute = season === null ? { surface: 'library', titleId: id } : { surface: 'library', titleId: id, season };
    // Back returns to the place the title was opened from; from a title page, the one before.
    const fromTitlePage = surface === 'library' && Boolean(libraryLocation.titleId);
    if (!fromTitlePage) {
      lastLens.current = surface === 'library' ? (libraryLocation.collections ? 'collections' : libraryLocation.view ?? 'all') : null;
      lastCollection.current = surface === 'library' && libraryLocation.collections === 'detail' ? libraryLocation.collectionId ?? null : null;
    }
    const artist = libraryLocation.titleId!;
    backToArtist.current = fromTitlePage && title.type === 'album' ? { album: id, artist, name: summaryFor(artist)?.name ?? cachedTitle(artist)?.name ?? 'Artist', direct: true } : null;
    markNavigation();
    rememberSummary(title); // the title page paints it at once
    openRoute(route);
    navigationMarked.current = routePath(route);
    window.scrollTo({ top: 0 });
  });
  // The title page's own Back returns to the place it was opened from, with each lens's wall as it was left.
  const lastWalls = useRef<Partial<Record<LibraryPlace, string>>>({});
  const lastLens = useRef<LibraryLens | 'all' | 'collections' | null>(null);
  const lastCollection = useRef<string | null>(null);
  // An album opened on its artist's page: Back returns to that page, by the browser's Back while the album has stayed
  // on screen since (`direct`); after a detour (a track played, another surface) the entry before is not the artist.
  const backToArtist = useRef<{ album: string; artist: string; name: string; direct: boolean } | null>(null);
  useEffect(() => {
    if (backToArtist.current && (surface !== 'library' || libraryLocation.titleId !== backToArtist.current.album)) backToArtist.current.direct = false;
  }, [surface, libraryLocation.titleId]);
  // A sign-out or member switch forgets every wall (its filters and search, and the cached pages with their marks),
  // the All landing, the cached title details and summaries with their marks, and any early playback session and decisions.
  // Not on sign-in itself: the first wall may already have rendered its store in that same commit.
  const wallMember = useRef<string | null>(null);
  useEffect(() => {
    const member = state.currentUser?.id ?? null;
    if (wallMember.current !== null && wallMember.current !== member) {
      lastWalls.current = {};
      lastLens.current = null;
      lastCollection.current = null;
      backToArtist.current = null;
      forgetWallStores();
      forgetAllStore();
      clearAlbumQueue();
      forgetTitles();
      forgetHomeCache();
      forgetPlaybackWarmup();
    }
    wallMember.current = member;
  }, [state.currentUser?.id]);
  useEffect(() => {
    if (!libraryLocation.titleId) lastWalls.current[libraryLocation.view ?? 'all'] = libraryLocation.wall;
  }, [libraryLocation.titleId, libraryLocation.view, libraryLocation.wall]);
  const titleBackStable = useStableCallback((type: TitleType | null, category?: TitleCategory | null) => {
    const artist = backToArtist.current;
    if (artist && artist.album === libraryLocation.titleId) {
      backToArtist.current = null;
      if (artist.direct) window.history.back();
      else openRoute({ surface: 'library', titleId: artist.artist });
      return;
    }
    if (lastLens.current === 'collections') {
      openRoute(lastCollection.current ? { surface: 'library', collections: 'detail', collectionId: lastCollection.current } : { surface: 'library', collections: 'list' });
      return;
    }
    openRoute(libraryBackRoute(type, category, lastLens.current, lastWalls.current));
  });
  /** Back's words name where it goes: the artist, the All landing, or the lens it was opened from. */
  const titleBackLabel = () => {
    const artist = backToArtist.current;
    if (artist && artist.album === libraryLocation.titleId) return artist.name;
    if (lastLens.current === 'all') return 'Library';
    if (lastLens.current === 'collections') return 'Collections';
    return lastLens.current ? LENS_LABELS[lastLens.current] : undefined;
  };
  const openPlaceStable = useStableCallback((place: LibraryPlace) => {
    // Each lens draws its own tab row, so the focused tab or See all unmounts: focus moves to the new active tab.
    const fromRow = document.activeElement?.closest('.g-tabs, .g-see-all');
    setLibraryLocation(place === 'all' ? {} : place === 'collections' ? { collections: 'list' } : { view: place });
    window.scrollTo({ top: 0 });
    if (fromRow) requestAnimationFrame(() => document.querySelector<HTMLElement>('.g-tabs [aria-current="page"]')?.focus({ preventScroll: true }));
  });
  const hideContinueWatchingStable = useStableCallback(async (entry: PlaybackProgress) => {
    await hideContinueWatching(entry.item_id);
    workspace.dispatch({ type: 'continueWatching/remove', itemId: entry.item_id });
  });
  const restoreContinueWatchingStable = useStableCallback(async (entry: PlaybackProgress) => {
    await unhideContinueWatching(entry.item_id);
    workspace.dispatch({ type: 'continueWatching/checkpoint', progress: entry });
  });

  const locationPath = () => `${window.location.pathname}${window.location.search}`;
  const syncedPathRef = useRef<string | null>(null);
  // A popstate (Back, or a surface's own redirect) just moved the address; the next sync must not push the stale path over it.
  const poppedRef = useRef(false);
  const replaceHistoryRef = useRef(false);
  useEffect(() => {
    const onPopState = () => {
      // Fragment-only moves (the skip link) also fire popstate; they are not route changes.
      if (locationPath() === syncedPathRef.current) return;
      // Tab changes inside one title's editor keep the draft, so they never ask.
      const from = new URL(syncedPathRef.current ?? '/', window.location.origin);
      const here = parseRoute(from.pathname, from.search);
      const there = parseRoute(window.location.pathname, window.location.search);
      const sameEditor = 'edit' in here && 'edit' in there && here.edit && there.edit && 'titleId' in here && 'titleId' in there && here.titleId === there.titleId;
      // Back out of a Settings form with unsaved edits asks first; staying puts the address back.
      if (!sameEditor && !confirmLeaveSettings()) { window.history.pushState(null, '', syncedPathRef.current ?? '/'); return; }
      const route = parseRoute(window.location.pathname, window.location.search);
      // A link time is applied once: the entry drops it so Back, Forward and reload do not seek there again.
      if ('startSeconds' in route && route.startSeconds !== undefined) window.history.replaceState(null, '', routePath({ surface: 'watch', libraryId: route.libraryId }));
      syncedPathRef.current = locationPath();
      poppedRef.current = true;
      window.setTimeout(() => { poppedRef.current = false; }, 0);
      openRouteStable(route);
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, [openRouteStable]);

  useEffect(() => {
    if (state.authStage !== 'ready' || !state.currentUser || !pendingRouteRef.current) return;
    const route = pendingRouteRef.current;
    pendingRouteRef.current = null;
    openRouteStable(route);
  }, [openRouteStable, state.authStage, state.currentUser?.id]);

  const libraryRoute: AppRoute = libraryLocation.titleId && libraryLocation.edit
    ? (libraryLocation.tab ? { surface: 'library', titleId: libraryLocation.titleId, edit: true, tab: libraryLocation.tab } : { surface: 'library', titleId: libraryLocation.titleId, edit: true })
    : libraryLocation.titleId
    ? (libraryLocation.season === undefined ? { surface: 'library', titleId: libraryLocation.titleId } : { surface: 'library', titleId: libraryLocation.titleId, season: libraryLocation.season })
    : libraryLocation.collections
      ? (libraryLocation.collections === 'detail' && libraryLocation.collectionId ? { surface: 'library', collections: 'detail', collectionId: libraryLocation.collectionId } : libraryLocation.create ? { surface: 'library', collections: 'list', create: libraryLocation.create } : { surface: 'library', collections: 'list' })
    : libraryLocation.view
      ? (libraryLocation.wall ? { surface: 'library', view: libraryLocation.view, wall: libraryLocation.wall } : { surface: 'library', view: libraryLocation.view })
      : { surface: 'library' };
  const streamingRoute: AppRoute = (() => {
    const view = streamingView === 'search' && !exploreQuery ? 'home' : streamingView;
    const rail = view === 'live' ? liveRail : view === 'home' ? exploreRail : null;
    return { surface: 'streaming', view, ...(streamingProvider !== 'youtube' ? { provider: streamingProvider } : {}), ...(view === 'search' ? { query: exploreQuery } : {}), ...(rail ? { rail } : {}) };
  })();
  const currentRoute: AppRoute | null = surface === 'watch'
    ? watchSelection?.kind === 'library' ? { surface: 'watch', libraryId: watchSelection.item.id }
      : watchSelection?.item.webpage_url ? { surface: 'watch', url: watchSelection.item.webpage_url }
        : null
    : surface === 'library' ? libraryRoute
      : surface === 'streaming' ? streamingRoute
        : surface === 'settings' ? (settingsSection === 'members' && settingsMember ? { surface, section: 'members', memberId: settingsMember } : settingsSection ? { surface, section: settingsSection } : { surface })
        : surface === 'requests' ? requestsRoute
          : surface === 'subscriptions' && channelPage ? ('url' in channelPage ? { surface, channelUrl: channelPage.url } : { surface, youtubeChannelId: channelPage.id, tab: channelPage.tab })
            : surface === 'subscriptions' && channelId ? { surface, channelId }
              : surface === 'subscriptions' ? streamingRoute : { surface };
  const currentPath = currentRoute && !pendingRouteRef.current ? routePath(currentRoute) : null;
  // Focus follows the page, not the surface: a tile opening a channel page keeps the surface but changes the key.
  const focusKey = routeFocusKey(currentRoute);
  const focusKeyRef = useRef(focusKey);
  useEffect(() => {
    // The first real route (a page load, an empty key before it) keeps the skip link as the first tab stop.
    if (focusKeyRef.current && focusKeyRef.current !== focusKey) focusSurfaceHeading();
    focusKeyRef.current = focusKey;
  }, [focusKey, focusSurfaceHeading]);
  useEffect(() => {
    if (!currentPath) return;
    // Wall and title-page metrics measure from here; the first sync keeps the page load's own start.
    if (syncedPathRef.current !== null && navigationMarked.current !== currentPath) markNavigation();
    navigationMarked.current = null;
    const popped = poppedRef.current;
    poppedRef.current = false;
    if (locationPath() !== currentPath && !popped) {
      // The first write only canonicalizes the entry the browser opened with.
      // A channel tab change replaces: Back leaves the channel page in one press.
      if (syncedPathRef.current && !replaceHistoryRef.current) window.history.pushState(null, '', currentPath);
      else window.history.replaceState(null, '', currentPath);
    }
    replaceHistoryRef.current = false;
    syncedPathRef.current = currentPath;
  }, [currentPath]);

  function revealHomeAfterOnboarding(status: OnboardingStatus, selectedKeys: string[]) {
    if (state.currentUser) workspace.dispatch({ type: 'currentUser/replace', user: { ...state.currentUser, onboarding_status: status } });
    setMemberInterests((current) => ({ categories: current?.categories ?? [], selected_keys: selectedKeys }));
    setOnboarding(null);
    setOnboardingError(null);
    setSurface('home');
    focusSurfaceHeading();
  }

  async function loadChannelSuggestions(keys: string[]) {
    const token = workspace.captureSessionToken();
    setChannelSuggestionsError(null);
    if (!keys.length) { setChannelSuggestions([]); setChannelSuggestionsLoading(false); return; }
    setChannelSuggestionsLoading(true);
    try {
      const response = await getChannelSuggestions(keys);
      if (workspace.isSessionTokenCurrent(token)) setChannelSuggestions(response.categories);
    } catch {
      if (workspace.isSessionTokenCurrent(token)) { setChannelSuggestions([]); setChannelSuggestionsError('suggestions-unavailable'); }
    } finally {
      if (workspace.isSessionTokenCurrent(token)) setChannelSuggestionsLoading(false);
    }
  }

  function handleInterestsContinue(keys: string[]) {
    // Setup advances to channel discovery; interests are held and saved together
    // with the follows when the member completes the discovery step.
    setOnboardingError(null);
    setOnboarding({ phase: 'discover', keys });
    void loadChannelSuggestions(keys);
  }

  function handleDiscoveryBack(keys: string[]) {
    setOnboarding({ phase: 'setup', keys });
  }

  async function handleChannelSearch(query: string): Promise<ChannelCandidate[]> {
    const response = await searchChannels(query);
    return response.channels;
  }

  async function handleOnboardingComplete(keys: string[], follows: FollowChannelRequest[]) {
    if (!state.currentUser) return;
    setOnboardingError(null);
    setOnboarding({ phase: 'preparing', keys });
    const token = workspace.captureSessionToken();
    let result: MemberOnboardingState;
    try {
      // Preparation waits only for this durable onboarding + interests + follow
      // write; provider and artwork warming happen afterward and never block it.
      result = await completeOnboarding(keys, follows);
    } catch {
      if (workspace.isSessionTokenCurrent(token)) { setOnboarding({ phase: 'discover', keys }); setOnboardingError('We could not save your setup just now. Please try again.'); }
      return;
    }
    if (!workspace.isSessionTokenCurrent(token)) return;
    if (result.followed.some((outcome) => outcome.status === 'created')) {
      // Seed the durably-created follows into workspace state so the existing
      // bounded, partial-outcome subscription feed warms their previews. A
      // warming failure is a channel-level recovery, never a rollback of the
      // follows this step already committed.
      try {
        const refreshed = await listAutomations();
        if (workspace.isSessionTokenCurrent(token)) {
          refreshed.forEach((automation) => workspace.dispatch({ type: 'automations/upsert', automation }));
          setSubscriptionRefreshGeneration((generation) => generation + 1);
        }
      } catch {
        // Ignored: Subscriptions surfaces its own per-channel recovery state.
      }
    }
    if (result.selected_keys.length) {
      // Best-effort, bounded warming of the first shelf. Home opens regardless
      // of whether provider or artwork warming succeeds, fails, or times out.
      try {
        const snapshot = await Promise.race([
          getHomeRecommendations(),
          new Promise<null>((resolve) => window.setTimeout(() => resolve(null), 4000)),
        ]);
        if (snapshot && workspace.isSessionTokenCurrent(token)) { setHomeRecommendations(snapshot); setHomeRecommendationsError(null); }
      } catch {
        // Ignored: Home surfaces its own loading or degraded recommendation state.
      }
    }
    if (!workspace.isSessionTokenCurrent(token)) return;
    revealHomeAfterOnboarding(result.status, result.selected_keys);
  }

  async function handleOnboardingSkip() {
    if (!state.currentUser) return;
    setOnboardingError(null);
    setOnboarding((current) => ({ phase: 'preparing', keys: current?.keys ?? [] }));
    const token = workspace.captureSessionToken();
    let result: MemberOnboardingState;
    try {
      result = await skipOnboarding();
    } catch {
      if (workspace.isSessionTokenCurrent(token)) { setOnboarding((current) => ({ phase: 'setup', keys: current?.keys ?? [] })); setOnboardingError('We could not update your setup just now. Please try again.'); }
      return;
    }
    if (!workspace.isSessionTokenCurrent(token)) return;
    revealHomeAfterOnboarding(result.status, result.selected_keys);
  }

  async function runExplore(queryInput: string, limit = EXPLORE_PAGE) {
    const query = queryInput.trim();
    if (!query) { openPaletteMode('search'); return; }
    if (isUrl(query)) {
      await openRemote({ title: 'Loading video…', webpage_url: query });
      return;
    }
    if (!leaveSettingsOk()) return;
    setExploreQuery(query); setExploreLoading(true); setExploreError(null); setExploreRail(null); setSurface('streaming'); setStreamingView('search');
    if (limit === EXPLORE_PAGE) recordSearchHistoryStable(query);
    // Only the newest search may land: a slow earlier response never replaces it.
    const request = ++exploreRequestRef.current;
    const [remote, local] = await Promise.allSettled([sourceSearch({ query, limit }), searchLibrary(query)]);
    if (request !== exploreRequestRef.current) return;
    if (remote.status === 'rejected') setExploreError(remote.reason instanceof Error ? remote.reason.message : 'Unable to search videos');
    setExploreResults({
      items: remote.status === 'fulfilled' ? remote.value.items || [] : [],
      errors: remote.status === 'fulfilled' ? remote.value.errors || [] : [],
      library: local.status === 'fulfilled' ? local.value.items.filter((item) => item.status !== 'missing') : [],
      titles: local.status === 'fulfilled' ? (local.value.matches ?? []).flatMap((match) => (match.kind === 'title' && match.media_title ? [match.media_title] : [])) : [],
      limit,
    });
    setExploreLoading(false);
  }

  // Only openRoute's own load keeps the link time it just set; every other Play starts from the saved position.
  /** `early`: the item's progress, already requested beside a full item (openRoute), so neither is fetched again here. */
  function openLibrary(item: LibraryItem, keepStart = false, early?: Promise<PlaybackProgress | null>) {
    if (!keepStart) setWatchStart(null);
    if (libraryWatchItem?.id === item.id && surface !== 'watch') { navigate('watch'); return; }
    if (!leaveSettingsOk()) return;
    notePlayIntent({ keepRecent: true }); // TTFF; keeps openRoute's earlier mark
    holdBackground(); // the page's side requests wait for this Play's first frame, so the video's own request is not queued behind them
    const sessionToken = workspace.captureSessionToken();
    acquisition.reset();
    const playbackRequest = ++playbackLoadRequestRef.current;
    curationMutationGenerationRef.current += 1;
    activeLibraryItemIdRef.current = item.id;
    setPreviousSurface(surface === 'watch' ? previousSurface : surface); setLibraryWatchItem(item); setActivePlayback(null); setPlaybackLoading(true); setSurface('watch'); closePalette(); window.scrollTo({ top: 0, behavior: prefersReducedMotion ? 'auto' : 'smooth' });
    if (early) workspace.dispatch({ type: 'library/upsert', item });
    else void getLibraryItem(item.id).then((full) => {
      if (playbackRequest !== playbackLoadRequestRef.current || !workspace.isSessionTokenCurrent(sessionToken) || activeLibraryItemIdRef.current !== item.id) return;
      setLibraryWatchItem((current) => current?.id === full.id ? full : current);
      workspace.dispatch({ type: 'library/upsert', item: full });
    }).catch(() => {
      if (playbackRequest === playbackLoadRequestRef.current && workspace.isSessionTokenCurrent(sessionToken) && activeLibraryItemIdRef.current === item.id) setError('Unable to load this Library item.');
    });
    void (early ?? getPlaybackProgress(item.id)).then((progress) => {
      if (playbackRequest !== playbackLoadRequestRef.current || !workspace.isSessionTokenCurrent(sessionToken) || activeLibraryItemIdRef.current !== item.id) return;
      setActivePlayback(progress);
    }).catch(() => {
      if (playbackRequest === playbackLoadRequestRef.current && workspace.isSessionTokenCurrent(sessionToken) && activeLibraryItemIdRef.current === item.id) reportMessage('Playback history is unavailable. Starting from the beginning.');
    }).finally(() => {
      // The player mounts once its start point is known; the item refresh above no longer gates it.
      if (playbackRequest === playbackLoadRequestRef.current && workspace.isSessionTokenCurrent(sessionToken) && activeLibraryItemIdRef.current === item.id) setPlaybackLoading(false);
    });
  }

  async function openRemote(item: YouTubeSearchResult) {
    if (item.reco) recordRecoOpen(item.reco);
    if (!leaveSettingsOk()) return;
    const opening = acquisition.open(item);
    if (!opening) return;
    playbackLoadRequestRef.current += 1; curationMutationGenerationRef.current += 1; activeLibraryItemIdRef.current = null; setPlaybackLoading(false); setActivePlayback(null);
    setLibraryWatchItem(null); setPreviousSurface(surface === 'watch' ? previousSurface : surface); setSurface('watch'); closePalette(); setDownloadMenuOpen(false); window.scrollTo({ top: 0, behavior: prefersReducedMotion ? 'auto' : 'smooth' });
    // Search history tracks queries the member typed, not videos opened directly.
    await opening;
  }

  /** One follow path for the watch page and the channel page; a failure throws to the caller. */
  async function followChannel(label: string, sourceUrl: string): Promise<SourceAutomation> {
    const existing = findChannelBySource(channelAutomations, sourceUrl);
    if (existing) return existing;
    const sessionToken = workspace.captureSessionToken();
    const defaults = state.userSettings?.resolved_automation_defaults;
    const hadSuppression = followHadSuppression(suppressions, label);
    const automation = await createAutomation({ label, source_url: sourceUrl, source_type: 'channel', cron_expression: defaults?.cron_expression || '0 */6 * * *', active: true, auto_download: false, format_selection: defaults?.format_selection || buildAcquisitionFormatSelection('best', state.preferences), output_profile: defaults?.output_profile ? { ...defaults.output_profile, base_path: null } : { base_path: null, subdir: '', template: '%(title)s - %(uploader)s [%(id)s].%(ext)s', organize_by: 'uploader' }, rules: defaults?.rules || { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video', min_duration: null, max_duration: null, max_age_days: null }, duplicate_policy: defaults?.duplicate_policy || 'skip_same_source', max_items_per_run: defaults?.max_items_per_run || 20, max_items_per_day: defaults?.max_items_per_day || null, backfill_limit: defaults?.backfill_limit || 10 });
    if (!workspace.isSessionTokenCurrent(sessionToken)) throw new Error('stale session');
    workspace.dispatch({ type: 'automations/upsert', automation });
    setSubscriptionRefreshGeneration((generation) => generation + 1);
    reportMessage(hadSuppression ? `Following ${label}. We'll recommend ${label} again.` : `Following ${label}.`);
    if (hadSuppression) void refreshSuppressions();
    return automation;
  }

  async function followCurrentChannel() {
    const sessionToken = workspace.captureSessionToken();
    if (!watchSelection) return;
    const raw = watchSelection.kind === 'remote' ? watchSelection.preview?.raw : watchSelection.item.metadata_json;
    const label = readString(raw, 'uploader') || readString(raw, 'channel') || (watchSelection.kind === 'remote' ? watchSelection.item.uploader : watchSelection.item.uploader);
    if (!label) { setError('This video does not expose a channel name.'); return; }
    const sourceUrl = readString(raw, 'channel_url') || readString(raw, 'uploader_url');
    if (!sourceUrl) { setError('Lumina could not find this channel’s address.'); return; }
    if (findChannelBySource(channelAutomations, sourceUrl)) { reportMessage(`${label} is already part of your subscriptions.`); return; }
    try {
      await followChannel(label, sourceUrl);
    } catch (followError) { if (workspace.isSessionTokenCurrent(sessionToken)) setError(followError instanceof Error ? followError.message : 'Unable to follow this channel'); }
  }

  async function handleRefresh() {
    setRefreshing(true);
    try {
      await workspace.refreshLibraryCollection();
    } finally {
      setRefreshing(false);
    }
  }
  async function handlePlaybackCheckpoint(item: LibraryItem, position: number, duration: number | null, completed: boolean) { const sessionToken = workspace.captureSessionToken(); const mutation = playbackMutationRef.current.catch(() => undefined).then(async () => { if (!workspace.isSessionTokenCurrent(sessionToken)) return; try { const saved = await updatePlaybackProgress(item.id, { position_seconds: position, duration_seconds: duration, completed }); if (!saved?.item_id || !workspace.isSessionTokenCurrent(sessionToken)) return; if (activeLibraryItemIdRef.current === item.id) setActivePlayback(saved); workspace.dispatch({ type: 'continueWatching/checkpoint', progress: saved }); } catch { /* Playback remains available when history is unavailable. */ } }); playbackMutationRef.current = mutation; await mutation; }
  async function handleRemotePlaybackCacheChange(value: RemotePlaybackCachePreferences) {
    const sessionToken = workspace.captureSessionToken();
    try {
      await updateMySettings({ remote_playback_cache: value });
      if (workspace.isSessionTokenCurrent(sessionToken)) workspace.dispatch({ type: 'settings/remotePlaybackCache', value });
    } catch (settingsError) {
      if (workspace.isSessionTokenCurrent(sessionToken)) setError(settingsError instanceof Error ? settingsError.message : 'Unable to save stream cache preference');
      throw settingsError;
    }
  }
  async function handleRestartPlayback(item: LibraryItem) { await handlePlaybackCheckpoint(item, 0, item.duration || activePlayback?.duration_seconds || null, false); }
  async function handleClearPlayback(item: LibraryItem) { const sessionToken = workspace.captureSessionToken(); const mutation = playbackMutationRef.current.catch(() => undefined).then(async () => { if (!workspace.isSessionTokenCurrent(sessionToken)) return; try { await clearPlaybackProgress(item.id); if (!workspace.isSessionTokenCurrent(sessionToken)) return; if (activeLibraryItemIdRef.current === item.id) setActivePlayback(null); workspace.dispatch({ type: 'continueWatching/remove', itemId: item.id }); reportMessage('Playback progress cleared.'); } catch { if (workspace.isSessionTokenCurrent(sessionToken)) setError('Unable to clear playback progress'); } }); playbackMutationRef.current = mutation; await mutation; }
  async function handleCancel(job: DownloadJob) { const token = workspace.captureSessionToken(); try { const next = await cancelJob(job.id); if (workspace.isSessionTokenCurrent(token)) workspace.dispatch({ type: 'jobs/upsert', job: next }); } catch (cancelError) { if (workspace.isSessionTokenCurrent(token)) setError(cancelError instanceof Error ? cancelError.message : 'Unable to cancel download'); } }
  async function handleRetry(job: DownloadJob) {
    const token = workspace.captureSessionToken();
    try {
      // A batch save's job retries through its batch entry; the new job arrives over realtime.
      if (job.acquisition_batch_id && job.acquisition_entry_id) {
        await retryAcquisitionEntry(job.acquisition_batch_id, job.acquisition_entry_id);
        if (!workspace.isSessionTokenCurrent(token)) return;
        setBatchRefreshGeneration((generation) => generation + 1);
      } else {
        const next = await retryJob(job.id);
        if (!workspace.isSessionTokenCurrent(token)) return;
        workspace.dispatch({ type: 'jobs/upsert', job: next });
      }
      reportMessage('Download queued again.');
    } catch (retryError) { if (workspace.isSessionTokenCurrent(token)) throw retryError; }
  }
  async function handleClear() { const token = workspace.captureSessionToken(); try { await clearCompletedJobs(); if (workspace.isSessionTokenCurrent(token)) workspace.dispatch({ type: 'jobs/removeCompleted' }); } catch (clearError) { if (workspace.isSessionTokenCurrent(token)) setError(clearError instanceof Error ? clearError.message : 'Unable to clear completed downloads'); } }
  async function mutateCuration<T>(itemId: string, action: () => Promise<T>, apply: (value: T) => void, success: string) {
    const token = workspace.captureSessionToken();
    const mutationGeneration = ++curationMutationGenerationRef.current;
    const isCurrent = () => workspace.isSessionTokenCurrent(token)
      && activeLibraryItemIdRef.current === itemId
      && curationMutationGenerationRef.current === mutationGeneration;
    setCurationBusy(true);
    setCurationError(null);
    try {
      const value = await action();
      if (!isCurrent()) return;
      apply(value);
      reportMessage(success);
    } catch (curationFailure) {
      if (isCurrent()) {
        setCurationError(curationFailure instanceof Error ? curationFailure.message : 'Unable to save this Library change.');
        throw curationFailure;
      }
    } finally {
      if (isCurrent()) setCurationBusy(false);
    }
  }
  async function handleVisibilityChange(visibility: 'private' | 'shared') {
    const item = watchSelection?.kind === 'library' ? watchSelection.item : null;
    if (!item) return;
    await mutateCuration(
      item.id,
      () => updateLibraryItemVisibility(item.id, visibility),
      (updated) => { setLibraryWatchItem(updated); workspace.dispatch({ type: 'library/upsert', item: updated }); },
      visibility === 'shared' ? 'This item is shared with the household.' : 'This item is private.',
    );
  }
  async function handleAddCurationTag(tag: string) {
    const item = watchSelection?.kind === 'library' ? watchSelection.item : null;
    if (!item) return;
    await mutateCuration(item.id, () => createLibraryTag(item.id, tag), (created) => setCurationTags((current) => current.some((entry) => entry.id === created.id) ? current : [...current, created]), 'Private tag added.');
  }
  async function handleDeleteCurationTag(tagId: string) {
    const item = watchSelection?.kind === 'library' ? watchSelection.item : null;
    const tag = curationTags.find((entry) => entry.id === tagId);
    if (!item || !tag) return;
    await mutateCuration(item.id, () => deleteLibraryTag(item.id, tag.tag), () => setCurationTags((current) => current.filter((entry) => entry.id !== tagId)), 'Private tag deleted.');
  }
  function handleAutomationChange(automation: SourceAutomation) { workspace.dispatch({ type: 'automations/upsert', automation }); }
  function handleAutomationRemoved(id: string) { workspace.dispatch({ type: 'automations/remove', id }); }
  async function recoverSubscription(automation: SourceAutomation) {
    const token = workspace.captureSessionToken();
    const run = await runAutomation(automation.id);
    if (!workspace.isSessionTokenCurrent(token)) throw new DOMException('Request cancelled.', 'AbortError');
    const refreshed = await listAutomations();
    if (!workspace.isSessionTokenCurrent(token)) throw new DOMException('Request cancelled.', 'AbortError');
    refreshed.forEach((automation) => workspace.dispatch({ type: 'automations/upsert', automation }));
    if (run.status === 'failed') throw new Error(run.error || 'This channel check failed again.');
  }

  function updateAcquisitionDefaults(patch: Partial<AcquisitionDefaults>) {
    if (patch.formatPreset !== undefined) acquisition.chooseFormat(patch.formatPreset);
    const preferencesPatch: Partial<AcquisitionDefaults> = {};
    if (patch.outputContainer !== undefined) preferencesPatch.outputContainer = patch.outputContainer;
    if (patch.downloadSubtitles !== undefined) preferencesPatch.downloadSubtitles = patch.downloadSubtitles;
    if (patch.outputFolder !== undefined) preferencesPatch.outputFolder = patch.outputFolder;
    workspace.dispatch({ type: 'preferences/patch', patch: preferencesPatch });
  }

  const collectionAnnouncer = <CollectionAnnouncer jobsRetrying={state.jobsRetrying} jobsState={state.jobsState} libraryRetrying={state.libraryRetrying} libraryState={state.libraryState} />;
  // Memoised above the signed-out return (hooks stay unconditional) so memo(Sidebar) sees a stable `extra`.
  const moreName = state.currentUser ? state.currentUser.display_name || state.currentUser.username : '';
  const setStreamingProviders = useCallback((streamingProviders: OptionalProvider[]) => workspace.dispatch({ type: 'preferences/patch', patch: { streamingProviders } }), [workspace]);
  const moreSwitch = useStableCallback(() => { closeMobileNavigation(); openMemberPicker(); });
  const moreAddLink = useStableCallback(() => { closeMobileNavigation(); openPaletteMode('link'); });
  const moreSignOut = useStableCallback(() => { closeMobileNavigation(); void handleLogout(); });
  const moreItems = useMemo(() => (
    <div className="g-nav-group">
      <p aria-hidden="true" className="g-label g-nav-group-label">{moreName}</p>
      <button className="g-nav-item" onClick={moreSwitch} type="button"><span>Switch member…</span></button>
      {openSearchBlocked ? null : <button className="g-nav-item" onClick={moreAddLink} type="button"><span>Add a link</span></button>}
      <button className="g-nav-item" onClick={moreSignOut} type="button"><span>Sign out</span></button>
    </div>
  ), [moreName, moreSwitch, moreAddLink, moreSignOut, openSearchBlocked]);
  if (accountLink) return <>{collectionAnnouncer}<AuthScreen busy={authBusy} error={error} onCancel={() => { setAccountLink(null); setError(null); }} onLogin={handleLogin} onRetrySession={handleRetrySession} onSetup={handleAccountLink} onSwitched={() => void handleSwitched()} sessionProblem={null} stage={accountLink.kind} /></>;
  if (state.authStage !== 'ready' || !state.currentUser) return <>{collectionAnnouncer}<AuthScreen busy={authBusy} error={error} onLogin={handleLogin} onRetrySession={handleRetrySession} onSetup={handleSetup} onSwitched={() => void handleSwitched()} sessionProblem={state.sessionProblem} stage={state.authStage === 'ready' ? 'loading' : state.authStage} twoFactor={twoFactor} /></>;

  if (onboarding?.phase === 'discover') {
    const followedIdentities = new Set(channelAutomations.map((channel) => normalizeChannelAddress(channel.source_url)).filter((value): value is string => Boolean(value)));
    return <>{collectionAnnouncer}<Suspense fallback={surfaceLoading}><ChannelDiscoveryStep error={onboardingError} onBack={() => handleDiscoveryBack(onboarding.keys)} onContinue={(follows) => void handleOnboardingComplete(onboarding.keys, follows)} onRetry={() => void loadChannelSuggestions(onboarding.keys)} onResolveAddress={(value) => resolveChannelAddressCandidate(value, followedIdentities)} onSearch={handleChannelSearch} reducedMotion={prefersReducedMotion} suggestions={channelSuggestions} suggestionsError={channelSuggestionsError} suggestionsLoading={channelSuggestionsLoading} /></Suspense></>;
  }
  if (onboarding) return <>{collectionAnnouncer}<Suspense fallback={surfaceLoading}><OnboardingSurface busy={onboarding.phase === 'preparing'} categories={memberInterests?.categories ?? []} error={onboardingError} initialSelectedKeys={onboarding.keys.length ? onboarding.keys : memberInterests?.selected_keys ?? []} onContinue={handleInterestsContinue} onSkip={handleOnboardingSkip} phase={onboarding.phase} reducedMotion={prefersReducedMotion} /></Suspense></>;

  const curationItem = watchSelection?.kind === 'library' ? watchSelection.item : null;
  const curationDataMatchesItem = Boolean(curationItem && curationDataItemId === curationItem.id);
  const curation = curationItem ? (
    <div aria-busy={curationLoading || !curationDataMatchesItem} className="watch-curation">
      {curationLoading || !curationDataMatchesItem ? <p aria-live="polite" role="status">Loading Library details…</p> : null}
      <LibraryCurationControls
        busy={curationBusy || curationLoading || !curationDataMatchesItem}
        canManageItem={curationItem.user_id === state.currentUser.id || (!curationItem.user_id && state.currentUser.role === 'admin')}
        error={curationDataMatchesItem ? curationError : null}
        item={{ id: curationItem.id, title: curationItem.title, visibility: curationItem.visibility || 'private', ownerDisplayName: curationItem.owner_display_name || curationItem.owner_username || 'the vault owner', ownedByCurrentMember: curationItem.user_id === state.currentUser.id || (!curationItem.user_id && state.currentUser.role === 'admin') }}
        key={curationItem.id}
        onAddTag={handleAddCurationTag}
        onDeleteTag={handleDeleteCurationTag}
        onVisibilityChange={handleVisibilityChange}
        tags={(curationDataMatchesItem ? curationTags : []).map((tag) => ({ id: tag.id, label: tag.tag }))}
      />
    </div>
  ) : undefined;
  const acquisitionDefaults = {
    formatPreset: acquisition.state.format,
    outputContainer: state.preferences.outputContainer,
    downloadSubtitles: state.preferences.downloadSubtitles,
    outputFolder: state.preferences.outputFolder,
  } as const;
  const acquisitionDefaultsSettings = <SourceSettings onChange={updateAcquisitionDefaults} value={acquisitionDefaults} />;
  const batchPlan = acquisitionPlanForSource(acquisitionDefaults);
  const playlistAcquisition = watchSelection?.kind === 'remote' && watchSelection.preview?.kind === 'playlist' ? (
    <PlaylistAcquisitionPanel
      key={`${state.currentUser.id}:${watchSelection.preview.webpage_url || watchSelection.item.webpage_url || watchSelection.item.id || ''}`}
      formatSelection={batchPlan.formatSelection}
      onOpenEntry={openRemote}
      onQueued={() => { setBatchRefreshGeneration((generation) => generation + 1); reportMessage('Selected playlist videos were added to vault activity.'); }}
      outputProfile={{ ...batchPlan.outputProfile, organize_by: 'playlist' }}
      preview={watchSelection.preview}
    />
  ) : undefined;
  const collectionsRoute: CollectionsRoute = libraryLocation.collections === 'detail' && libraryLocation.collectionId
    ? { collections: 'detail', collectionId: libraryLocation.collectionId }
    : { collections: 'list', ...(libraryLocation.create ? { create: libraryLocation.create } : {}) };
  const collectionsProps = {
    currentUserId: state.currentUser.id,
    formatSelection: batchPlan.formatSelection,
    isQueueing: acquisition.isQueueing,
    key: state.currentUser.id,
    library: state.library,
    onBackToList: () => openRoute({ surface: 'library', collections: 'list' }),
    onOpenCollection: (collectionId: string) => openRoute({ surface: 'library', collections: 'detail', collectionId }),
    onOpenLibrary: (libraryItemId: string) => openRoute({ surface: 'watch', libraryId: libraryItemId }),
    onOpenRemote: openRemoteStable,
    onOpenTitle: openTitleStable,
    onQueueRemote: queueRemoteStable,
    // `?new=` opened a form: drop it from the address without a history entry.
    onRouteHandled: () => { window.history.replaceState(null, '', '/library/collections'); setLibraryLocation({ collections: 'list' }); },
    outputProfile: batchPlan.outputProfile,
  };
  const householdCollections = <CollectionsPage {...collectionsProps} embedded onBackToList={() => openPlaceStable('collections')} route={{ collections: 'list' }} />;
  const collectionsPage = (lenses: ReactNode) => <CollectionsPage {...collectionsProps} lenses={lenses} route={collectionsRoute} />;
  const playlistActivity = <AcquisitionBatchActivity key={state.currentUser.id} refreshKey={batchRefreshGeneration} />;
  const watchSurfaceKey = watchSelection?.kind === 'library'
    ? `library:${watchSelection.item.id}`
    : watchSelection?.kind === 'remote'
      ? remoteSourceIdentity(watchSelection.item, watchSelection.preview?.raw || {}) || `remote:${watchSelection.item.webpage_url || watchSelection.item.id || 'unknown'}`
      : 'watch:none';
  const channelsElement = <ChannelsSurface channelId={channelId} channels={channelAutomations} commands={subscriptionCommands} isQueueing={acquisition.isQueueing} library={state.library} onAutomationChange={handleAutomationChange} onAutomationRemoved={handleAutomationRemoved} onExplore={() => openRoute({ surface: 'streaming', view: 'home' })} onOpen={openRemoteStable} onOpenChannel={(id) => { setChannelId(id); setSurface(id ? 'subscriptions' : 'streaming'); setStreamingView('channels'); window.scrollTo({ top: 0 }); }} onQueue={queueRemoteStable} onRecover={recoverSubscription} onRetry={() => { subscriptionRetryFocusRequestedRef.current = true; setSubscriptionRefreshGeneration((generation) => generation + 1); }} outcomes={subscriptionOutcomes} live={liveDiscovery} refreshing={subscriptionRefreshing} />;
  // Twitch on / Kick off until the member's choices resolver supplies the list.
  const streamingElement = <StreamingSurface channels={channelsElement} fromChannels={subscriptionVideos} explore={{ error: exploreError, isQueueing: acquisition.isQueueing, library: state.library, loading: exploreLoading, onOpen: openRemoteStable, onOpenTitle: openTitleStable, onQueue: queueRemoteStable, onSearch: runExploreStable, popular: popularDiscovery, popularError: popularDiscoveryError, libraryResults: exploreResults.library, onLoadMore: exploreResults.limit < EXPLORE_MAX && exploreResults.items.length >= exploreResults.limit ? loadMoreExploreStable : undefined, onOpenLibrary: openLibraryStable, query: exploreQuery, results: exploreResults.items, sourceErrors: exploreResults.errors, titleResults: exploreResults.titles, rail: exploreRail, onRailChange: setExploreRail }} live={{ channels: channelAutomations, error: liveDiscoveryError, isQueueing: acquisition.isQueueing, library: state.library, onOpen: openRemoteStable, onQueue: queueRemoteStable, onRailChange: setLiveRail, onRetry: () => setLiveGeneration((value) => value + 1), rail: liveRail, settings: { commands: subscriptionCommands, onChange: handleAutomationChange, onRecover: recoverSubscription, onRemoved: handleAutomationRemoved }, snapshot: liveDiscovery }} onNavigate={(next) => openRouteStable({ surface: 'streaming', ...next })} provider={streamingProvider} providers={(['youtube', ...state.preferences.streamingProviders] as StreamingProvider[]).filter((kind) => !providerBlocked(access, kind))} query={exploreQuery} rail={(streamingView === 'live' ? liveRail : exploreRail) ?? undefined} view={streamingView} />;
  const gate = watchGate(accessValue);
  const surfaceContent = surface === 'home' ? <HomeSurface channels={channelAutomations} continueWatching={state.continueWatching} homeShelves={state.preferences.homeShelves} interests={memberInterests} isQueueing={acquisition.isQueueing} library={state.library} libraryProblem={state.libraryProblem} libraryRetrying={state.libraryRetrying} libraryState={state.libraryState} onHideContinueWatching={hideContinueWatchingStable} onHomeShelvesChange={homeShelvesChangeStable} onNavigate={navigateStable} onOpenLibrary={openLibraryStable} onOpenQueued={openQueuedStable} onOpenRemote={openRemoteStable} onOpenRoute={openRouteStable} onOpenTitle={openTitleStable} onPersonalize={openInterestSettingsStable} onPlay={openLibraryIdStable} onQueueRemote={queueRemoteStable} onRestoreContinueWatching={restoreContinueWatchingStable} onRetryLibrary={retryLibrary} onRetryRecommendations={retryRecommendations} onSignIn={returnToSignIn} recentLibrary={state.libraryRecent} recommendationError={homeRecommendationsError} recommendations={homeRecommendations} recommendationsLoading={homeRecommendationsLoading} subscriptionOutcomes={subscriptionOutcomes} subscriptionVideos={subscriptionVideos} user={state.currentUser} />
    : surface === 'streaming' ? streamingElement
      : surface === 'subscriptions' ? (
        channelPage && 'url' in channelPage ? <ChannelResolver key={channelPage.url} url={channelPage.url} />
          : channelPage ? <ChannelPage channelId={channelPage.id} commands={subscriptionCommands} follows={channelAutomations} isQueueing={acquisition.isQueueing} key={channelPage.id} onAutomationChange={handleAutomationChange} onAutomationRemoved={handleAutomationRemoved} onFollow={({ name, url }) => followChannel(name, url)} onOpen={openRemoteStable} onPlayLibrary={openLibraryStable} onQueue={queueRemoteStable} onRecover={recoverSubscription} onTabChange={(tab) => { replaceHistoryRef.current = (tab ?? 'videos') !== (channelPage.tab ?? 'videos'); setChannelPage({ id: channelPage.id, tab }); }} tab={channelPage.tab} />
            : channelsElement
      )
        : surface === 'library' ? (libraryLocation.titleId && libraryLocation.edit
          ? <TitleEditorPage id={libraryLocation.titleId} key={libraryLocation.titleId} onBack={() => openRoute({ surface: 'library', titleId: libraryLocation.titleId! })} onTab={(tab) => { replaceHistoryRef.current = true; openRoute({ surface: 'library', titleId: libraryLocation.titleId!, edit: true, tab }); }} tab={libraryLocation.tab ?? null} user={state.currentUser} />
          : libraryLocation.titleId
          ? <TitleDetailPage backLabel={titleBackLabel()} id={libraryLocation.titleId} key={libraryLocation.titleId} onBack={titleBackStable} onEdit={(titleId) => paletteNavigate(`/title/${encodeURIComponent(titleId)}/edit`)} onOpenTitle={openTitleStable} onPlay={openLibraryIdStable} onSearch={runExploreStable} onSeason={(season) => setLibraryLocation((current) => ({ ...current, season }))} season={libraryLocation.season ?? null} user={state.currentUser} />
          : <LibraryBrowser collections={householdCollections} collectionsPage={collectionsPage} currentUser={state.currentUser} onItemChanged={libraryItemChangedStable} onOpenTitle={openTitleStable} onPlay={openLibraryStable} onPlayAt={(libraryId, startSeconds) => openRoute({ surface: 'watch', libraryId, startSeconds })} onViewChange={openPlaceStable} onWallChange={(wall) => setLibraryLocation((current) => (wall ? { view: current.view, wall } : { view: current.view }))} view={libraryLocation.collections ? 'collections' : libraryLocation.view ?? 'all'} wall={libraryLocation.wall} />)
            : surface === 'downloads' ? <DownloadsSurface hasMoreJobs={state.jobsNextCursor !== null} jobs={state.jobs} loadingMoreJobs={state.jobsLoadingMore} loadMoreJobsError={state.jobsLoadMoreError} loadState={state.jobsState} onCancel={handleCancelStable} onClear={handleClear} onLoadMoreJobs={loadMoreJobsStable} onOpenItem={openLibraryIdStable} onRetry={handleRetryStable} onRetryLoad={retryJobs} onSignIn={returnToSignIn} playlistActivity={playlistActivity} recordingActivity={<LiveRecordingActivity key={`recordings:${state.currentUser.id}`} onOpenItem={openLibraryIdStable} session={workspace} />} problem={state.jobsProblem} retryingLoad={state.jobsRetrying} />
              : surface === 'requests' ? <RequestsSurface key={state.currentUser.id} onOpenRoute={openRouteStable} onQueueChanged={requestsQueueChangedStable} onRoute={requestsRouteStable} route={requestsRoute} session={workspace} user={state.currentUser} />
              : surface === 'settings' ? <SettingsSurface acquisitionDefaults={acquisitionDefaultsSettings} advanced={state.preferences.settingsAdvanced} onAdvancedChange={(settingsAdvanced) => workspace.dispatch({ type: 'preferences/patch', patch: { settingsAdvanced } })} captions={state.preferences.captions} onCaptionsChange={(captions) => workspace.dispatch({ type: 'preferences/patch', patch: { captions } })} autoplayUpNext={state.preferences.autoplayUpNext} onAutoplayUpNextChange={(autoplayUpNext) => workspace.dispatch({ type: 'preferences/patch', patch: { autoplayUpNext } })} playbackMaxHeight={state.preferences.playbackMaxHeight} onPlaybackMaxHeightChange={(playbackMaxHeight) => workspace.dispatch({ type: 'preferences/patch', patch: { playbackMaxHeight } })} onClearSearchHistory={clearSearchHistoryStable} onUpdateDisplayName={updateDisplayNameStable} searchHistory={searchHistory} onThemeChange={(theme) => workspace.dispatch({ type: 'preferences/patch', patch: { theme } })} theme={state.preferences.theme} formatPreset={acquisition.state.format} interests={memberInterests} onFormatChange={acquisition.chooseFormat} onLogout={handleLogout} playbackPrefs={playbackPrefs} onPlaybackPrefsChange={patchPlaybackPrefsStable} onRemotePlaybackCacheChange={handleRemotePlaybackCacheChange} onRestoreSuppression={restoreSuppressionStable} onSaveInterests={saveMemberInterests} remotePlaybackCache={state.userSettings?.remote_playback_cache || undefined} key={state.currentUser.id} onMessage={reportMessage} onSection={setSettingsSection} section={settingsSection} member={settingsMember} onMember={setSettingsMember} suppressions={suppressions} user={state.currentUser} />
                : watchSelection ? null : surfaceLoading;
  // The one player owner: Watch and the mini-player are the same mounted
  // surface in a stable slot, so the media element never remounts on navigation.
  const watchPlayer = watchSelection && !gate ? <SurfaceBoundary key={watchSurfaceKey}><Suspense fallback={surface === 'watch' ? surfaceLoading : null}><WatchSurface sourceProblem={acquisition.state.failure?.phase === 'preview' ? acquisition.state.failure.message : null} mini={surface !== 'watch'} onClose={resetWatch} onExpand={() => navigate('watch')} autoplayUpNext={state.preferences.autoplayUpNext} busy={watchSelection.kind === 'remote' && acquisition.isQueueing(watchSelection.item)} channels={channelAutomations} curation={curation} downloadMenuOpen={downloadMenuOpen} downloadQuality={acquisition.state.format} jobs={state.jobs} key={watchSurfaceKey} library={state.library} onAutoplayUpNextChange={(autoplayUpNext) => workspace.dispatch({ type: 'preferences/patch', patch: { autoplayUpNext } })} onBack={leaveWatch} onClearPlayback={handleClearPlayback} onCloseDownloadMenu={() => setDownloadMenuOpen(false)} onDownload={acquisition.queueSelection} onDownloadQuality={acquisition.chooseFormat} onFollow={followCurrentChannel} onOpenDownloads={() => navigate('downloads')} onOpenLibraryItem={(libraryId) => openRoute({ surface: 'watch', libraryId })} onOpenQueued={(entry) => entry.ref.library_item_id ? openRoute({ surface: 'watch', libraryId: entry.ref.library_item_id }) : void openRemote(queuedAsRemote(entry))} onOpenRelated={openRemote} onPlaybackCheckpoint={handlePlaybackCheckpoint} onReacquireRemote={async (item) => Boolean(await acquisition.open(item))} onRestart={handleRestartPlayback} onTheaterModeChange={(theaterMode) => workspace.dispatch({ type: 'preferences/patch', patch: { theaterMode } })} onToggleDownloadMenu={() => setDownloadMenuOpen((value) => !value)} playback={activePlayback} playlistAcquisition={playlistAcquisition} previewLoading={acquisition.state.previewing || (watchSelection.kind === 'library' && playbackLoading)} related={acquisition.state.related} captions={state.preferences.captions} onCaptionsChange={(captions) => workspace.dispatch({ type: 'preferences/patch', patch: { captions } })} playerVolume={state.preferences.playerVolume} onPlayerVolumeChange={(playerVolume) => workspace.dispatch({ type: 'preferences/patch', patch: { playerVolume } })} selection={watchSelection} onOpenTitle={openTitleStable} onPlaybackPrefsChange={patchPlaybackPrefsStable} playbackPrefs={playbackPrefs} startSeconds={watchSelection.kind === 'library' && watchStart?.libraryId === watchSelection.item.id ? watchStart.seconds : null} startNonce={watchStart?.nonce} theaterMode={state.preferences.theaterMode} /></Suspense></SurfaceBoundary> : null;

  const content = surface === 'watch' && gate ? <WatchStopState gate={gate} onHome={() => navigate('home')} until={access?.until ?? null} />
    : (surface === 'streaming' || surface === 'subscriptions') && allStreamingBlocked(access) ? <BlockedSurface what="Streaming" />
      : surfaceContent;

  return (
    <AccessProvider value={accessValue}>
    <StreamingProvidersProvider enabled={state.preferences.streamingProviders} onChange={setStreamingProviders}>
    <AppShell
      activity={{ state: state.jobsState, retrying: state.jobsRetrying, active: activeDownloads }}
      announcer={collectionAnnouncer}
      compact={isCompact}
      drawer={isDrawer}
      extraSkipLinks={watchSelection && surface !== 'watch' ? <a className="g-skip-link" href="#mini-player" onClick={() => document.getElementById('mini-player')?.focus()}>Skip to mini player</a> : null}
      mainRef={appMainRef}
      menuButtonRef={mobileMenuButtonRef}
      mobile={isMobile}
      moreItems={moreItems}
      navigationOpen={mobileNavOpen}
      onAddLink={() => openPaletteMode('link')}
      onCloseNavigation={closeMobileNavigation}
      onMenu={() => {
        closePalette();
        setDownloadMenuOpen(false);
        if (isDrawer) setMobileNavOpen(true);
        else workspace.dispatch({ type: 'preferences/patch', patch: { sidebarCollapsed: !state.preferences.sidebarCollapsed } });
      }}
      onNavigate={navigateStable}
      pendingRequests={state.currentUser.role === 'admin' ? pendingRequests : 0}
      onSearch={() => openPaletteMode('search')}
      onSettings={() => navigate('settings')}
      onSignOut={() => void handleLogout()}
      onTheme={(theme) => workspace.dispatch({ type: 'preferences/patch', patch: { theme } })}
      sidebarCollapsed={state.preferences.sidebarCollapsed}
      surface={surface}
      theme={state.preferences.theme}
      user={state.currentUser}
    >
      <RecoFeedbackProvider onError={setError} onExpired={recoExpired} resetKey={`${state.currentUser.id}:${recoEpoch}`} restore={recoRestore} suppress={recoSuppress}>
        <ArtActionsProvider value={artActions}>
          <SurfaceBoundary key={surface}><Suspense fallback={surfaceLoading}>{content}</Suspense></SurfaceBoundary>
          <WatchFullscreenHost active={!gate && (Boolean(watchSelection) || surface === 'watch')}>{watchPlayer}</WatchFullscreenHost>
          {watchSelection && !gate ? <TimeLeftNotice minutes={access?.remaining_minutes ?? null} /> : null}
        </ArtActionsProvider>
      </RecoFeedbackProvider>
      <MemberPickerHost beforeChange={prepareMemberChange} currentUser={state.currentUser} onSwitched={() => void handleSwitched()} />
      {palette.open && paletteContext ? (
        <Suspense fallback={null}>
          <CommandPalette
            captureSessionToken={workspace.captureSessionToken}
            channels={channelAutomations}
            context={paletteContext}
            history={searchHistory}
            isSessionTokenCurrent={workspace.isSessionTokenCurrent}
            key={`${palette.key}:${state.currentUser.id}`}
            mode={palette.mode}
            onClearHistory={clearSearchHistoryStable}
            onClose={closePalette}
            onOpenChannel={(channel) => openRemoteStable({ title: channel.label, webpage_url: channel.source_url })}
            onOpenLibraryItem={openLibraryStable}
            onOpenMoment={(match) => { if (match.item) openRoute({ surface: 'watch', libraryId: match.item.id, startSeconds: Math.floor((match.start_ms ?? 0) / 1000) }); }}
            onOpenRemote={openRemoteStable}
            onOpenTitle={openTitleStable}
            onOpenUrl={(url) => openRemoteStable({ title: 'Loading video…', webpage_url: url })}
            onRemoveHistory={removeSearchHistoryEntryStable}
            onSearchEverything={runExploreStable}
            open
            sessionKey={state.currentUser.id}
            user={state.currentUser}
          />
        </Suspense>
      ) : null}
    </AppShell>
    </StreamingProvidersProvider>
    </AccessProvider>
  );
}
