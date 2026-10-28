# Recommendation implementation

Implemented in the isolated recommendation-review worktree. This is a local code change, not a deployment. The earlier [review](library-recommendation-review-2026-10-27.md) and [research](recommendation-methods.md) remain the design context.

## Behavior

- Because you watched and More like this require a concrete anchor relationship in both the personal and legacy policies. Broad genre/category/decade, freshness and rating cannot alone qualify. Short or absent rows are intentional when evidence is missing.
- Public synopsis and metadata subject tokens complement existing local vectors. Inverse document frequency weights nominate older subject matches beyond recent arrivals, both from an anchor and from satisfied history. Processing is bounded and introduces no new dependency or request-time model/provider call.
- Anchored ranking emphasizes the verified relationship, with reasons derived from that evidence. Explicit animation on only one side requires stronger thematic support; unknown metadata is not treated as proof of live action.
- Home budgets its anchor rows to leave room for general discovery and excludes previously assigned titles before ranking later shelves. Served positions remain faithful to what is shown. Existing DPP and calibration remain in place.
- Native Home carries explanations, records visible-dwell impressions and opens, and offers title suppression with Undo. The server still attributes actual plays at the existing playback-progress boundary. Feedback survives concurrent Home refresh and is scoped to the current member.

## Evaluation changes

`scripts/perf/reco_relevance.py` supplies a small independent, synthetic English editorial set covering medical, legal, ocean and space subjects, unrelated animation, generic or missing metadata, and abstention. It checks both anchored surfaces without embeddings. It is a regression check, not a multilingual benchmark or evidence of measured household satisfaction.

The existing temporal replay is retained. Its anchored metric now counts exact title hits (previously its string-based dispatch accidentally counted first-genre matches). Trivial anchored baselines must pass the same relevance contract. The report records an eligible-hit ceiling; a tie can satisfy an ordering comparison only when it reaches a nonzero achievable ceiling. The original watch-history sampling order is preserved. Public synthetic synopses were added to exercise the newly available lexical signal; past scores are therefore not directly comparable with this protocol.

Replay evaluates surfaces independently, not the combined Home allocation. Public policy tests cover cross-shelf allocation and served-list attribution separately. Thresholds remain hand-set heuristics; larger judged datasets and real satisfied-viewing outcomes are required before claiming calibrated relevance or user uplift.

Test fixtures were updated to exercise meaningful subject matches, including the real-server recommendation-to-playback attribution journey. Two unrelated timing assumptions were repaired without weakening their assertions: playback expiry is relative to the current monotonic clock rather than requiring ten minutes of host uptime, and the mini-player keyboard fixture gives its short synthetic clip enough playback time for reliable control inspection.

## Verification

- Full backend: 5,545 passed, 20 prerequisite skips; frontend unit tests: 2,721 passed.
- Native Core suite and focused Home regressions passed; unsigned iOS and tvOS simulator builds succeeded.
- Independent synthetic relevance check: all 16 surface/abstention cases passed.
- Rich 10,000-title benchmark under the shared performance lock: similar-title first/median/p95 358/51/52 ms; Home title rows 431/146/149 ms. Existing warm budgets were retained (similar median/p95 72/120 ms; rows 150/250 ms).
- Full `mise exec -- make check` passed, including typecheck, production build, style budgets, backend/frontend suites, and all 649 browser cases (17.7 minutes for the browser tier). The real-server recommendation attribution journey and mini-player regression both passed.

Lexical performance uses a title-term index shared with the existing facts cache. Cache changes and publication are synchronized, and every lookup intersects the current member's visible, unstarted, type- and time-eligible IDs. This adds memory and serializes title-index refreshes; the measured warm budgets are not a claim about arbitrary concurrent deployments.

## Remote Up Next follow-up

The initial title work did not cover YouTube/Twitch Up Next. That separate path now
requires meaningful current-video evidence, excludes category-only/creator-only
matches and generic title boilerplate, keeps recommendations within the current
provider, and leads with the strongest match or a verified next part. The legacy
path uses the same admission rule. Access checks cover the actual URL and declared
provider.

Web autoplay carries a bounded, member-scoped recent-source chain to prevent
A → B → A loops, including through saved video copies. Explicit queues remain
first; a deliberate new selection resets the chain. Saved Twitch videos use the
same request path, and failed Up Next loads expose retry. A next-video preview
failure stops with an error rather than trying unrelated alternatives. Apple has
no remote Up Next client path today.

The old synthetic `up_next_channel_hit@10` remains a diagnostic but is no longer an
acceptance target: it rewards a creator match even when the topic changes. The
independent remote editorial contract instead checks relevant first choices,
wrong-game/creator drift, and abstention; service and app workflows test provider
boundaries and session continuity. Original synthetic watch order and other replay
targets remain unchanged. This changes the evaluation protocol and supplies no
claim of measured user uplift. Sparse provider pools and heuristic title/vector
matching remain limitations; no new provider retrieval service was added.

Remote follow-up validation: full `mise exec -- make check` passed with 5,555 backend tests (20 prerequisite skips), 2,730 frontend tests, and all 649 browser cases. Typecheck, production build and style budgets passed. The independent editorial check passed all 16 title cases and 5 remote cases. The verification figures above describe the earlier title-stage run.
