import { type FormEvent, useEffect, useRef, useState } from 'react';
import {
  ArrowRight, BookOpen, Camera, Car, Check, ChevronDown, ChevronLeft, Compass, Dumbbell, Film,
  Gamepad2, Globe, Headphones, Heart, Leaf, Library, Mic, Music, Newspaper, Palette,
  Play, Plus, Search, Scissors, Sparkles, Sprout, Telescope, Trophy, Utensils, X,
} from '../../luminaIcons';
import { Artwork } from '../../Artwork';
import { type CategoryChannelSuggestions, type ChannelCandidate, type FollowChannelRequest, type InterestCategory } from '../../types';
import { Avatar, BrandMark, Button, EmptyState, ErrorState, Field, fieldProps, initials, Input, Masthead, Popover, ProgressBar, Skeleton } from '../../ui';
import './onboarding.css';

const ONBOARDING_ART = {
  creators: '/onboarding/creator-studio-v1.jpg',
  interests: '/onboarding/listening-room-v1.jpg',
  preparing: '/onboarding/vault-arrival-v2.jpg',
  welcome: '/onboarding/vault-arrival-v2.jpg',
} as const;

const INTEREST_ICONS: Record<string, typeof Music> = {
  music: Music, film: Film, documentaries: Globe, 'live-performances': Mic, gaming: Gamepad2,
  news: Newspaper, sports: Trophy, comedy: Sparkles, education: BookOpen,
  'science-technology': Telescope, podcasts: Headphones, cooking: Utensils, travel: Compass,
  'diy-crafts': Scissors, 'home-garden': Sprout, cars: Car, fitness: Dumbbell,
  'fashion-beauty': Sparkles, nature: Leaf, history: BookOpen, 'business-finance': Library,
  photography: Camera, animation: Palette, family: Heart, culture: Palette,
};
const FIRST_INTERESTS = ['music', 'film', 'science-technology', 'nature', 'documentaries', 'cooking', 'gaming', 'travel'];

function OnboardingArtwork({ eager = false, scene }: { eager?: boolean; scene: keyof typeof ONBOARDING_ART }) {
  return <img alt="" aria-hidden="true" className="g-onboarding-vault-art" decoding="async" fetchPriority={eager ? 'high' : 'auto'} loading={eager ? 'eager' : 'lazy'} src={ONBOARDING_ART[scene]} />;
}

function JourneyHeader({ step }: { step: 'welcome' | 'interests' | 'creators' }) {
  return (
    <header className="g-onboarding-header">
      <BrandMark size="bar" />
      <ol aria-label="Your welcome journey" className="g-onboarding-journey">
        {(['welcome', 'interests', 'creators'] as const).map((entry, index) => (
          <li aria-current={entry === step ? 'step' : undefined} key={entry}>
            <span aria-hidden="true" className="g-onboarding-journey-dot">{(['welcome', 'interests', 'creators'].indexOf(step) > index) ? <Check /> : null}</span>
            {['Welcome', 'Your taste', 'Your creators'][index]}
          </li>
        ))}
      </ol>
    </header>
  );
}

function CreatorPortrait({ candidate, size = 48 }: { candidate: ChannelCandidate; size?: 48 | 96 }) {
  if (!candidate.artwork_url) return <Avatar decorative name={candidate.display_name} size={size} />;
  return <span aria-hidden="true" className={`g-avatar is-${size}`}><Artwork alt="" fallback={<span className="g-avatar-initials">{initials(candidate.display_name)}</span>} src={candidate.artwork_url} /></span>;
}

export function WelcomeSurface({ displayName, reducedMotion = false, busy = false, error = null, onBegin, onSkip }: {
  displayName?: string | null;
  reducedMotion?: boolean;
  busy?: boolean;
  error?: string | null;
  onBegin: () => void;
  onSkip: () => void;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => { headingRef.current?.focus({ preventScroll: true }); }, []);
  const greeting = displayName?.trim() ? `The vault is open, ${displayName.trim()}.` : 'The vault is open.';

  return (
    <main className={`g-onboarding is-welcome g-dark${reducedMotion ? ' reduced-motion' : ''}`}>
      <JourneyHeader step="welcome" />
      <section className="g-onboarding-welcome-art g-dark" aria-label="Welcome to Lumina">
        <OnboardingArtwork eager scene="welcome" />
        <div className="g-onboarding-welcome-copy">
          <p className="g-onboarding-welcome-kicker">{greeting}</p>
          <h1 ref={headingRef} tabIndex={-1}>Welcome to Lumina</h1>
          <p className="g-onboarding-welcome-lede">Films, music, stories, and new discoveries with family and friends. Together in a vault that feels like you.</p>
          <div className="g-onboarding-welcome-invitation">
            <span>A few interests. Your favorite creators. A Home of your own.</span>
          </div>
          {error ? <p className="g-onboarding-error" role="alert">{error}</p> : null}
          <div className="g-onboarding-welcome-actions">
            <Button busy={busy} disabled={busy} iconEnd={<ArrowRight />} onClick={onBegin} variant="primary">Personalize my Home</Button>
            <Button disabled={busy} onClick={onSkip} variant="quiet">Explore without setup</Button>
          </div>
        </div>
      </section>
      <section className="g-onboarding-welcome-details" aria-label="What waits inside">
        <div className="g-onboarding-welcome-features">
          <article><Library aria-hidden="true" /><div><h2>Your library</h2><p>The media shared with you, in one place.</p></div></article>
          <article><Compass aria-hidden="true" /><div><h2>A Home of your own</h2><p>Discoveries shaped by your taste.</p></div></article>
          <article><Play aria-hidden="true" /><div><h2>Ready on any screen</h2><p>In a browser or compatible media app.</p></div></article>
        </div>
      </section>
    </main>
  );
}

export function ChannelChoice({ candidate, selected, busy, onToggle }: {
  candidate: ChannelCandidate;
  selected: boolean;
  busy: boolean;
  onToggle: (candidate: ChannelCandidate) => void;
}) {
  const label = candidate.following ? `${candidate.display_name} — already following` : selected ? `${candidate.display_name} — selected, deselect` : `Follow ${candidate.display_name}`;
  const hasPreview = Boolean(candidate.preview_artwork_url);
  return (
    <button aria-label={label} aria-pressed={candidate.following ? undefined : selected} className="g-onboarding-channel" disabled={candidate.following || busy} onClick={() => onToggle(candidate)} type="button">
      <span aria-hidden="true" className="g-onboarding-channel-art">
        <Artwork alt="" className="g-onboarding-channel-image" fallback={<span className="g-onboarding-channel-cover">{candidate.artwork_url ? <CreatorPortrait candidate={candidate} size={96} /> : <span className="g-onboarding-channel-monogram">{initials(candidate.display_name)}</span>}</span>} src={candidate.preview_artwork_url} />
        <span className="g-onboarding-channel-check">{candidate.following || selected ? <Check /> : <Plus />}</span>
        <span className="g-onboarding-channel-state">{candidate.following ? 'Following' : selected ? 'Selected' : hasPreview ? 'From this channel' : candidate.source_label}</span>
      </span>
      <span className="g-onboarding-channel-identity"><CreatorPortrait candidate={candidate} /><span><span className="g-onboarding-channel-name">{candidate.display_name}</span><span className="g-onboarding-channel-caption">{hasPreview && candidate.preview_title ? candidate.preview_title : `Discover on ${candidate.source_label}`}</span></span></span>
    </button>
  );
}

export function ChannelDiscoveryStep({
  suggestions, suggestionsLoading = false, suggestionsError = null, initialSelected = [],
  busy = false, error = null, reducedMotion = false, onRetry, onSearch, onResolveAddress,
  onSelectionChange, onContinue, onBack,
}: {
  suggestions: CategoryChannelSuggestions[];
  suggestionsLoading?: boolean;
  suggestionsError?: string | null;
  initialSelected?: ChannelCandidate[];
  busy?: boolean;
  error?: string | null;
  reducedMotion?: boolean;
  onRetry?: () => void;
  onSearch: (query: string) => Promise<ChannelCandidate[]>;
  onResolveAddress: (value: string) => ChannelCandidate | null;
  onSelectionChange?: (candidates: ChannelCandidate[]) => void;
  onContinue: (follows: FollowChannelRequest[]) => void;
  onBack: () => void;
}) {
  const [selected, setSelected] = useState<ChannelCandidate[]>(initialSelected);
  const [categoryKey, setCategoryKey] = useState('all');
  const [query, setQuery] = useState('');
  const [searchedQuery, setSearchedQuery] = useState<string | null>(null);
  const [searchResults, setSearchResults] = useState<ChannelCandidate[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => { headingRef.current?.focus({ preventScroll: true }); }, []);

  function choose(next: ChannelCandidate[]) {
    setSelected(next);
    onSelectionChange?.(next);
  }
  function toggle(candidate: ChannelCandidate) {
    if (candidate.following || busy) return;
    choose(selected.some((entry) => entry.channel_key === candidate.channel_key) ? selected.filter((entry) => entry.channel_key !== candidate.channel_key) : [...selected, candidate]);
  }
  async function submitFind(event: FormEvent) {
    event.preventDefault();
    const value = query.trim();
    if (!value || busy || searching) return;
    const resolved = onResolveAddress(value);
    setSearchError(null);
    setSearchedQuery(value);
    if (resolved) {
      setSearchResults([resolved]);
      if (!resolved.following && !selected.some((entry) => entry.channel_key === resolved.channel_key)) choose([...selected, resolved]);
      setQuery('');
      return;
    }
    setSearching(true);
    setSearchResults([]);
    try { setSearchResults(await onSearch(value)); }
    catch { setSearchError('Channel search is unavailable right now. Your picks are still safe. Try again or keep exploring below.'); }
    finally { setSearching(false); }
  }
  const follows: FollowChannelRequest[] = selected.map((candidate) => ({ source_url: candidate.source_url, display_name: candidate.display_name }));
  const populated = suggestions.filter((category) => category.channels.length);
  const shownCategories = categoryKey === 'all' ? populated : populated.filter((category) => category.key === categoryKey);

  return (
    <main className={`g-onboarding is-discovery${reducedMotion ? ' reduced-motion' : ''}`}>
      <div aria-hidden="true" className="g-onboarding-progress"><ProgressBar label="" value={100} /></div>
      <div className="g-onboarding-panel">
        <JourneyHeader step="creators" />
        <header className="g-onboarding-discovery-hero">
          <div className="g-onboarding-discovery-copy">
            <p className="sr-only">Step 2 of 2</p>
            <Masthead headingRef={headingRef} lede="The voices you love. The perspectives you haven’t found yet. Bring a few YouTube favorites into your Home." title="Meet your next favorites" />
          </div>
          <div aria-hidden="true" className="g-onboarding-discovery-scene">
            <OnboardingArtwork eager scene="creators" />
            <span className="g-onboarding-scene-caption">A world of creators. A place of your own.</span>
          </div>
        </header>
        <div className="g-onboarding-discovery-body">
          <div className="g-onboarding-catalog">
            <form className="g-onboarding-search" onSubmit={submitFind} role="search">
              <Field hideLabel label="Search channels or paste a channel address">
                {(ids) => <Input {...fieldProps(ids)} autoComplete="off" disabled={busy} onChange={(event) => setQuery(event.target.value)} placeholder="Creator name or channel link" type="search" value={query} />}
              </Field>
              <Button aria-label="Find channels" busy={searching} disabled={busy || searching || !query.trim()} icon={<Search />} type="submit">Find channels</Button>
            </form>
            <p aria-live="polite" className="g-onboarding-search-status">{searching ? 'Finding your creators…' : searchError || (searchedQuery ? `${searchResults.length} channel${searchResults.length === 1 ? '' : 's'} found` : '')}</p>
            {searchedQuery ? (
              <section aria-labelledby="channel-search-results-title" className="g-onboarding-channel-section is-search-results">
                <div className="g-onboarding-category-heading">
                  <h2 className="g-onboarding-category" id="channel-search-results-title">{searching ? 'Looking for your favorites' : 'Your search results'}</h2>
                  <Button disabled={searching || busy} onClick={() => { setSearchedQuery(null); setSearchResults([]); setSearchError(null); setQuery(''); }} variant="quiet">Clear search</Button>
                </div>
                {searching ? <Skeleton count={3} label="Searching channels…" shape="still" /> : searchResults.length ? (
                  <div className="g-onboarding-channels">{searchResults.map((candidate) => <ChannelChoice busy={busy} candidate={candidate} key={candidate.channel_key} onToggle={toggle} selected={selected.some((entry) => entry.channel_key === candidate.channel_key)} />)}</div>
                ) : !searchError ? <EmptyState body="Try another name or paste their YouTube channel link. You can keep exploring the picks below." title={`No channels found for “${searchedQuery}”`} /> : null}
              </section>
            ) : null}
            {populated.length > 1 ? <div aria-label="Browse interests" className="g-onboarding-category-filters" role="group">
              <Button disabled={busy} onClick={() => setCategoryKey('all')} pressed={categoryKey === 'all'} variant="quiet">For you</Button>
              {populated.map((category) => <Button disabled={busy} key={category.key} onClick={() => setCategoryKey(category.key)} pressed={categoryKey === category.key} variant="quiet">{category.label}</Button>)}
            </div> : null}
            {suggestionsLoading ? <Skeleton count={3} label="Loading channels…" shape="still" /> : null}
            {suggestionsError ? <ErrorState onRetry={onRetry} title="Lumina could not load suggestions." /> : null}
            {shownCategories.map((category) => (
              <section aria-labelledby={`channel-cat-${category.key}`} className="g-onboarding-channel-section" key={category.key}>
                <div className="g-onboarding-category-heading">
                  <h2 className="g-onboarding-category" id={`channel-cat-${category.key}`}>Popular in {category.label}</h2>
                  <p className="g-onboarding-note">{category.state === 'curated' ? 'A starting set to explore.' : 'A little of what you love.'}</p>
                </div>
                <div className="g-onboarding-channels">{category.channels.map((candidate) => <ChannelChoice busy={busy} candidate={candidate} key={candidate.channel_key} onToggle={toggle} selected={selected.some((entry) => entry.channel_key === candidate.channel_key)} />)}</div>
              </section>
            ))}
            {!populated.length && !suggestionsLoading && !suggestionsError ? <EmptyState body="Search for a favorite creator above, or step inside and discover at your own pace." title="Your favorites belong here" /> : null}
          </div>
        </div>
        <footer className="g-onboarding-footer">
          <Button disabled={busy} icon={<ChevronLeft />} onClick={onBack} variant="quiet">Back</Button>
          <Popover align="end" label="Your chosen creators" trigger={(props) => <Button {...props} aria-label={`Your chosen creators, ${selected.length} selected`} className="g-onboarding-picks-trigger" disabled={busy} iconEnd={<ChevronDown />} variant="quiet"><span aria-hidden="true" className="g-onboarding-pick-stack">{selected.slice(0, 3).map((candidate) => <CreatorPortrait candidate={candidate} key={candidate.channel_key} />)}</span><span aria-live="polite">{selected.length ? `${selected.length} creator${selected.length === 1 ? '' : 's'} chosen` : 'Choose at your own pace'}</span></Button>}>
            <div className="g-onboarding-picks-panel">
              <h2>Your chosen creators</h2>
              {selected.length ? <ul aria-label="Your chosen creators" className="g-onboarding-picks">{selected.map((candidate) => <li key={candidate.channel_key}><CreatorPortrait candidate={candidate} /><span>{candidate.display_name}</span><Button disabled={busy} aria-label={`Remove ${candidate.display_name} from your picks`} icon={<X />} onClick={() => { toggle(candidate); document.querySelector<HTMLButtonElement>('.g-onboarding-picks-trigger')?.focus(); }} variant="quiet"><span className="sr-only">Remove</span></Button></li>)}</ul> : <p>Follow a creator that catches your eye. There’s no minimum, and you can add more later.</p>}
              <p className="g-onboarding-assurance">Follows shape Home and Subscriptions. Downloads stay off unless you turn them on.</p>
            </div>
          </Popover>
          {error ? <p className="g-onboarding-error" role="alert">{error}</p> : null}
          <Button aria-label={follows.length ? `Enter my Home with ${follows.length} channel${follows.length === 1 ? '' : 's'}` : 'Enter my Home'} busy={busy} disabled={busy} iconEnd={<ArrowRight />} onClick={() => onContinue(follows)} variant="primary">Enter my Home</Button>
        </footer>
      </div>
    </main>
  );
}

export function OnboardingSurface({ categories, initialSelectedKeys = [], previewChannels = [], phase, busy = false, error = null, reducedMotion = false, interestsLoading = false, interestsError = null, onRetryInterests, onContinue, onSkip }: {
  categories: InterestCategory[];
  initialSelectedKeys?: string[];
  previewChannels?: ChannelCandidate[];
  phase: 'setup' | 'preparing';
  interestsLoading?: boolean;
  interestsError?: string | null;
  onRetryInterests?: () => void;
  busy?: boolean;
  error?: string | null;
  reducedMotion?: boolean;
  onContinue: (keys: string[]) => void;
  onSkip: () => void;
}) {
  const [selected, setSelected] = useState<string[]>(initialSelectedKeys);
  const [showAll, setShowAll] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const toggle = (key: string) => setSelected((current) => current.includes(key) ? current.filter((entry) => entry !== key) : [...current, key]);
  useEffect(() => { headingRef.current?.focus({ preventScroll: true }); }, [phase]);
  const rootClass = `g-onboarding${reducedMotion ? ' reduced-motion' : ''}`;

  if (phase === 'preparing') return (
    <main className={`${rootClass} is-preparing g-dark`}>
      <OnboardingArtwork eager scene="preparing" />
      <div aria-live="polite" className="g-onboarding-preparing-status" role="status">
        <BrandMark size="hero" />
        <p className="g-onboarding-welcome-kicker">A few choices. Something entirely yours.</p>
        <Masthead align="center" headingRef={headingRef} lede="Bringing your interests and favorite creators together. Your Home will open in a moment." title="Preparing your Home" />
        {previewChannels.length ? <div aria-hidden="true" className="g-onboarding-preparing-creators">{previewChannels.slice(0, 4).map((candidate) => <figure key={candidate.channel_key}><Artwork alt="" className="g-onboarding-preparing-preview" fallback={<CreatorPortrait candidate={candidate} />} src={candidate.preview_artwork_url} /><figcaption>{candidate.display_name}</figcaption></figure>)}</div> : null}
        <ProgressBar label="Preparing your Home" value={null} />
      </div>
    </main>
  );
  const starterCategories = categories.filter((category) => FIRST_INTERESTS.includes(category.key) || selected.includes(category.key));
  const visibleCategories = showAll || categories.length <= 8 ? categories : starterCategories.length ? starterCategories : categories.slice(0, 8);

  return (
    <main className={`${rootClass} is-interests`}>
      <div aria-hidden="true" className="g-onboarding-progress"><ProgressBar label="" value={50} /></div>
      <div className="g-onboarding-panel">
        <JourneyHeader step="interests" />
        <section className="g-onboarding-interest-art" aria-label="Discover your taste">
          <OnboardingArtwork eager scene="interests" />
          <div className="g-onboarding-art-copy"><p>One Home.</p><strong>Entirely your taste.</strong><span>For the things you come back to.<br />And the things you haven’t found yet.</span>
            {selected.length ? <div aria-hidden="true" className="g-onboarding-taste-preview">{categories.filter((category) => selected.includes(category.key)).map((category) => <span key={category.key}>{category.label}</span>)}</div> : null}
          </div>
        </section>
        <section className="g-onboarding-interest-workspace">
          <p className="g-onboarding-step">Step 1 of 2 · Your taste</p>
          <Masthead headingRef={headingRef} lede="The stories, sounds, and rabbit holes you love. Pick what draws you in — we’ll start there." title="What draws you in?" />
          {interestsError ? <ErrorState onRetry={onRetryInterests} title="We couldn’t load your interests." body="Try again, or explore your vault without setup." /> : categories.length ? <fieldset className="g-onboarding-interests" disabled={busy}>
            <legend>Choose interests</legend><p className="g-onboarding-interest-hint">As many or as few as you like.</p>
            <div className="g-onboarding-interest-grid">{visibleCategories.map((category) => {
              const Icon = INTEREST_ICONS[category.key] || Compass;
              return <label className="g-onboarding-interest" key={category.key}><input checked={selected.includes(category.key)} onChange={() => toggle(category.key)} type="checkbox" /><Icon aria-hidden="true" className="g-onboarding-interest-icon" /><span>{category.label}</span><span className="g-onboarding-interest-check"><Check aria-hidden="true" /></span></label>;
            })}</div>
            {categories.length > visibleCategories.length ? <Button disabled={busy} onClick={() => setShowAll(true)} variant="quiet">Explore all {categories.length} interests <Plus aria-hidden="true" /></Button> : null}
            <p aria-live="polite" className="g-onboarding-selection-count">{selected.length ? `${selected.length} selected — a place to start.` : 'Follow your curiosity. You can also continue without choosing.'}</p>
          </fieldset> : <Skeleton count={4} label="Loading interests…" shape="still" />}
          <p className="g-onboarding-assurance">Just for your recommendations. Your Library stays as it is, and you can change your taste anytime.</p>
          {error ? <p className="g-onboarding-error" role="alert">{error}</p> : null}
          <div className="g-onboarding-footer"><Button disabled={busy} onClick={onSkip} variant="quiet">Skip for now</Button><Button busy={busy || interestsLoading} disabled={busy || interestsLoading || Boolean(interestsError)} iconEnd={<ArrowRight />} onClick={() => onContinue(selected)} variant="primary">Continue</Button></div>
        </section>
      </div>
    </main>
  );
}
