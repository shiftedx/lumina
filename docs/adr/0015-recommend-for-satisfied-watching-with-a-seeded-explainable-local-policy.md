# ADR 0015: Recommend for satisfied watching with a seeded, explainable, local policy

- Status: Accepted
- Date: 2026-09-30
- Amends: [ADR 0007](0007-use-one-local-recommendation-policy-across-discovery-surfaces.md), [ADR 0011](0011-recommend-unstarted-media-titles-on-library-surfaces.md)

## Context

ADR 0007 asks for one deterministic, inspectable, non-ML recommendation policy per member. The owner's goal is now concrete: Lumina's suggestions should be good enough that the owner opens Lumina instead of youtube.com. Measured against that goal, the policy as built falls short in five ways (research inventory §6):

- It ranks a household cut of 24 globally popular videos.
- It treats a 5-second bounce as a watch.
- It never learns from being ignored.
- It shows the same list on every load.
- It explains nothing per item.

Determinism is part of the problem: a list that never changes cannot explore, and it goes stale between snapshot refreshes. "Non-ML" was written to rule out trained models and outside services. It was not written to rule out the on-device embedder that ADR 0014 later added. Privacy is not in question: household activity never leaves the install.

## Decision

- **Inspectable and local, with privacy unchanged.**
  - There is still one member-scoped policy across every recommendation surface.
  - Every signal is the member's own: events, watch depth, follows, saves, interests and explicit feedback.
  - Everything is computed on the box.
  - Recommendation data is exported with the member and can be cleared by the member.
  - Cross-member signals, such as household co-watch, stay off unless the household explicitly opts in through a future decision.
  - Raw search queries are still never stored. That now also covers the provider queries the background refresher derives from a member's history: they are never persisted or logged.
- **No model is trained on household data.**
  - Ranking uses counts, decayed sums, means and a bounded, deterministic k-means over one member's own vectors.
  - Constants are set by hand and kept in one place.
  - Embeddings come from the existing on-device model (ADR 0014). They are a fixed feature extractor and are never fitted to the household.
  - Any change to a constant ships with an offline replay run.
  - Every recommended item shows a one-line reason derived from its top-scoring factor and a concrete seed.
- **"Deterministic" becomes "reproducible".**
  - A fixed share of slots is filled by sampling, at the tail of each 12-slot window: 2 of 12 on Home and Explore For you, 1 of 12 on anchored lists (Up Next, More like this, Because you watched), and none on Explore's category rails. This replaces ADR 0007's 25% exploration share.
  - The sampling is seeded by member, local date, surface and context, so a list stays stable within a day and changes across days.
  - The draw probability is logged with each impression, and replay can reproduce any list.
- **Satisfied watching is the objective.**
  - Completion, rewatch, saves and follows raise a channel's or topic's weight. Bounces lower it.
  - Clicks feed only fatigue and metrics.
  - Explicit controls have exact, visible, reversible effects: Not interested, Show fewer from a channel (×0.2, recovering over 140 days), and Don't recommend a channel.
- **Candidates are per member and are fetched off request threads.**
  - A background refresher builds each member's pool from that member's follows, channel listings, recent watches and interests, within fixed provider ceilings.
  - Request threads never call a provider or the embedder. Up Next's synchronous related search is removed.
  - Content fetched for one member's seeds is never a candidate for another member.
- **Library titles go through the same pipeline as remote videos** (amends ADR 0011). This replaces "same weights and 25% exploration share": they keep the shared suppression filter, gain title-specific factors, and take the per-surface exploration share above.

## Consequences

- ADR 0007 is amended: its status line reads "Accepted; amended by ADR 0011 and ADR 0015".
- ADR 0011 is amended: its status line reads "Accepted; amended by ADR 0015". Its exploration share is now set per surface.
- Members are never hard-deleted. Deactivation plus Clear recommendation history satisfies the deletion invariant.
- Two loads on different days differ. A bug report needs the date and surface to reproduce a list.
- A member-scoped event log (`reco_events`) exists, with retention of 35 days for impressions and 400 days for everything else. Diagnostics shows household totals only.
- The recommender makes background yt-dlp calls: at most 40 an hour and 300 a day for the household. They go through the existing guarded seams, and interactive search takes priority.
- The legacy policy stays behind a kill switch (`personal_recommendations`) for one release. It is also the replay baseline.
- The synthetic replay gate is judged against measured oracle ceilings and on paired instances. The replay on a real host keeps the relative targets.

## Alternatives considered

- **Keep ADR 0007's strict determinism.** Rejected: identical lists on every load, and exploration that is only "the most popular unfamiliar item", cannot compete with a feed that changes daily.
- **Train a small model on the box** (logistic regression or gradient-boosted trees on impressions and plays). Rejected for now:
  - A household produces a few thousand impressions a month, which is too few to fit more than a handful of weights without overfitting.
  - A learned weight cannot produce an honest one-line reason.

  Revisit when any member has at least 20,000 impressions and 500 attributed plays, and the replay shows that hand-set constants have plateaued.
- **Use an external recommendation service or YouTube's own feed.** Rejected: household activity would leave the install, and YouTube's feed requires signing in.
