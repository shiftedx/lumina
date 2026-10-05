# ADR 0019: Member access lives inside the one visibility predicate

- Status: Accepted
- Date: 2026-10-04

## Context

Admins need to share only some libraries with a member (a guest gets the Movies, a child gets Shows under TV-Y7) and invite people from outside the house. Every Lumina surface (library grid, search, Home, title pages, artwork, playback, downloads of a file) and the Jellyfin-compatible API already decide visibility through `LibraryService.visible_predicate` (items) and `visible_title_predicate` (titles), which ADR 0009 reserved for "root access and rating limits".

## Decision

- **Member access** is one `member_access` row per restricted member. No row = every Section, no limits; admins never have one.
- Section and rating limits are SQL ANDed into the shared branch of `visible_predicate` (`member_access.item_clause`): a titled item passes when its leaf title's Section (category, or music for albums) is granted and its rating is under the scale's ceiling (movies: G…NC-17; series, seasons, episodes: TV-Y…TV-MA; unrated per the member's choice); an untitled item is the Vault, or `root:<id>` when imported. A member's own items always pass. Titles follow through `visible_title_predicate`, plus a cheap check on the title's own Section and inherited rating that prunes before the item EXISTS run.
- A known member's access (an attached user, or a request/Jellyfin snapshot that carries it) becomes literal values, and an unrestricted member adds nothing. A bare transient User gets the same clause reading the row through uncorrelated subqueries, so the rule is correct whatever session runs it. Raw FTS SQL compiles the same clause.
- Ratings are derived columns (`media_titles.rating`, `rating_rank`) recomputed on flush from the effective `official_rating` field, so the existing field ranks (user > nfo > tmdb) decide; seasons and episodes copy their series'. The rank is on the title's own scale (film for movies, TV for series), a certificate from the other scale taking its nearest equivalent (TV-14 = PG-13, TV-MA = R, R/NC-17 = TV-MA, G = TV-G …), so a ceiling never compares across scales. "Fill in ratings" backfills existing titles without network and queues matched unrated ones for the paced TMDB refresh.
- Hidden titles are simply absent (404 by id, never "blocked"), so a limit leaks nothing about what exists.

## Consequences

- Every existing and future surface that uses the predicate obeys member access with no per-route code; the leak matrix test guards the main surfaces.
- A restricted member pays one primary-key title lookup per candidate item: on the production seed (41.6k items) the library grid page takes ~200 ms for a kid against ~135 ms unrestricted; walls, counts and search are unchanged or faster.
- Token-less art capabilities (Jellyfin image tags, `/api/art` signatures) issued to a restricted member carry their art scope (a digest of their library limits + their id) under the HMAC; a fetch re-checks that member's visibility (cached a minute), so a tag dies when its title is hidden or the limits change and cannot be re-pointed at another member. Unrestricted members and admins keep the shared, stable tags.
- Streaming limits, schedules and screen time are not visibility and are enforced at their own entry points.

## Alternatives considered

- A per-route check after loading: rejected; every new surface would be a new leak.
- Denormalizing Section and rating onto `library_items`: rejected for now; it would need cascades on every title rating and category change. Revisit if a kid's grid page becomes a measured problem.
