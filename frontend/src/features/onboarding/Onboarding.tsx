import { type FormEvent, useEffect, useRef, useState } from 'react';
import { Check, ChevronLeft, Search } from 'lucide-react';
import { type CategoryChannelSuggestions, type ChannelCandidate, type FollowChannelRequest, type InterestCategory } from '../../types';
import { Avatar, Button, EmptyState, ErrorState, Field, fieldProps, Input, Masthead, ProgressBar, Skeleton } from '../../ui';
import './onboarding.css';

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
      <Avatar decorative imageUrl={candidate.artwork_url || undefined} name={candidate.display_name} size={48} />
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
    <div className={`g-onboarding${reducedMotion ? ' reduced-motion' : ''}`}>
      <div aria-hidden="true" className="g-onboarding-progress"><ProgressBar label="" value={100} /></div>
      <div className="g-onboarding-panel">
        <Masthead
          headingRef={headingRef}
          kicker="Step 2 of 2"
          lede="Find creators you already know or discover channels from your interests. Following seeds your Home and Subscriptions — it never acquires media on its own, and you can change it anytime."
          title="Follow a few channels"
        />

        <form className="g-onboarding-search" onSubmit={submitFind} role="search">
          <Field hideLabel label="Search channels or paste a channel address">
            {(ids) => <Input {...fieldProps(ids)} autoComplete="off" disabled={busy} onChange={(event) => setQuery(event.target.value)} placeholder="Search a creator, or paste a channel address" type="search" value={query} />}
          </Field>
          <Button disabled={busy} icon={<Search />} type="submit">Find channels</Button>
        </form>
        <p aria-live="polite" className="g-onboarding-note">
          {searching ? 'Searching for channels…' : searchError ? searchError : ''}
        </p>
        {error ? <p className="g-onboarding-error" role="alert">{error}</p> : null}

        {searchResults.length ? (
          <section aria-labelledby="channel-search-results-title">
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
          <section aria-labelledby={`channel-cat-${category.key}`} key={category.key}>
            <h2 className="g-onboarding-category" id={`channel-cat-${category.key}`}>Popular in {category.label}</h2>
            {category.state === 'curated' ? (
              <p className="g-onboarding-note">A starting set while Lumina gathers fresh picks for {category.label}.</p>
            ) : null}
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

        <div className="g-onboarding-footer">
          <Button disabled={busy} icon={<ChevronLeft />} onClick={onBack}>Back</Button>
          <Button busy={busy} disabled={busy} onClick={() => onContinue(follows)} variant="primary">
            {follows.length ? `Continue with ${follows.length} channel${follows.length === 1 ? '' : 's'}` : 'Continue'}
          </Button>
        </div>
      </div>
    </div>
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
      <div className={rootClass}>
        <div aria-live="polite" className="g-onboarding-panel" role="status">
          <Masthead align="center" headingRef={headingRef} lede="Saving your choices and gathering a first set of recommendations. This only takes a moment." title="Preparing your Home" />
          <ProgressBar label="Preparing your Home" value={null} />
        </div>
      </div>
    );
  }

  return (
    <div className={rootClass}>
      <div aria-hidden="true" className="g-onboarding-progress"><ProgressBar label="" value={50} /></div>
      <div className="g-onboarding-panel">
        <Masthead
          headingRef={headingRef}
          kicker="Step 1 of 2"
          lede="Choose a few broad interests to seed your recommendations. Lumina will not follow creators, acquire media, or change your Library — and you can change these anytime."
          title="Set up your Home"
        />
        {categories.length ? (
          <fieldset className="g-onboarding-interests" disabled={busy}>
            <legend className="g-label">Choose interests</legend>
            {categories.map((category) => (
              <label className="g-chip g-onboarding-chip" key={category.key}>
                <input checked={selected.includes(category.key)} onChange={() => toggle(category.key)} type="checkbox" />
                <Check aria-hidden="true" className="g-onboarding-check" />{category.label}
              </label>
            ))}
          </fieldset>
        ) : (
          <Skeleton count={4} label="Loading interests…" shape="line" />
        )}
        {error ? <p className="g-onboarding-error" role="alert">{error}</p> : null}
        <div className="g-onboarding-footer">
          <Button disabled={busy} onClick={onSkip} variant="quiet">Skip for now</Button>
          <Button busy={busy} disabled={busy} onClick={() => onContinue(selected)} variant="primary">Continue</Button>
        </div>
      </div>
    </div>
  );
}
