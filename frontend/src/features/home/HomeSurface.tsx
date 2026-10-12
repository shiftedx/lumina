/**
 * Home: the masthead, a hero spotlight, then the member's shelves in their order and native shapes; a
 * fresh member gets one start card instead. "Edit home" swaps the hero, shelves and personalize prompt for
 * HomeEditor in place, and every change is saved at once through onHomeShelvesChange. Shell-state shelves cost
 * no request; fetched shelves, the watchlist and Live load only when near.
 */
import { Fragment, type KeyboardEvent, memo, type ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { dismissNextUp } from '../../api';
import { EDIT_HOME_EVENT, onCommand } from '../../app/commands';
import type { AppRoute, Surface } from '../../app/routes';
import { recordMetric, sinceNavigation } from '../../perfMetrics';
import type { SubscriptionChannelOutcome } from '../../subscriptionFeed';
import type { LibraryItem, MemberInterests, MemberRecommendationSnapshot, PlaybackProgress, SourceAutomation, TitleRow, TitleSummary, UserProfile, WatchQueueEntry, YouTubeSearchResult } from '../../types';
import type { CollectionLoadProblem, CollectionLoadState } from '../../workspace';
import { useToast } from '../../ui';
import { FOCUS_TARGETS, moveFocus } from '../media/focusNav';
import { CollectionRecovery, itemKey, StaleCollectionNotice } from '../media/MediaCards';
import { RemoteStillCard } from '../gallery/RemoteStillCard';
import { RecoCard, useRecoFilter, useRecoGeneration } from '../reco/recoFeedback';
import { remoteTarget } from '../reco/recoModel';
import { RecoPersonalScope, isPersonal } from '../reco/recoPersonal';
import { RecoImpressionScope, useRecoImpressions } from '../reco/useRecoImpressions';
import { loadWatchQueue, queuedAsRemote, useWatchQueue } from '../watch/WatchQueue';
import { cachedShelf, rememberShelf } from './homeCache';
import { HomeEditor } from './HomeEditor';
import { HomeHero } from './HomeHero';
import { FetchedShelf, HomeShelf, itemStill, NearGate, remoteStill, RemoveButton, type ShelfActions, titleStill, useNear } from './HomeShelf';
import { entryLeft, entryPercent, firstName, greeting, heroSlides, mastheadDate, titleCaption } from './homeModel';
import { HOME_CATALOGUE, HOME_SHELF_IDS, type HomeShelfId, type HomeShelfPref, normalizeHomeShelves, type ShelfStatus } from './homeShelves';
import { LiveShelf } from './LiveShelf';
import { createVisit, FETCHED_SHELVES, isFetchedShelf, SHELF_LIMIT } from './shelfSources';
import './home.css';

export type HomeSurfaceProps = {
  user: UserProfile;
  library: LibraryItem[];
  recentLibrary: LibraryItem[];
  continueWatching: PlaybackProgress[];
  subscriptionVideos: YouTubeSearchResult[];
  subscriptionOutcomes: SubscriptionChannelOutcome[];
  channels: SourceAutomation[];
  /** The member's raw ui_prefs.home_shelves (null = the default layout); normalised here. */
  homeShelves: HomeShelfPref[] | null;
  /** Every edit, saved at once; null = Reset to default. */
  onHomeShelvesChange: (next: HomeShelfPref[] | null) => void;
  onNavigate: (surface: Surface) => void;
  /** See all to a library tab. */
  onOpenRoute: (route: AppRoute) => void;
  onOpenLibrary: (item: LibraryItem) => void;
  onOpenRemote: (item: YouTubeSearchResult) => void;
  onOpenQueued?: (entry: WatchQueueEntry) => void;
  onOpenTitle: (title: TitleSummary) => void;
  /** The hero's Resume and Play. */
  onPlay: (itemId: string) => void;
  onQueueRemote: (item: YouTubeSearchResult) => void;
  isQueueing: (item: YouTubeSearchResult) => boolean;
  libraryState: CollectionLoadState;
  libraryProblem: CollectionLoadProblem | null;
  libraryRetrying: boolean;
  onRetryLibrary: () => void;
  onSignIn: () => void;
  interests?: MemberInterests | null;
  recommendations?: MemberRecommendationSnapshot | null;
  recommendationError?: string | null;
  recommendationsLoading?: boolean;
  onPersonalize?: () => void;
  onRetryRecommendations?: () => void;
  onHideContinueWatching?: (entry: PlaybackProgress) => Promise<void>;
  onRestoreContinueWatching?: (entry: PlaybackProgress) => Promise<void>;
};


/** The watchlist: the shared queue store, loaded once per session the first time this shelf comes near. */
function Watchlist({ onOpen }: { onOpen: (entry: WatchQueueEntry) => void }) {
  const { queue, loading, message } = useWatchQueue();
  const [ref, near] = useNear<HTMLDivElement>();
  useEffect(() => { if (near && !queue) void loadWatchQueue(); }, [near]); // eslint-disable-line react-hooks/exhaustive-deps -- once, when near
  const entries = (queue?.entries ?? []).filter((entry) => entry.availability === 'available').slice(0, SHELF_LIMIT);
  if (queue && !entries.length) return null;
  const failed = !queue && !loading && Boolean(message);
  return (
    <div className="h-slot" ref={ref}>
      <HomeShelf heading={HOME_CATALOGUE.watchlist.name} itemKey={(entry) => entry.id} items={entries} onRetry={() => void loadWatchQueue()} renderCard={(entry, slot) => remoteStill(queuedAsRemote(entry), slot, () => onOpen(entry))} sectionKey="watchlist" shape="still" state={queue ? 'ready' : failed ? 'failed' : 'loading'} />
    </div>
  );
}

type PickedShelfProps = {
  recommendations: MemberRecommendationSnapshot | null;
  recommendationError: string | null;
  recommendationsLoading: boolean;
  onRetry?: () => void;
  onPersonalize?: () => void;
  onOpenRemote: (item: YouTubeSearchResult) => void;
};

/** Picked for you: a still per pick with the server's reason and the menu under it. */
function PickedShelf({ recommendations, recommendationError, recommendationsLoading, onRetry, onPersonalize, onOpenRemote }: PickedShelfProps) {
  // A hidden pick keeps its slot as its Undo row; a gone one leaves the list.
  const items = useRecoFilter(recommendations?.items ?? [], remoteTarget, true).slice(0, SHELF_LIMIT);
  const observe = useRecoImpressions(items.find((item) => item.reco)?.reco?.list_id ?? null);
  const loading = !items.length && (!recommendations || recommendations.state === 'loading' || recommendationsLoading);
  const failed = !items.length && !loading && Boolean(recommendationError || recommendations?.state === 'failed');
  const state = recommendations?.state;
  // Today's partial, stale and empty lines.
  const line = items.length
    ? state === 'partial' ? 'A few interests are still warming up; available picks are shown now.' : recommendations?.stale ? 'Showing last-known picks while discovery refreshes.' : null
    : state === 'partial' ? 'Personalized picks are warming up. Some interests have not returned playable media yet.'
      : state === 'stale' ? 'Last-known picks are unavailable while discovery refreshes.'
        : state === 'empty' ? 'Nothing new for these interests yet. Try broadening your interests.' : null;
  return (
    <RecoPersonalScope value={isPersonal(items)}>
      <RecoImpressionScope value={observe}>
        <HomeShelf
          failedText={`Personalized discovery is taking a pause. ${recommendationError || recommendations?.error || 'Your Home collection is still available while discovery recovers.'}`}
          heading="Picked for you"
          itemKey={(item) => itemKey(item)}
          items={items}
          notes={line ? <p className="h-note">{line}</p> : null}
          onRetry={onRetry}
          renderCard={(item, slot) => (
            <RecoCard reco={item.reco} target={remoteTarget(item)}>
              <RemoteStillCard {...slot} item={item} onOpen={onOpenRemote} />
            </RecoCard>
          )}
          sectionKey="picked_for_you"
          seeAll={onPersonalize ? { label: 'Edit interests', onClick: onPersonalize } : null}
          shape="still"
          state={loading ? 'loading' : failed ? 'failed' : 'ready'}
        />
      </RecoImpressionScope>
    </RecoPersonalScope>
  );
}

const HERO_WAIT_MS = 800;

function HomeSurfaceView({
  user, library, recentLibrary, continueWatching, subscriptionVideos, subscriptionOutcomes, channels, homeShelves, onHomeShelvesChange,
  onNavigate, onOpenRoute, onOpenLibrary, onOpenRemote, onOpenQueued, onOpenTitle, onPlay, onQueueRemote, isQueueing,
  libraryState, libraryProblem, libraryRetrying, onRetryLibrary, onSignIn,
  interests = null, recommendations = null, recommendationError = null, recommendationsLoading = false, onPersonalize, onRetryRecommendations,
  onHideContinueWatching, onRestoreContinueWatching,
}: HomeSurfaceProps) {
  const { queue, loading: queueLoading, message: queueMessage } = useWatchQueue();
  const queueFailed = !queue && !queueLoading && Boolean(queueMessage);
  const layout = useMemo(() => normalizeHomeShelves(homeShelves), [homeShelves]);
  const visible = useMemo(() => new Set(layout.filter((shelf) => shelf.visible).map((shelf) => shelf.id)), [layout]);
  // Each closed Undo window starts a new visit, so the title rows re-fetch without the title the member just hid.
  const recoGeneration = useRecoGeneration();
  const visit = useMemo(createVisit, [recoGeneration]);
  const [statuses, setStatuses] = useState<Partial<Record<HomeShelfId, ShelfStatus>>>({});
  const setStatus = useCallback((id: HomeShelfId, status: ShelfStatus) => setStatuses((current) => (current[id] === status ? current : { ...current, [id]: status })), []);
  const [editing, setEditing] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const toast = useToast();
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(() => new Set());
  const [firstScreen, setFirstScreen] = useState(false);
  // P-M1: seeded from the session cache so a return visit paints the hero at once instead of popping in above the
  // shelves once /api/titles resolves.
  const [newest, setNewest] = useState<TitleSummary[] | null | undefined>(() => cachedShelf<TitleSummary[]>(user.id, 'newest'));
  const headingRef = useRef<HTMLHeadingElement>(null);
  const editRef = useRef<HTMLButtonElement>(null);
  const focusEditRef = useRef(false);
  const recoveryButtonRef = useRef<HTMLButtonElement>(null);
  const wasRetryingRef = useRef(false);
  const restoreFocusRef = useRef(false);

  // Hero: the Continue titles; else the newest, fetched now and shared with its shelf (9.2 row 4a).
  const needsNewest = !continueWatching.some((entry) => entry.title);
  useEffect(() => {
    if (!needsNewest || newest !== undefined) return undefined;
    let current = true;
    visit.newest().then((items) => { if (current) { rememberShelf(user.id, 'newest', items); setNewest(items); } }, () => { if (current) setNewest(null); });
    return () => { current = false; };
  }, [needsNewest, newest, visit]);
  // The CLS wait is capped: a slow /api/titles must not leave Home unpainted.
  const [waited, setWaited] = useState(false);
  useEffect(() => { const t = window.setTimeout(() => setWaited(true), HERO_WAIT_MS); return () => window.clearTimeout(t); }, []);
  const heroDecided = !needsNewest || newest !== undefined;
  // The hero's recommendations join its Continue slides when the visit's title rows arrive (shared with their shelves).
  const [titleRows, setTitleRows] = useState<TitleRow[] | null>(null);
  useEffect(() => {
    let current = true;
    visit.titleRows().then((response) => { if (current) setTitleRows(response.rows); }, () => undefined);
    return () => { current = false; };
  }, [visit]);
  const slides = useMemo(() => (heroDecided ? heroSlides(continueWatching, titleRows, newest ?? null) : []), [heroDecided, continueWatching, titleRows, newest]);

  // First screen: the frame after the masthead, the hero (or its absence) and the first shelves' frames paint.
  useEffect(() => {
    if (!heroDecided || firstScreen) return undefined;
    const frame = requestAnimationFrame(() => {
      recordMetric('home_first_screen_ms', 'home', sinceNavigation());
      setFirstScreen(true);
    });
    return () => cancelAnimationFrame(frame);
  }, [heroDecided, firstScreen]);

  // A return visit comes back to where the member left it; cached shelves render in the same commit.
  useLayoutEffect(() => {
    const top = cachedShelf<number>(user.id, 'scroll');
    if (top) window.scrollTo({ top });
    const save = () => rememberShelf(user.id, 'scroll', window.scrollY);
    window.addEventListener('scroll', save, { passive: true });
    return () => window.removeEventListener('scroll', save);
  }, [user.id]);

  // A library retry started here hands focus back to the page or the recovery button when it settles (as today).
  useEffect(() => {
    if (wasRetryingRef.current && !libraryRetrying && restoreFocusRef.current) {
      if (libraryState === 'ready' || libraryState === 'empty') headingRef.current?.focus();
      else recoveryButtonRef.current?.focus();
      restoreFocusRef.current = false;
    }
    wasRetryingRef.current = libraryRetrying;
  }, [libraryRetrying, libraryState]);
  const retryLibrary = () => { restoreFocusRef.current = true; onRetryLibrary(); };

  useLayoutEffect(() => {
    if (editing || !focusEditRef.current) return;
    focusEditRef.current = false;
    editRef.current?.focus();
  }, [editing]);

  async function hide(entry: PlaybackProgress) {
    try {
      await onHideContinueWatching?.(entry);
      toast({ tone: 'success', message: 'Removed from Continue watching.', action: onRestoreContinueWatching ? { label: 'Undo', onAction: () => { void onRestoreContinueWatching(entry); } } : undefined });
    } catch {
      toast({ tone: 'error', message: 'Lumina could not remove that. Try again.' });
    }
  }
  const dismiss = useCallback((title: TitleSummary) => {
    setDismissed((current) => new Set(current).add(title.id));
    dismissNextUp(title.series_id ?? title.id).catch(() => {
      setDismissed((current) => { const next = new Set(current); next.delete(title.id); return next; });
      toast({ tone: 'error', message: 'Lumina could not remove that. Try again.' });
    });
  }, [toast]);
  function changeLayout(next: HomeShelfPref[] | null) {
    if (next === null) {
      const previous = homeShelves;
      toast({ tone: 'success', message: 'Home reset to the default layout.', action: { label: 'Undo', onAction: () => onHomeShelvesChange(previous) } });
    }
    onHomeShelvesChange(next);
  }
  function leaveEditing() {
    focusEditRef.current = true;
    setEditing(false);
    setAnnouncement('Home layout saved.');
  }
  function toggleEditing() {
    if (editing) leaveEditing();
    else { setAnnouncement(''); setEditing(true); }
  }

  const actions = useMemo<ShelfActions>(() => ({ onOpenTitle, onOpenLibrary, onOpenRoute }), [onOpenTitle, onOpenLibrary, onOpenRoute]);
  const nextUpActions = useMemo<ShelfActions>(() => ({ ...actions, onDismiss: dismiss }), [actions, dismiss]);

  const interestsChosen = Boolean(interests?.selected_keys.length);
  // Picked for you shows on any signal: interests, a follow, or whatever the server already picked (a satisfied watch).
  const pickedSignal = interestsChosen || channels.length > 0 || Boolean(recommendations?.items.length);
  const queued = onOpenQueued ? (queue?.entries ?? []).filter((entry) => entry.availability === 'available') : [];
  // Recently saved lists media saved from the web; titled movies and episodes have their own shelves.
  const recent = recentLibrary.filter((item) => item.status !== 'missing' && !item.title_id).slice(0, SHELF_LIMIT);
  const libraryUnavailable = (libraryState === 'offline' || libraryState === 'failed') && !recent.length;
  const followFailures = subscriptionOutcomes.filter((outcome) => outcome.status === 'authentication-required' || outcome.status === 'failed');
  const followsLoading = subscriptionOutcomes.some((outcome) => outcome.status === 'loading');
  const shellStatus: Partial<Record<HomeShelfId, ShelfStatus>> = {
    continue: continueWatching.length ? 'items' : 'empty',
    watchlist: !onOpenQueued ? 'empty' : queue ? (queued.length ? 'items' : 'empty') : queueFailed ? 'failed' : 'loading',
    picked_for_you: !pickedSignal ? 'empty' : recommendations?.items.length ? 'items' : recommendations?.state === 'empty' ? 'empty' : 'loading',
    from_follows: subscriptionVideos.length ? 'items' : followsLoading ? 'loading' : 'empty',
    recently_saved: recent.length ? 'items' : libraryState === 'loading' ? 'loading' : 'empty',
  };
  const emptyShelves = new Set(HOME_SHELF_IDS.filter((id) => (shellStatus[id] ?? statuses[id]) === 'empty'));
  // Fresh member: everything personal is empty and settled, including every visible title shelf.
  // P-I3: failing to load is settled (not stuck), so it stops blocking "fresh" forever, but a failed load's real
  // content is unknown, so the start card logic treats it as not-empty rather than guessing it is empty.
  const queueKnown = !onOpenQueued || !visible.has('watchlist') || queue !== null || queueFailed;
  const titlesEmpty = FETCHED_SHELVES.every((id) => statuses[id] === 'empty');
  // P-I2: a hidden shelf's content is unknown, so it must not count towards "fresh" — only a from-scratch or
  // all-visible layout can be fresh, and never while mid-edit (leaving edit mode must never be caused by fresh).
  const noHiddenShelves = homeShelves === null || layout.every((shelf) => shelf.visible);
  const fresh = !editing && noHiddenShelves && !continueWatching.length && !queued.length && !queueFailed && queueKnown
    && !pickedSignal && !channels.length && !recent.length && !subscriptionVideos.length && !followFailures.length && !followsLoading && titlesEmpty && (libraryState === 'empty' || libraryState === 'ready');

  // Palette "Edit Home": enter edit mode as the masthead button does; a no-op while the start card shows or already editing.
  useEffect(() => onCommand(EDIT_HOME_EVENT, () => { if (!fresh && !editing) { setAnnouncement(''); setEditing(true); } }), [fresh, editing]);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    // From the greeting, Down reaches the first control below the masthead (the hero's Resume) and Up reaches Edit home,
    // rather than whichever target is nearest the middle of a wide heading.
    if (event.target === headingRef.current && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
      const target = event.key === 'ArrowUp' ? editRef.current : event.currentTarget.querySelector('.h-body')?.querySelector<HTMLElement>(FOCUS_TARGETS);
      if (target) {
        event.preventDefault();
        target.focus();
        return;
      }
    }
    moveFocus(event);
  }

  function picked(): ReactNode {
    return (
      <PickedShelf
        onOpenRemote={onOpenRemote}
        onPersonalize={onPersonalize}
        onRetry={onRetryRecommendations}
        recommendationError={recommendationError}
        recommendations={recommendations}
        recommendationsLoading={recommendationsLoading}
      />
    );
  }

  function shelf(id: HomeShelfId): ReactNode {
    if (isFetchedShelf(id)) {
      return <FetchedShelf actions={id === 'next_up' ? nextUpActions : actions} hidden={id === 'next_up' ? dismissed : undefined} id={id} onStatus={setStatus} userId={user.id} visit={visit} />;
    }
    switch (id) {
      case 'continue':
        return continueWatching.length ? (
          <HomeShelf
            heading="Continue watching"
            itemKey={(entry) => entry.id}
            items={continueWatching}
            renderCard={(entry, slot) => (
              <>
                {entry.title
                  ? titleStill(entry.title, slot, () => onOpenLibrary(entry.item), { left: entryLeft(entry), percent: entryPercent(entry) })
                  : itemStill(entry.item, slot, onOpenLibrary, entryPercent(entry))}
                {onHideContinueWatching ? <RemoveButton label={`Remove ${entry.title ? titleCaption(entry.title).name : entry.item.title} from Continue watching`} onRemove={() => void hide(entry)} /> : null}
              </>
            )}
            sectionKey="continue"
            shape="still"
            state="ready"
          />
        ) : null;
      case 'live':
        return <NearGate heading={HOME_CATALOGUE.live.name} sectionKey="live" shape="still">{() => <LiveShelf onOpenRemote={onOpenRemote} onSeeAll={() => onOpenRoute({ surface: 'streaming', view: 'live' })} onStatus={(status) => setStatus('live', status)} userId={user.id} />}</NearGate>;
      case 'watchlist':
        return onOpenQueued ? <Watchlist onOpen={onOpenQueued} /> : null;
      case 'picked_for_you':
        return pickedSignal ? picked() : null;
      case 'from_follows':
        return subscriptionVideos.length || followsLoading || followFailures.length ? (
          <HomeShelf
            heading="New from your follows"
            itemKey={(item) => itemKey(item)}
            items={subscriptionVideos.slice(0, SHELF_LIMIT)}
            notes={followFailures.length ? (
              <p className="h-note">
                {followFailures.length} followed channel{followFailures.length === 1 ? '' : 's'} need{followFailures.length === 1 ? 's' : ''} attention. Available channels are still shown.{' '}
                <button className="h-inline-button" data-focus-item onClick={() => onOpenRoute({ surface: 'streaming', view: 'channels' })} type="button">Review channels</button>
              </p>
            ) : null}
            renderCard={(item, slot) => remoteStill(item, slot, () => onOpenRemote(item))}
            sectionKey="from_follows"
            seeAll={{ label: 'View channels', onClick: () => onOpenRoute({ surface: 'streaming', view: 'channels' }) }}
            shape="still"
            state={!subscriptionVideos.length && followsLoading ? 'loading' : 'ready'}
          />
        ) : null;
      case 'recently_saved':
        return recent.length || libraryState === 'loading' || libraryState === 'stale' || libraryUnavailable ? (
          <HomeShelf
            heading="Recently saved"
            itemKey={(item) => item.id}
            items={recent}
            notes={libraryUnavailable
              ? <CollectionRecovery buttonRef={recoveryButtonRef} label="Library" onRetry={retryLibrary} onSignIn={onSignIn} problem={libraryProblem} retrying={libraryRetrying} state={libraryState as 'offline' | 'failed'} />
              : libraryState === 'stale' ? <StaleCollectionNotice buttonRef={recoveryButtonRef} label="Library" onRetry={retryLibrary} onSignIn={onSignIn} problem={libraryProblem} retrying={libraryRetrying} /> : null}
            renderCard={(item, slot) => itemStill(item, slot, onOpenLibrary)}
            sectionKey="recently_saved"
            seeAll={{ label: 'Open library', onClick: () => onNavigate('library') }}
            shape="still"
            state={libraryState === 'loading' && !recent.length ? 'loading' : 'ready'}
          />
        ) : null;
      default:
        return null;
    }
  }

  return (
    <div className="surface gallery home" onKeyDown={onKeyDown}>
      <header className="h-masthead">
        <div className="h-kicker-line">
          <p className="g-label g-kicker">{mastheadDate()}</p>
          {/* Nothing to arrange for a fresh member. */}
          {fresh ? null : <button aria-pressed={editing} className="g-button g-button-text is-quiet" data-focus-item onClick={toggleEditing} ref={editRef} type="button">{editing ? 'Done' : 'Edit home'}</button>}
        </div>
        <h1 ref={headingRef} tabIndex={-1}>{greeting()}, {firstName(user)}</h1>
      </header>
      {/* Until the hero is decided (or HERO_WAIT_MS passes) the body is laid out but not painted: a hero that arrives later would push the shelves already on screen down (CLS). Hidden, not unmounted, so the shelves still fetch. */}
      <div className="h-body" style={heroDecided || waited ? undefined : { visibility: 'hidden' }}>
        {fresh ? (
          <section aria-labelledby="h-start-title" className="h-start">
            <p className="g-label">Getting started</p>
            <h2 id="h-start-title">Make Home yours</h2>
            <p>Home fills with what you watch, queue, follow, and save. Nothing is invented here, so start with something real.</p>
            <div className="h-start-actions" data-focus-row>
              <button className="g-button g-button-text is-primary" data-focus-item onClick={() => onOpenRoute({ surface: 'streaming', view: 'home' })} type="button">Discover something</button>
              <button className="g-button g-button-text" data-focus-item onClick={() => onOpenRoute({ surface: 'streaming', view: 'channels' })} type="button">Follow a channel</button>
              {interests && onPersonalize ? <button className="g-button g-button-text" data-focus-item onClick={onPersonalize} type="button">Choose interests</button> : null}
            </div>
          </section>
        ) : editing ? (
          <HomeEditor empty={emptyShelves} onChange={changeLayout} onDone={leaveEditing} shelves={layout} />
        ) : (
          <>
            {slides.length ? <HomeHero onOpenTitle={onOpenTitle} onPlay={onPlay} ready={firstScreen} slides={slides} /> : null}
            {layout.filter((entry) => entry.visible).map(({ id }) => <Fragment key={id}>{shelf(id)}</Fragment>)}
            {interests && !interestsChosen && onPersonalize ? (
              <section aria-labelledby="h-personalize-title" className="h-personalize">
                <p className="g-label">Discovery, your way</p>
                <h2 id="h-personalize-title">Make Home yours</h2>
                <p>Choose a few broad interests to seed suggestions. This will not follow creators or add anything to your Library.</p>
                <button className="g-button g-button-text" data-focus-item onClick={onPersonalize} type="button">Choose interests</button>
              </section>
            ) : null}
          </>
        )}
      </div>
      <p className="sr-only" role="status">{announcement}</p>
    </div>
  );
}

export const HomeSurface = memo(HomeSurfaceView);
