# ADR 0007: Use one local recommendation policy across discovery surfaces

- Status: Accepted; amended by [ADR 0011](0011-recommend-unstarted-media-titles-on-library-surfaces.md) and [ADR 0015](0015-recommend-for-satisfied-watching-with-a-seeded-explainable-local-policy.md)
- Date: 2026-07-18

## Context

Lumina needs personalized discovery on Home, Up Next, and related-media surfaces without turning each surface into an independent heuristic, copying a provider's opaque ranking, or sharing household activity with an external recommendation service. Interest categories should help a new Household member reach useful content quickly, while continued discovery must still surface unfamiliar creators, adjacent interests, and less-obvious media.

## Decision

Lumina uses one member-scoped recommendation policy to rank remote Media sources for Home, Up Next, and related discovery. The policy is deterministic and inspectable, combines Interest categories, followed channels, playback activity, saves, recency, and popularity, and reserves a configurable share of results for exploration beyond the member's established preferences. Each surface may apply contextual weighting, such as stronger similarity to the current media for Up Next, but must consume the shared candidate and suppression policy.

Raw search queries are not persisted as recommendation signals; opening or watching a search result supplies the signal instead. Saved Library items remain in Library and Continue Watching surfaces rather than appearing as recommendation candidates. Household members can reversibly suppress one Media source or a source channel without unfollowing it, removing saved media, erasing playback history, or affecting another member.

## Consequences

- Recommendation behavior remains local to one Lumina installation and isolated per Household member.
- Ranking can be explained and tested without committing Lumina to machine learning or collaborative filtering.
- Interest categories seed discovery but do not confine it.
- New recommendation surfaces must reuse the shared policy rather than invent a separate ranking pipeline.

