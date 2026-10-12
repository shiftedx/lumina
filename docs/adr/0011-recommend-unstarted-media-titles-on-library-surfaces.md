# ADR 0011: Recommend unstarted Media titles on library surfaces

- Status: Accepted; amended by [ADR 0015](0015-recommend-for-satisfied-watching-with-a-seeded-explainable-local-policy.md)
- Date: 2026-09-25
- Amends: [ADR 0007](0007-use-one-local-recommendation-policy-across-discovery-surfaces.md)

## Context

ADR 0007 keeps saved Library items out of recommendations and ranks only remote Media sources. The media vault now holds movies and shows the household has never started. "More like this", "Because you watched" and Jellyfin's Similar and Suggestions need ranked picks from that library.

## Decision

The one recommendation policy gains a second candidate pool: unstarted, visible Media titles. It reuses the same score shape, weights, suppression filter, ranking and 25% exploration share, with title-specific signals (genre and people overlap with watched titles, favorites, anchor similarity, recency within the library, community rating). The title pool appears only on library surfaces: title pages, Home title rows, and Jellyfin Similar and Suggestions. Remote discovery feeds still exclude library items. Started or completed titles belong to Continue watching and Next up, not to recommendations. Title suppressions reuse the existing suppression table with `scope="title"`.

## Consequences

- There is still one inspectable, local, per-member policy; no surface invents its own ranking.
- ADR 0007's rule "saved Library items remain in Library and Continue Watching surfaces" now reads "…and in library-surface recommendations of unstarted titles".
