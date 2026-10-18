# Broader arr integration and playback implementation plan

- Date: 2026-10-17.
- Status: implementation in progress; see the [delivery record](arr-integration-playback-implementation.md) for slice status and evidence.
- Execution: read the [PM handoff](arr-integration-playback-handoff.md) before assigning parallel implementation packets.
- Baseline: `a4e01da` plus current documentation changes.
- Inputs: [competitive analysis](../research/soulsync-competitive-analysis-2026-10-16.md), [Lumina capability audit](../research/lumina-capabilities-2026-10-16.md), and three current-code scope reviews covering arr operations, playback, and connector/native-acquisition boundaries.
- Sequence: Phase 1 → Phase 2 → Phase 3; Phase 4 can begin after Phase 1 and run alongside Phases 2–3; Phase 5 follows the shared-session work; Phase 6 remains optional.

## Existing features to extend

| Existing capability | Reuse |
| --- | --- |
| Sonarr/Radarr requests | Approval, quotas, followers, retry/cancel, notifications, anime profiles and dispatch in `backend/app/services/requests/` |
| Arr connection setup | Bounded calls, write-only keys, test, profiles/roots and path mappings in `services/requests/arr.py`, `routers/requests.py`, and frontend request settings |
| Registered library and scoped import | Existing provider-ID matching, member visibility, mapped external-root scans and actual-file availability |
| Music | Owner-scoped local queue restore; shared per-item progress; playlists, radio, lyrics, normalization, bounded gapless and offline listening |
| Sonos | Browser→Sonos and Sonos→browser flows, queue-scoped revocable tickets and output controls; extend continuity and recovery |
| Video | Direct/remux/transcode decisions, manual conversion quality, audio/subtitle selection, caption styling and compatible Jellyfin clients |
| Remote streaming | Existing upstream HLS/DASH adaptation; additional adaptive work applies only to local conversion |
| Resource policy | Household/member playback, encoding, bandwidth and work budgets under ADR 0024 |
| Online acquisition | Existing yt-dlp jobs and durable acquisition outbox; separate from future movie/TV release management |

## Phase 1 Whole library visibility

**Added**

- Integration settings independent of the Requests switch, retaining existing connection configuration and credentials.
- Version/capability discovery for supported Sonarr/Radarr installations.
- Polling observer for all managed titles, including items added outside Lumina.
- Fully paginated queue, history, health, calendar, missing items and quality-cutoff observations.
- External-ID mapping, last-success/staleness state and restart reconciliation.
- Admin operations view and visibility-scoped title availability/import explanations.
- Setup feedback for unmapped folders and external roots awaiting their first import.

**Implementation**

- Extend `backend/app/services/requests/arr.py`; retain request-specific dispatch and synchronization.
- Add a separate arr observation service/router and minimal external-item mapping/sync state; do not make fake request rows for upstream inventory.
- Reuse `requests/status.py`, mapped scoped-import helpers, existing title surfaces and settings components.
- Keep upstream-only/unmatched inventory admin-only unless an explicit member-visible catalog policy permits it; linking must not reveal hidden library titles.

**Exit checks**

- Requests disabled: enabled integrations still display current observations without dispatching requests.
- An upstream-added title appears without a Lumina request; Play appears only after visible registered media exists.
- Queues larger than 1,000 records and missed polls reconcile completely; outages retain the last successful state with a stale label.
- Hidden titles, raw paths, keys and upstream diagnostic bodies remain outside member responses.

## Phase 2 Acquisition controls

**Added**

- Monitor/unmonitor movies, shows and seasons; select existing quality profiles.
- Interactive release search with quality, language and rejection reasons; explicit grab through the owning arr service.
- Retry/search and targeted import/rescan recovery for correlated items.
- Current-quality, target/cutoff and upgrade explanations.
- Durable operation audit, permission checks and uncertain-outcome reconciliation.

**Implementation**

- Extend Phase 1 mappings with an operation ledger and allowlisted commands; use existing request policy where a command initiates acquisition.
- Separate manage-acquisition permission from ordinary playback/request permissions; default operational writes to admins.
- Preserve upstream edits; reread state after writes and before changing a stale resource.
- Treat profile selection separately from moving an existing library root. Root moves, bulk rename, file deletion and retention remain outside this phase.
- Keep cancel-request, cancel-transfer, unmonitor and delete-file as distinct actions.
- Reconcile an uncertain grab before offering retry; block blind retry when upstream correlation cannot prove whether it succeeded.

**Exit checks**

- Timeout, reload and retry produce one correlated acquisition outcome; unresolved outcomes remain visibly pending reconciliation.
- A denied member cannot initiate acquisition through operational controls.
- Upstream-side changes survive a Lumina refresh; each attempted write has a durable result or explicit uncertain state.
- Existing request dispatch, partial availability and notifications retain their contracts.

## Phase 3 Music subtitles and infrastructure connectors

| Slice | Added | Dependency and exit check |
| --- | --- | --- |
| 3A Lidarr | Music-specific artist/album identity; monitoring; approved album requests; queue/history; profile/root configuration for new acquisitions; import correlation | Verify installed API/version and release/completeness semantics. Add a music request contract rather than putting albums in movie/show rows. A requested album becomes playable only after visible tracks import. |
| 3B Bazarr | Conditional connector for missing-subtitle/language status and explicit subtitle search/download | First verify API, authentication, supported versions and identity matching. Commit supported capabilities only after contract discovery; search/download appears only if supported. Existing local subtitle playback remains the consumer. |
| 3C Prowlarr | Read-only health, indexer/search diagnostics and links to upstream administration | Prowlarr retains indexer configuration/credentials. Arr-managed release searches continue through Sonarr/Radarr/Lidarr. |
| 3D Download clients | Read-only correlated transfer health/details and upstream links for selected clients | Select the first client from household demand; no promise of every client at launch. Arr remains the control path for arr-owned transfers; hide unrelated jobs from ordinary members. |

- Connector matrix: declare supported versions, reads, commands, configuration and unsupported actions for each connector.
- Ship 3A–3D independently; Bazarr discovery or another optional connector must not block existing integrations.
- Full upstream settings editors, arbitrary API forwarding, direct arr-owned transfer cancellation and seeding/retention administration remain outside this phase.

## Phase 4 Playback continuity and listening

| Slice | Added | Dependency and exit check |
| --- | --- | --- |
| 4A Shared music session | Member-owned server queue, cursor/position, repeat/shuffle state, active output and revision/conflict policy | New additive storage/API beside per-item `PlaybackProgress`. Browser B restores browser A's queue; concurrent edits cannot silently overwrite it. Reconcile existing local queues on first use; retain offline local playback and define reconnect conflicts. |
| 4B Sonos continuity | Acknowledged transfer and recovery using existing cast tickets, including queue truncation/unsupported-track feedback | Build on 4A and existing browser↔Sonos flow. An acknowledged transfer preserves the supported queue/item/position; failed transfer retains recoverable browser state. Revoke/stop the source only after the target acknowledges. |
| 4C Acquisition-aware queue | Entries for playable, requested, downloading and failed media; explicit request action and skip/wait policy | 4A plus correlated acquisition observations; music acquisition depends on 3A. Use a queue-entry contract distinct from playable `MusicTrack`; registering a visible file is the transition to Play. Preview support, if added later, must be explicitly labeled. |
| 4D Audio/subtitle defaults | Ordered languages, subtitle enabled/forced/SDH preferences, per-play override | Extend existing caption styling/preferences and track resolver ahead of pure playback decisions. Apply to browser and compatible Jellyfin paths where the protocol permits; explicit client/per-play selections win. |
| 4E Listening controls | Optional decoded-audio crossfade and sleep timer | Preserve default gapless album transitions. Mark crossfade unavailable for large-file native streaming and outputs that cannot implement it. Start timer browser-only; device-following timer requires acknowledged output support. |
| 4F Listening history export | Optional member-owned Last.fm/ListenBrainz adapters | Separate provider discovery and replay-safe play-event ledger. Retry/reconnect cannot duplicate scrobbles; seeking, previews and completion use explicit eligibility rules. |

**Implementation**

- Extend `frontend/src/features/music/{musicSession,musicCastSession,musicEngine,MusicPlayer}` and the existing music router/cast-capability services.
- Add a small shared-session service/API; keep queue revisions separate from per-item progress and output-specific capabilities.
- Preserve owner-scoped offline storage, memory caps, manual overrides and connected-app revocation.
- Import existing browser queues by item ID only: revalidate current member visibility and rebuild entries from server data. Remove stale/hidden items with an appropriate member-facing reason; never trust stored manifests, metadata or URLs as server authority.
- Honor newer intentional playback, including rewinds, through an explicit reconnect policy; do not always choose the furthest position.
- Generated subtitle/lyrics artifacts retain ADR 0013 provenance and validation rules.

## Phase 5 Output reach and local video resilience

**Added**

- One additional first-party output target selected by actual household demand and protocol feasibility.
- Per-output capabilities and actionable compatibility/fallback explanations.
- Measured starting-quality improvements for locally converted video.
- Conditional two/three-rendition local HLS adaptation if stall/recovery evidence justifies its encoding cost.

**Dependencies and exit checks**

- Reuse Phase 4 session/control contracts; existing Jellyfin API playback does not by itself establish remote device control.
- Qualify the selected real output: start, pause, seek, resume, applicable subtitles/audio and revocation; advertise only demonstrated capabilities.
- Preserve pure `playback_decision.decide` and ADR 0024 admission/delivery limits; budget all simultaneous renditions.
- Shaped-network trials show a useful stall/recovery improvement without unacceptable startup or encode cost; quality switches preserve position and selected tracks.
- Run real-decode WebKit checks for local remux/transcode changes. Existing remote upstream ABR and manual quality controls remain covered.
- Skip the local ABR ladder if evidence does not justify it; output expansion can ship independently.

## Phase 6 Optional native movie and TV acquisition

- **Decision gate:** establish a remaining user need after broader integration; approve a new ADR before changing ADR 0018's ownership boundary.
- **Pilot scope:** one media kind, separate managed root/edition, explicit native owner; continue offering external arr integration.
- **Added if selected:** native monitoring, release parsing/search/RSS, quality/cutoff decisions, Prowlarr and one selected download-client adapter, validation/import and recovery history.
- **Reuse:** existing acquisition outbox patterns, scanner, visibility predicate, resource budgets and playback library; add operation-specific external dispatch reconciliation.
- **Required gates:** exact release/episode identity; search/RSS deduplication; crash-safe dispatch; failed-release retry/blocklist; path mapping; archive/file validation; hardlinks/seeding; atomic upgrades preserving the previous valid copy; migration and backup/recovery.
- **Ownership transfer:** pause the old manager, reconcile transfers/files/history, then activate the new owner; never two auto-grab/import/rename/upgrade authorities for the same managed edition/root.
- **Retention:** no unattended deletion without satisfying ADR 0005's provenance and lifecycle prerequisites.
- **Exit checks:** crash/restart/uncertain timeout never creates duplicate work; wrong/sample files never become playable; rollback restores one owner and usable media.

## Delivery and verification

- Ship each numbered phase/slice as focused changes; add or amend ADRs when changing ownership, shared-session persistence or member policy boundaries.
- Additive `MIGRATIONS` and operator rollback SQL accompany schema changes; document connection setup, permissions and unsupported capabilities.
- Extend existing request API/service tests, arr fakes, music engine/session/cast tests and browser journeys; test user workflows rather than helpers or CSS.
- Run focused checks while implementing and `mise exec -- make check` before handing back implementation changes; use isolated synthetic media and real supported external services/devices for connector qualification.
- Existing verification baseline: prior full check passed type checking/build, then ended with `5009 passed, 11 skipped, 1 failed`; failing test: `tests/test_live_recording_scheduling_manager.py::test_scheduling_starts_in_waiting_and_runs_the_waiter`. Resolve or independently characterize that failure before claiming a clean implementation gate.
- Planning-only changes: documentation path/link validation and diff whitespace checks; no new runtime behavior or device compatibility claims.
