# ADR 0023: Keep music edits above audio artifacts

- Status: Accepted
- Date: 2026-10-12

## Context

Music needs editable artist and release identities, protected track curation and bulk artwork cleanup, while external storage scans remain read-only. Browser offline listening and Sonos output need their own access boundaries without giving devices household credentials.

## Decision

Artists and releases remain derived, ownerless Media titles; playable tracks remain visibility-controlled Library items. Explicit track edits live in a separate override with conflict checks and undo, so rescanning original tags cannot erase household curation or rename a manually chosen destination release. Writing changes into audio files is a separate, opt-in owner action with an expiring preview, an atomic replacement and a digest-guarded original backup.

Public identities, lyrics and performance discoveries are cached and bounded. Confident unique matches enrich unlocked fields; ambiguous identities wait for review. Public provider data uses the existing source-precedence and manual-lock rules, and saved video associations retain the video's own visibility. Sonos receives expiring capabilities for a specific member and queue, rather than browser credentials. Offline audio stays in owner-scoped browser storage and the offline service worker caches only the static listening shell.

Artist matching may use two distinct registered album names to distinguish a unique artist among at most three exact name or alias matches, only with complete candidate and discography results. Artist portrait discovery follows the matched identity's Wikimedia or Deezer links. Blocked Wikimedia originals first retry the same file at the standard 500 px size without changing identity or attribution. Deezer is a fallback for absent or unavailable Wikimedia photos; its returned artist ID must equal the linked ID, and downloads use an exact host and artist-image path allowlist. A name search or album sleeve does not establish a portrait identity. Missing photos are cached for a week and transient provider failures for an hour, within the existing worker and disk budgets. Failed provider portraits can invalidate an older metadata cache once per hour and enqueue a deduplicated repair; this does not bypass user artwork, local artwork, or item locks.

Music's `/audio` route is independent of Video and the Books integration seam. The browser schedules decoded audio buffers against its audio clock for gapless album transitions; very large files use a clearly labeled native streaming mode because whole-track PCM decoding cannot have an unbounded memory budget.

## Consequences

Schema 18 adds music lookup, track override, edit history, playlist, cast capability and file backup journal tables. Existing media artifacts, title fields and member visibility predicates remain authoritative. Rolling back retains these tables and the original-file backups; a database rollback does not undo tags already written into audio files. A file restore serializes with writeback and checks the current digest before replacing the original.

The separate offline entry uses the same semantic styles but ships only the listening shell. Aggregate CSS across both entries and lazy routes has a 60 KiB gzip budget, measured at 57 KiB on this change; the shared 7 KiB style-core and existing JavaScript chunk caps stay fixed.

## Alternatives considered

- Writing every edit directly into original files would make read-only libraries unusable and metadata undo dependent on mounts remaining online.
- Giving speakers a browser cookie would expose household sessions to network devices and prevent queue-specific revocation.
- Caching authenticated API responses in a general service worker would mix offline music with unrelated household data. The static shell and member-scoped saved music have separate storage.
