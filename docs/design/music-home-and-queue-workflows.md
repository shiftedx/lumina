# Music Home and queue workflows

Implementation contract for [#344](https://github.com/shiftedx/lumina/issues/344) and [#345](https://github.com/shiftedx/lumina/issues/345). Queue behavior follows [ADR 0026](../adr/0026-member-owned-music-sessions-use-revisions.md).

## Scope and decisions

| Decision | Contract |
| --- | --- |
| Home shelves | Resume, Recently Played, Playlists, Downloads and Mixes, in that default order. Omit enabled shelves with no content, except Downloads, which shows an empty state. Hidden shelves remain visible in the editor for restoring them. |
| Member state | Shelf visibility and order are member-owned server preferences. Default or invalid state resolves to the default order with every shelf visible. Reset replaces the saved value with the default. |
| Device state | Continuation is device-local, off by default. Download availability, retained bytes and local queue checkpoints remain device-local. They never establish server authority. |
| Resume | Restore reads the member session paused and presents the saved occurrence and position. Resume starts that occurrence only on an explicit action. Active playback stays active while browsing. Restoring a checkpoint never recreates a remote output. |
| Recommendation action | Every result is previewed before it changes playback. Play Mix replaces the current local queue only after confirmation. Add to Queue appends. Save as Playlist saves the preview ordering. |
| R1 matching | R1 uses current metadata and listening state only. Its reasons identify the actual rule. Audio similarity is scheduled for Release 2. |
| Queue authority | The revisioned member session remains authoritative. Generated entries are an additive #346 contract. R1 clients that do not understand provenance may display the playable queue and must preserve order and position when writing it. |

## Home read and preference contract for #344

`GET /api/me/music-home` returns authenticated, member-scoped shelf data and the canonical layout. Reuse the recently played album projection, newest first, and existing visible playlist summaries. Cap both at 12. Return Rotation, Favorites and Unplayed as descriptors and load tracks on demand. Native uses the same service at `GET /jellyfin/lumina/music/home` and `PUT /jellyfin/lumina/music/home/layout` under its connected-app identity. Derive download availability on the device and apply current visibility before returning any catalog or session metadata.

```ts
type MusicHomeShelf = 'resume' | 'recent' | 'playlists' | 'downloads' | 'mixes';
type MusicHomeLayout = { order: MusicHomeShelf[]; hidden: MusicHomeShelf[] };
type MusicHome = {
  layout: MusicHomeLayout;
  resume: MusicSessionSummary | null;
  recent: MusicItemSummary[];
  playlists: MusicPlaylist[];
  mixes: MusicMixSummary[];
};
```

`PUT /api/me/music-home/layout` replaces `order` and `hidden` after validation. Each field accepts each known shelf at most once. `hidden` is a subset of the canonical `order`. Append omitted known shelves in default relative order. Reject unknown or duplicate IDs. Reset writes the full default order and an empty `hidden` list. Parallel edits use last confirmed server state on the next refresh. #344 does not introduce a layout revision because layout edits do not mutate playback state.

Resume summary uses the existing session revision, queue occurrence index, track title, album artwork, position and duration where known. It is absent for an empty session. Hidden/missing entries are pruned by the existing session read before Home is generated. A pending acquisition at the active logical entry reports **Waiting for import**. It does not offer Resume until a playable occurrence exists.

State handling is shared across clients.

| State | Presentation | Action |
| --- | --- | --- |
| No session | Omit Resume | Play library content |
| Paused playable session | Track, queue position and saved time | Explicit Resume starts device-owned playback |
| Session conflict | Current local snapshot stays visible behind a conflict sheet | Use server or Keep this device, then refresh Home |
| No local download | Downloads shelf shows remote item state | Download, remove or play online |
| Missing or evicted local bytes | Downloaded item is marked unavailable | Retry/download online. Do not claim offline playback |
| Pending Wait entry | Resume shows waiting state | Wait for import or choose a later explicit skip action |

## Contextual mix contract for #345

`GET /api/music/contextual-mix?seed_kind=track|artist|album&seed_id=…&exclude_item_ids=…` returns at most 100 visible, playable tracks. Use repeatable `exclude_item_ids` query parameters, capped at 100 unique IDs. Mount the same service at `GET /jellyfin/lumina/music/contextual-mix`. This route leaves the existing `/mixes/{kind}` dispatch intact.

```ts
type MusicMixReason =
  | { code: 'seed_track'; item_id: string }
  | { code: 'same_artist'; artist_id: string }
  | { code: 'shared_genre'; genre: string };
type MusicMixPreview = { seed: MusicMixSeed; items: Array<MusicTrack & { reason: MusicMixReason }>; exhausted: boolean };
```

Resolve the seed under current visibility. Missing or invisible seeds yield the same 404. Valid seeds with no candidates return an empty list. For a track seed, omit the seed occurrence and match its genres or artist. For an artist seed, use that artist and genres on visible tracks. For an album seed, start with visible tracks in disc/track order, then match the album artist and genres. Rank matched candidates by artist match first, then shared genre, then stable item ID. Deduplicate by item ID and apply exclusions before truncation. The client localizes each reason from its code and visible metadata.

| Entry point | Seed | Required R1 reason |
| --- | --- | --- |
| Track row or Now Playing, Track Radio | track | `same_artist` or `shared_genre` |
| Artist detail, Instant Mix | artist | `same_artist` or `shared_genre` |
| Album detail, Instant Mix | album | `seed_track`, then `same_artist` or `shared_genre` |

Preview is read-only. It displays seed, ordered candidates and one reason per candidate. It never begins playback or writes a session.

| Action | Session behavior |
| --- | --- |
| Play Mix | Replace the device-owned queue after the user confirms. Set position to zero and start the first preview item. Persist through the existing revisioned write. With remote output active, use the existing Return flow before replacing or appending. |
| Add to Queue | Append preview tracks after the final logical entry. Preserve the selected occurrence, active position, repeat, shuffle and pending Wait boundary. Do not start playback. |
| Save as Playlist | Create a regular playlist containing preview IDs in display order. It does not touch the listening session. |
| Dismiss, error or empty | Do not mutate the queue or playback. Show an empty/error state with retry only when a retry can change the result. |

A pending acquisition with `unavailable_policy=wait` remains a hard playback and prefetch boundary. Add to Queue inserts after the logical end, never before that boundary. Play Mix is an explicit replacement, so it may remove the pending entry after confirmation. #346 records generated-entry provenance. #345 must operate correctly without that field.

## Client workflow checks

| Workflow | Web | iOS and iPadOS | tvOS |
| --- | --- | --- | --- |
| Home browse | Phone and desktop show all available shelves, compact states and accessible headings. Tab/Shift+Tab reaches shelf controls and order controls. | iPhone compact and iPad regular layouts announce shelf title, position and hidden state. VoiceOver exposes Move Earlier, Move Later, Hide and Reset actions. | Initial focus is visible. The remote can enter shelves and use Move Up, Move Down, Hide and Reset without drag interaction. |
| Resume | Click or Enter on Resume starts the displayed occurrence from the saved position. | VoiceOver announces saved position. Background/foreground retains the paused snapshot. | Back from player returns focus to Resume when that action opened playback. |
| Mix | Keyboard opens preview from track, artist and album. Escape dismisses without a queue write. | VoiceOver announces seed, count, each reason and the three distinct actions. | Focus enters preview at the first actionable control. Back dismisses and restores focus to the initiating action. |
| Conflict and unavailable | Current queue stays visible. Explicit resolution or download/retry is required. | Same semantics with no automatic start. | Same semantics with visible focus on the recovery action. |

## Release 1 queue continuation

At natural end of deliberate device-owned playback, an opted-in connected client may request a bounded batch anchored to the last played occurrence. Use #345 exclusions to suppress already queued tracks and recent recommendations. Append via one authoritative revision write. Mark each appended occurrence as generated. Ignore late responses after Stop, sign-out, an ownership change, a new playback intent or a revision conflict. Repeat and unresolved Wait take priority over continuation. Empty, timeout and offline end the queue normally. Removing unplayed recommendations preserves manually queued occurrences, even when IDs match.

## Evidence

See the [fixture and baseline record](music-home-baseline.md) for the seed, reproduction commands, samples and performance budgets. Reuse the same fixtures for the #344 and #345 contract tests.
