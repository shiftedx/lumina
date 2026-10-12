# ADR 0016: An edit is a lock, and the source value is kept for revert

- Status: Accepted
- Date: 2026-10-02

## Context

2.1.0 adds a metadata editor for Media titles in the web app (spec `2026-10-02-library-automation-and-metadata-editor-design.md`). ADR 0009 already ranks field sources (`user` > `nfo` > `tmdb` > `path`) and lets a write land only at or above the recorded rank. Jellyfin separates editing from locking: an edit that is not locked is overwritten by a full refresh, and a lock is set in a separate pane. Users lose edits that way. Lumina also keeps only the winning value, so a "revert to the TMDB value" would need a network refresh. With an item lock, it could not be done at all.

## Decision

- A saved edit writes the field with source `user`, which outranks every scanner and TMDB write. **An edit is a lock.** Locking a field without editing it pins the current value as `user`.
- **Unlock is revert.** A new deferred JSON column, `media_titles.source_values`, keeps the best value that non-user sources offered for each user-sourced field (and for each field of a locked item). It is updated whenever such a write is blocked at an equal or higher rank. Revert restores that value and its source with no network call.
- `media_titles.locked` ("Lock this item") blocks every non-user field write, plus Identify, Unmatch and refresh. The scanner's structural writes (key, type, parent, item links, category) continue.
- A deliberate clear or image removal is a `user` tombstone, because `apply_field` never writes None.
- `media_titles.apply_field` stays the one choke point. Every save, pin, revert, bulk operation and undo is recorded in `title_edits` as a batch, and undo writes the before-values back only where nothing has changed since.
- The Jellyfin API exposes the results read-only (values, `LockData`, `LockedFields`) and accepts no edits. NFO files are never written.

## Consequences

- Nothing a scan or refresh does can silently drop a household edit. "Edited but unlocked" does not exist.
- Lock state is derived from `field_sources`, so there is no second lock list to keep consistent.
- Scans write a little more, because they record kept values on blocked fields only.
- Rolling back to schema 9 keeps every edit, because the `user` source is pre-existing semantics. Item locks and uploaded images are ignored until re-upgrade.

## Alternatives considered

- Jellyfin's model (a separate `locked_fields` list, with edits that do not lock): rejected because edits can be lost.
- Re-fetching from TMDB or re-reading the NFO to revert: rejected because it is slow, needs the network, and is impossible for a locked item or an offline root.
