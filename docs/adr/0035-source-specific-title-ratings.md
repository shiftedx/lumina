# ADR 0035: Keep title ratings separate by source

- Status: Accepted
- Date: 2026-10-28

Title heroes show IMDb user ratings, Rotten Tomatoes critic percentages and Metacritic scores as separate metadata fields. Each field uses the existing source precedence and edit/revert machinery independently: an NFO IMDb score must not prevent another provider from filling the other two scores. TMDB's community score, the generic Jellyfin critic score and parental certificates retain their existing meanings; none can stand in for a named score.

Named NFO ratings and household edits work locally. Optional OMDb enrichment uses exact IMDb identities, with its own provenance at the same precedence as TMDB. A separate ratings batch runs in the existing metadata loop, independently of TMDB requests and without fetching when someone opens a hero. Known IMDb identities can receive ratings without a TMDB key. An OMDb failure or incomplete response preserves cached scores and does not fail a successful TMDB refresh. Identify and Unmatch clear scores belonging to the previous online match while retaining NFO values and household edits.

The ratings queue is derived from durable title and IMDb-cache records. New unscored titles take priority over the older unscored backlog and stale scores. Successful lookups, including partial or empty results, are cached by IMDb identity so duplicate files do not spend additional requests or repeatedly ask for scores a provider does not publish. Persisted retry times and expiring claims let unfinished work resume after a process or container restart; returned scores are applied only while the title still has the same IMDb identity.

Every outgoing OMDb request, including a connection test, reserves one of 950 slots before network I/O in a short SQLite write transaction. The durable reservation ledger enforces a rolling 24-hour window across workers and restarts. A rolling window avoids assuming the provider's daily reset timezone, and leaves 50 of a 1,000-request allowance for use outside Lumina. Failed requests and interrupted work retain their reservations because the provider may already have counted them. Provider quota responses pause further requests durably. Refresh all cannot bypass the cache or this budget.

The server keeps the OMDb key write-only and removes it from backups. Requests use fixed HTTPS hosts, the public-address transport and bounded responses. OMDb is an opt-in external service with its own availability, quota and content terms; no IMDb, Rotten Tomatoes or Metacritic pages are scraped. Missing scores are omitted, including when a provider does not cover a title.

Source logos identify the scores visually, with source names retained for assistive technology. Rotten Tomatoes uses its Fresh tomato for scores of at least 60% and its Rotten splat below 60%, following [the published Tomatometer rules](https://www.rottentomatoes.com/about). A score alone cannot establish Certified Fresh status, which requires a separate designation and review criteria; Lumina does not infer that award.

Metacritic scores use the [movie and TV color ranges](https://metacritichelp.zendesk.com/hc/en-us/articles/15456077802647-What-s-with-these-green-yellow-and-red-colors): green from 61, yellow from 40 to below 61, and red below 40. The numeric score and an accessible sentiment label accompany the color. Game-specific thresholds do not apply to these titles.

Native item DTOs expose the same stored values through the optional `LuminaIMDbRating`, `LuminaRottenTomatoesRating`, and `LuminaMetacriticRating` extensions. Missing, nonnumeric and out-of-range values are omitted independently. The legacy `CriticRating` remains separate; native heroes use it only when no named ratings are available. The lightweight `LuminaCatalog` projection omits these detail fields; opening a title fetches its normal item DTO. Both native platforms share the rating presentation and the web's source mark artwork.
