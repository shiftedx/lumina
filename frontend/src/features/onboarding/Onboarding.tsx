import { type FormEvent, useEffect, useRef, useState } from 'react';
import { ArrowRight, Check, ChevronLeft, Compass, Library, Play, Search } from 'lucide-react';
import { type CategoryChannelSuggestions, type ChannelCandidate, type FollowChannelRequest, type InterestCategory } from '../../types';
import { Avatar, Button, EmptyState, ErrorState, Field, fieldProps, Input, Masthead, ProgressBar, Skeleton } from '../../ui';
import './onboarding.css';

const ONBOARDING_ART = {
  creators: '/onboarding/creator-studio-v1.jpg',
  interests: '/onboarding/listening-room-v1.jpg',
  preparing: '/onboarding/film-archive-v1.jpg',
  welcome: '/onboarding/vault.jpg',
} as const;

function OnboardingArtwork({ eager = false, scene }: { eager?: boolean; scene: keyof typeof ONBOARDING_ART }) {
  return (
    <img
      alt=""
      aria-hidden="true"
      className="g-onboarding-vault-art"
      decoding="async"
      fetchPriority={eager ? 'high' : 'auto'}
      loading={eager ? 'eager' : 'lazy'}
      src={ONBOARDING_ART[scene]}
    />
  );
}

export function WelcomeSurface({
  displayName,
  reducedMotion = false,
  busy = false,
  error = null,
  onBegin,
  onSkip,
}: {
  displayName?: string | null;
  reducedMotion?: boolean;
  busy?: boolean;
  error?: string | null;
  onBegin: () => void;
  onSkip: () => void;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => { headingRef.current?.focus(); }, []);
  const greeting = displayName?.trim() ? `The vault is open, ${displayName.trim()}.` : 'The vault is open.';

  return (
    <main className={`g-onboarding is-welcome${reducedMotion ? ' reduced-motion' : ''}`}>
      <section className="g-onboarding-welcome-art" aria-label="Welcome to Lumina">
        <OnboardingArtwork eager scene="welcome" />
        <div className="g-onboarding-welcome-copy">
          <p className="g-onboarding-welcome-kicker">{greeting}</p>
          <h1 ref={headingRef} tabIndex={-1}>Welcome to Lumina</h1>
          <p className="g-onboarding-welcome-lede">
            A shared media vault. A Home that’s yours. Browse films, series, music, and new discoveries with family and friends.
          </p>
        </div>
      </section>

      <section className="g-onboarding-welcome-details" aria-label="What waits inside">
        <div className="g-onboarding-welcome-features">
          <article>
            <Library aria-hidden="true" />
            <div><h2>Your library</h2><p>Browse the media shared with you.</p></div>
          </article>
          <article>
            <Compass aria-hidden="true" />
            <div><h2>A Home of your own</h2><p>Personal picks shaped by what you choose and watch.</p></div>
          </article>
          <article>
            <Play aria-hidden="true" />
            <div><h2>Ready on any screen</h2><p>Pick up your place in a browser or a compatible media app.</p></div>
          </article>
        </div>
        {error ? <p className="g-onboarding-error" role="alert">{error}</p> : null}
        <div className="g-onboarding-welcome-actions">
          <Button busy={busy} disabled={busy} icon={<ArrowRight />} onClick={onBegin} variant="primary">Personalize my Home</Button>
          <Button disabled={busy} onClick={onSkip} variant="quiet">Explore without setup</Button>
        </div>
      </section>
    </main>
  );
}

export function ChannelChoice({
  candidate,
  selected,
  busy,
  onToggle,
}: {
  candidate: ChannelCandidate;
  selected: boolean;
  busy: boolean;
  onToggle: (candidate: ChannelCandidate) => void;
}) {
  const label = candidate.following
    ? `${candidate.display_name} — already following`
    : selected
      ? `${candidate.display_name} — selected, deselect`
      : `Follow ${candidate.display_name}`;
  return (
    <button
      aria-label={label}
      aria-pressed={candidate.following ? undefined : selected}
      className="g-onboarding-channel"
      disabled={candidate.following || busy}
      onClick={() => onToggle(candidate)}
      type="button"
    >
      <span className="g-onboarding-channel-art">
        <Avatar decorative imageUrl={candidate.artwork_url || undefined} name={candidate.display_name} size={96} />
        {candidate.following || selected ? <span className="g-onboarding-channel-check"><Check aria-hidden="true" /></span> : null}
      </span>
      <span className="g-onboarding-channel-name">{candidate.display_name}</span>
      <span className="g-onboarding-channel-state">{candidate.following ? 'Following' : selected ? 'Selected' : 'Follow'}</span>
    </button>
  );
}

export function ChannelDiscoveryStep({
  suggestions,
  suggestionsLoading = false,
  suggestionsError = null,
  busy = false,
  error = null,
  reducedMotion = false,
  onRetry,
  onSearch,
  onResolveAddress,
  onContinue,
  onBack,
}: {
  suggestions: CategoryChannelSuggestions[];
  suggestionsLoading?: boolean;
  suggestionsError?: string | null;
  busy?: boolean;
  /** The save that follows Continue failed: said here because this step has no other place for it. */
  error?: string | null;
  reducedMotion?: boolean;
  onRetry?: () => void;
  onSearch: (query: string) => Promise<ChannelCandidate[]>;
  onResolveAddress: (value: string) => ChannelCandidate | null;
  onContinue: (follows: FollowChannelRequest[]) => void;
  onBack: () => void;
}) {
  const [selected, setSelected] = useState<Map<string, ChannelCandidate>>(new Map());
  const [query, setQuery] = useState('');
  const [searchResults, setSearchResults] = useState<ChannelCandidate[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => { headingRef.current?.focus(); }, []);

  function toggle(candidate: ChannelCandidate) {
    if (candidate.following) return;
    setSelected((current) => {
      const next = new Map(current);
      if (next.has(candidate.channel_key)) next.delete(candidate.channel_key);
      else next.set(candidate.channel_key, candidate);
      return next;
    });
  }

  async function submitFind(event: FormEvent) {
    event.preventDefault();
    const value = query.trim();
    if (!value || busy) return;
    // A pasted channel address resolves to the same candidate identity a
    // category suggestion or existing follow uses — no search is issued.
    const resolved = onResolveAddress(value);
    if (resolved) {
      setSearchResults((current) => [resolved, ...current.filter((entry) => entry.channel_key !== resolved.channel_key)]);
      if (!resolved.following) setSelected((current) => new Map(current).set(resolved.channel_key, resolved));
      setQuery('');
      setSearchError(null);
      return;
    }
    setSearching(true);
    setSearchError(null);
    try {
      setSearchResults(await onSearch(value));
    } catch {
      setSearchError('Channel search is unavailable right now. Your picks are still safe.');
    } finally {
      setSearching(false);
    }
  }

  const follows: FollowChannelRequest[] = [...selected.values()].map((candidate) => ({
    source_url: candidate.source_url,
    display_name: candidate.display_name,
  }));
  const populated = suggestions.filter((category) => category.channels.length);

  return (
    <main className={`g-onboarding is-discovery${reducedMotion ? ' reduced-motion' : ''}`}>
      <div aria-hidden="true" className="g-onboarding-progress"><ProgressBar label="" value={100} /></div>
      <div className="g-onboarding-panel">
        <header className="g-onboarding-discovery-hero">
          <OnboardingArtwork scene="creators" />
          <div className="g-onboarding-discovery-copy">
            <p className="g-onboarding-step">Step 2 of 2</p>
            <Masthead
              headingRef={headingRef}
              lede="Choose creators you already enjoy, or search for someone specific. These follows shape Home and Subscriptions from day one."
              title="Follow a few channels"
            />
          </div>
        </header>

        <div className="g-onboarding-discovery-body">
          <aside className="g-onboarding-search-workspace">
            <div>
              <h2>Start with a name</h2>
              <p>Following never downloads media on its own. You can change these choices anytime.</p>
            </div>
            <form className="g-onboarding-search" onSubmit={submitFind} role="search">
              <Field hideLabel label="Search channels or paste a channel address">
                {(ids) => <Input {...fieldProps(ids)} autoComplete="off" disabled={busy} onChange={(event) => setQuery(event.target.value)} placeholder="Creator or channel address" type="search" value={query} />}
              </Field>
              <Button disabled={busy || searching} icon={<Search />} type="submit">Find channels</Button>
            </form>
            <p aria-live="polite" className="g-onboarding-note">
              {searching ? 'Searching for channels…' : searchError ? searchError : ''}
            </p>
            {error ? <p className="g-onboarding-error" role="alert">{error}</p> : null}
          </aside>

          <div className="g-onboarding-catalog">
            {searchResults.length ? (
              <section aria-labelledby="channel-search-results-title" className="g-onboarding-channel-section">
                <h2 className="g-onboarding-category" id="channel-search-results-title">Search results</h2>
                <div className="g-onboarding-channels">
                  {searchResults.map((candidate) => (
                    <ChannelChoice busy={busy} candidate={candidate} key={candidate.channel_key} onToggle={toggle} selected={selected.has(candidate.channel_key)} />
                  ))}
                </div>
              </section>
            ) : null}

            {suggestionsLoading ? <Skeleton count={6} label="Loading channels…" shape="row" /> : null}
            {suggestionsError ? <ErrorState onRetry={onRetry} title="Lumina could not load suggestions." /> : null}

            {populated.map((category) => (
              <section aria-labelledby={`channel-cat-${category.key}`} className="g-onboarding-channel-section" key={category.key}>
                <div className="g-onboarding-category-heading">
                  <h2 className="g-onboarding-category" id={`channel-cat-${category.key}`}>Popular in {category.label}</h2>
                  {category.state === 'curated' ? (
                    <p className="g-onboarding-note">A starting set while Lumina gathers fresh picks.</p>
                  ) : null}
                </div>
                <div className="g-onboarding-channels">
                  {category.channels.map((candidate) => (
                    <ChannelChoice busy={busy} candidate={candidate} key={candidate.channel_key} onToggle={toggle} selected={selected.has(candidate.channel_key)} />
                  ))}
                </div>
              </section>
            ))}

            {!populated.length && !searchResults.length && !suggestionsLoading && !suggestionsError ? (
              <EmptyState body="Search for a channel above." title="No suggestions right now." />
            ) : null}
          </div>
        </div>

        <div className="g-onboarding-footer">
          <Button disabled={busy} icon={<ChevronLeft />} onClick={onBack}>Back</Button>
          <Button busy={busy} disabled={busy} onClick={() => onContinue(follows)} variant="primary">
            {follows.length ? `Continue with ${follows.length} channel${follows.length === 1 ? '' : 's'}` : 'Continue'}
          </Button>
        </div>
      </div>
    </main>
  );
}

export function OnboardingSurface({
  categories,
  initialSelectedKeys = [],
  phase,
  busy = false,
  error = null,
  reducedMotion = false,
  onContinue,
  onSkip,
}: {
  categories: InterestCategory[];
  initialSelectedKeys?: string[];
  phase: 'setup' | 'preparing';
  busy?: boolean;
  error?: string | null;
  reducedMotion?: boolean;
  onContinue: (keys: string[]) => void;
  onSkip: () => void;
}) {
  const [selected, setSelected] = useState<string[]>(initialSelectedKeys);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const toggle = (key: string) =>
    setSelected((current) => (current.includes(key) ? current.filter((entry) => entry !== key) : [...current, key]));

  // Land the reading cursor on the surface heading when setup opens and again
  // when preparation begins, so the flow is reachable and its progress heard.
  useEffect(() => { headingRef.current?.focus(); }, [phase]);

  const rootClass = `g-onboarding${reducedMotion ? ' reduced-motion' : ''}`;

  if (phase === 'preparing') {
    return (
      <main className={`${rootClass} is-preparing g-dark`}>
        <OnboardingArtwork scene="preparing" />
        <div aria-live="polite" className="g-onboarding-preparing-status" role="status">
          <p className="g-onboarding-welcome-kicker">Your Home is taking shape</p>
          <Masthead align="center" headingRef={headingRef} lede="Saving your choices and gathering a first set of recommendations. This only takes a moment." title="Preparing your Home" />
          <ProgressBar label="Preparing your Home" value={null} />
        </div>
      </main>
    );
  }

  return (
    <main className={`${rootClass} is-interests`}>
      <div aria-hidden="true" className="g-onboarding-progress"><ProgressBar label="" value={50} /></div>
      <div className="g-onboarding-panel">
        <section className="g-onboarding-interest-art" aria-label="Discover your taste">
          <OnboardingArtwork scene="interests" />
          <div className="g-onboarding-art-copy">
            <p>One Home.</p>
            <strong>Entirely your taste.</strong>
          </div>
        </section>

        <section className="g-onboarding-interest-workspace">
          <p className="g-onboarding-step">Step 1 of 2</p>
          <Masthead
            headingRef={headingRef}
            lede="Choose a few broad interests and Lumina will turn the first page of a Home made for you."
            title="Set up your Home"
          />
          <p className="g-onboarding-assurance">
            Interests only shape recommendations. Lumina will not follow creators, acquire media, or change your Library — and you can change them anytime.
          </p>
          {categories.length ? (
            <fieldset className="g-onboarding-interests" disabled={busy}>
              <legend>Choose interests</legend>
              <p className="g-onboarding-interest-hint">Pick as many as you like</p>
              <div className="g-onboarding-interest-grid">
                {categories.map((category) => (
                  <label className="g-onboarding-interest" key={category.key}>
                    <input checked={selected.includes(category.key)} onChange={() => toggle(category.key)} type="checkbox" />
                    <span>{category.label}</span>
                    <span className="g-onboarding-interest-check"><Check aria-hidden="true" /></span>
                  </label>
                ))}
              </div>
              <p aria-live="polite" className="g-onboarding-selection-count">
                {selected.length ? `${selected.length} selected` : 'Nothing selected yet'}
              </p>
            </fieldset>
          ) : (
            <Skeleton count={4} label="Loading interests…" shape="line" />
          )}
          {error ? <p className="g-onboarding-error" role="alert">{error}</p> : null}
          <div className="g-onboarding-footer">
            <Button disabled={busy} onClick={onSkip} variant="quiet">Skip for now</Button>
            <Button busy={busy} disabled={busy} icon={<ArrowRight />} onClick={() => onContinue(selected)} variant="primary">Continue</Button>
          </div>
        </section>
      </div>
    </main>
  );
}
