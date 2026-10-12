# ADR 0009: Media titles are a derived, ownerless layer above Library items

- Status: Accepted
- Date: 2026-09-25

## Context

A Library item is one playable file. Jellyfin clients and Lumina's TV and movie views need Series, Season, Episode, Movie and BoxSet entities, with several files (versions) per Movie or Episode and extras beside them. Everything Lumina already stores (playback progress, the watch queue, Household collections, full-text search, artwork, transcripts, the probe cache) is keyed on `library_items.id`, and that id already survives renames through artifact relinking. Jellyfin derives item ids from paths, so a move duplicates items and a rename loses watch state.

## Decision

Add `media_titles` as a derived layer. The scanner builds it from folder structure and NFO files; TMDB can enrich it. Library items point up to their leaf title (`title_id`), or to the owning title for an extra (`extra_type`). Titles have random uuid4 ids; their identity is a separate `key` that versions converge on and renames re-key. Titles have no owner: a title is visible to a member exactly when a visible, non-missing linked item exists at or below it. One predicate, `LibraryService.visible_title_predicate`, serves both the web UI and the Jellyfin API. Each field records its source, and a write lands only at or above the recorded rank (`user` > `nfo` > `tmdb` > `path`), so rescans and refreshes never overwrite household edits. Titles and items are tombstoned, never deleted.

## Consequences

- No item-keyed table changes; Continue watching, the watch queue, collections and search keep working unchanged.
- A title's watch state spans its versions, so replacing a file keeps it.
- Existing imports get titles on the next admin rescan; there is no automatic migration pass.
- A show split across two storage roots becomes two series. A folder renamed and re-encoded at once gets new titles (ceiling noted in the scanner; add provider-id matching when it bites).
- Future root access and rating limits go inside the one predicate.

## Alternatives considered

- Turning `LibraryItem` into Movie/Episode and adding a `media_sources` table: rejected. It re-points every dependent table for no gain.

## Addendum (2026-09-29): categories and music titles (library gallery, schema 8)

- `album` and `artist` are Media title types. A music track is a Library item of kind `track` whose `title_id` is its album, and an album's `parent_id` is its artist. Their keys are global, like boxsets' (`artist:{album artist}`, `album:{album artist}/{album}`), so an album split across storage roots is one album. They are visible through the same predicate: an album through its tracks, an artist through its albums' tracks. They are not in the title search index or embeddings, and the Jellyfin API does not serve them.
- `media_titles.category` (`movies`, `shows`, `anime`) is a derived column. SQL sets it from the folder names of each title's files and the admin's anime folder names; seasons and episodes take their series'. It is recomputed after every scan batch and when the folder names change, and never edited by hand.
- This is additive under this ADR's model, so it gets no ADR of its own. Rolling back to schema 7 deletes the album and artist rows (their tracks keep their progress) and re-stamps; `docker/README.md` has the steps.
