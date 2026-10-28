# Library recommendation review

Date: 2026-10-27. Reviewed commit: `480ea56` in the isolated `recommendation-review` worktree. Scope: review and proposal only; no ranking behavior or production data changed. Examples below are synthetic. Companion: [open-source methods](recommendation-methods.md).

## Recommendation

Keep Lumina's local, member-scoped infrastructure. Improve candidate relevance and representation before adding another ranking framework. The first release should make anchored recommendations trustworthy; the next should make discovery richer. A large catalog with a handful of household members favors content-based retrieval with personal reranking, rather than collaborative filtering trained on the household.

An anchored shelf is a promise: each result must have a defensible relationship to its anchor. A general discovery shelf can explore beyond that relationship, but should say so. Returning fewer results is preferable to filling an anchored shelf with unrelated titles.

## Findings grounded in source

1. **Anchor relevance is a boost, not an eligibility condition.** `backend/app/services/reco/policy.py`, `_anchored` / `_titles`, restrict movie vs series but not content relationship or animation/live-action compatibility. `pool.py:title_candidates` unions personal taste, positive metadata overlap, the newest 50 titles, and anchor neighbors. It does not subsequently require anchor relevance. `ranking.py:context` starts at 1 even with zero similarity, and `rank` can fill exploitation slots from those candidates. Novelty is therefore not the only possible cause of irrelevant suggestions.
2. **The fallback cannot recognize the subject of a synopsis.** `profile.py:tokens_of` uses genres, five actors, directors/creators, collection, category, and decade. It omits synopsis, setting, themes, and tone. Token Jaccard treats broad metadata as overlap; category/decade are excluded from anchor-neighbor nomination but still enter ranking similarity. `normalise_topic` rescales similarities relative to the candidate population: strongest among weak matches can become 1.0, which is not evidence of absolute relevance.
3. **Global taste can compete with the anchor.** `ranking.py:score` multiplies general profile relevance, freshness, rating, fatigue, feedback, and context. General affinity is useful for Home, but an unrelated favorite genre should not win entry into a tightly anchored shelf. Profile centroids also combine satisfied remote and title vectors; assess whether separate movie/TV and remote taste views improve results without introducing separate policy stacks.
4. **Shelf labels can overclaim.** `routers/discovery.py:get_home_title_rows` labels every nonempty anchored list “Because you watched …”, regardless of item reasons. A result whose actual reason is “New in your library” remains under that claim. `routers/jellyfin_home.py` rebuilds native title rows from IDs via `_title_dtos`; that path does not carry the original per-item recommendation annotations through. Extend the native response contract to retain explanation and attribution, with native-client contract tests.
5. **Earlier shelves consume later shelves.** `policy.py:title_rows` ranks each list, then `_unseen` removes items already shown without replacement. This can leave a later shelf with only weak leftovers and can starve Recommended for you. It is a plausible amplifier of short rows, not a verified explanation of any particular screenshot.
6. **There is substantial infrastructure to reuse.** Local synopsis embeddings (`embeddings.py`, ADR 0014), member-scoped progress and feedback, taste centroids, DPP diversity, calibration, reproducible exploration, served-list attribution, and offline replay already exist. Replacing them wholesale would increase complexity without fixing the eligibility mistake.

The screenshot alone does not establish the deployed commit, whether the personal-policy switch is on, embedding coverage, metadata quality, or the exact served-list factors. Those should be checked with local diagnostics before claiming the exact cause. The legacy title policy also permits general/exploration candidates; any repair needs a safe legacy fallback.

## Reproduction and verification

A synthetic direct call to the current `rank` function used a medical-drama anchor with tokens `genre:drama`, `cat:tv`, `decade:2010`, an empty member profile, and two library candidates: animation with no shared tokens, and sport with only a shared category. Surface: `home_because`; requested size: 20; embeddings absent. Result:

```text
unrelated-sport exploit New in your library
unrelated-animation exploit New in your library
```

This proves the ranker admits a zero-similarity item into an anchored exploitation list. The source trace above establishes how the newest-title branch can supply such candidates. This was not a replay of private household data or an end-to-end reproduction of the screenshot.

Focused existing tests: 99 passed; 2 API test setup errors because the available backend environment lacks `authlib`. Command: `mise exec -- <existing-backend-python> -m pytest -q -p no:cacheprovider tests/test_reco_policy.py tests/test_reco_ranking.py tests/test_title_recommendations.py`, executed against this worktree's backend. The existing interpreter was reused without installing into or editing the other checkout.

Required `mise exec -- make check` attempted: bootstrap tests (16) and browser-driver unit tests (3) passed; gate stopped at frontend typecheck because this fresh worktree has no `tsc` installed. Full gate is not green. No product changes are proposed as verified or ready to merge by this review.

## Proposed pipeline

### 1. Retrieve titles by meaning

Use the existing public metadata and local embeddings, adding an inexpensive lexical baseline over synopsis and structured subject terms. Weighted TF-IDF or BM25 should distinguish a rare subject from the generic word “drama”. Use normalized metadata for form (animation/live action), genre, setting, themes, tone when supported by evidence, creators, and franchise. Missing metadata must reduce confidence rather than invent a match.

Retrieve a bounded union from lexical subject matches, semantic neighbors, creator/franchise links, and personal taste clusters. Keep the source and supporting features for each nomination. For anchored shelves, all retrieval channels then pass the same anchor eligibility test. Cache/index features in background; no synchronous provider or model calls in a Home request. Use the existing bounded pool first; benchmark exact vector retrieval before adding ANN infrastructure.

### 2. Gate before ranking

For “Because you watched” and “More like this”, require independently meaningful anchor evidence. Same decade, broad category, rating, or freshness alone cannot qualify. Generic genre overlap alone should not justify a strong explanation. Calibrate separate lexical and embedding thresholds against judged pairs; do not invent a universal cosine cutoff or use within-pool percentiles as confidence.

Prefer compatible format for an anchored rail. An animation/live-action crossing needs a strong thematic bridge; otherwise place it in a separate discovery context. Do not infer that watching live action means a permanent dislike of animation, and do not infer format from spoken language or country.

Apply visibility, availability, suppressions, and started-title policy first. Apply the relevance gate to exploration and fallback paths too. Hide an empty shelf; do not backfill from newest/popular titles. Amend ADR 0015's anchored exploration allowance if removing those slots; an alternative is to explore only among qualified anchor matches.

### 3. Rank for the surface, then diversify

Within eligible anchored candidates, rank primarily by anchor relevance; personal taste can break close ties and promote compelling adjacent matches. Cap rating/freshness effects so they cannot compensate for weak meaning. General Home recommendations can use multiple taste clusters and broader discovery.

Retain DPP as the baseline; compare MMR as a simpler reranker in offline evaluation, not as an additional layer. Diversify among relevant candidates, avoid franchise/cast domination, and reserve useful candidates across shelves before rendering them. Fill depleted rows only with additional qualifying candidates. Version policy and cache keys so a changed gate does not serve an old list.

### 4. Make discovery useful to a person

Proposed shelf intents:

- “More medical workplace dramas”: precise matches to a satisfied anchor.
- “A different take on ethical dilemmas”: supported adjacent themes, with a clear explanation of the bridge.
- “From your other interests”: preserve more than one taste instead of letting the latest binge dominate.
- “Overlooked in your library”: relevant, unstarted titles that have not repeatedly been shown; not merely obscure or old.

Explanations must come from verified ranking evidence: “Hospital setting and difficult ethical decisions”, “Same creator”, or “From your saved interests”. Do not ask an LLM to rationalize an arbitrary selected title after ranking.

Offer “Not for me”, “Already seen”, and a reversible “Less like this” with a clear scope. For cold start, a small selection of liked titles is more informative than generic genre selection. Cap evidence per series/session so a long binge does not erase other tastes. Preserve member privacy and explicit feedback precedence.

Native parity matters: carry list ID, position, reason, and policy version through the native Home API; record actual visible impressions and attributed plays. An unloaded or off-screen card is not a rejection. Playback progress remains useful without click attribution, but cannot identify which shelf helped.

## Evaluation and implementation sequence

1. **Trust repair:** add public-service/API regression fixtures for realistic anchor matches, unrelated highly rated/new candidates, no good matches, missing embeddings/metadata, animation crossings, and legacy fallback. Reuse existing policy/API tests. Preserve member access and suppression contracts. Ship the gate and truthful shelf behavior first.
2. **Content quality:** introduce lexical synopsis features, measure metadata/vector coverage, compare lexical-only, embedding-only, and hybrid retrieval. Use existing embedding lifecycle and metadata invalidation. Benchmark a large synthetic library and update the ADR for boundary changes.
3. **Discovery and learning:** improve cross-shelf allocation, native explanations/events, and feedback. Then tune diversity and exploration against satisfaction outcomes.

Build a blinded, judged set of anchor/candidate pairs across varied content domains, including difficult near-misses and sparse metadata. Measure candidate recall separately from top-list precision/NDCG; a ranker cannot rescue a title that was never retrieved. Evaluate confidence/empty-row rate as well as relevance. These are proposed acceptance measures, not measured improvements.

Extend the existing temporal replay with semantic judgments; next-watch prediction alone does not test whether an anchor explanation is true. Report coverage and diversity only alongside relevance so random results cannot win. Respect replay's documented historical-snapshot limitations; synthetic results are contract evidence, not evidence of household satisfaction.

After a small rollout, measure qualified starts, return to the recommended series, satisfied watch depth, saves, explicit hides, repeated ignored exposures, and time to choosing something. Attribute by surface and policy version. Compare descriptive results conservatively at household scale; do not claim statistical wins from a few clicks. Keep latency and zero access-control regressions as release requirements. Any future implementation still needs the full repository gate.
