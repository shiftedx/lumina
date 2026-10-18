# SoulSync competitive analysis: playback and acquisition

Research date: 2026-10-16. Recommendations are proposals, not accepted architecture changes.

## Recommendation

Expand Lumina's Sonarr/Radarr integration beyond requests. Make playback, availability, monitoring, release troubleshooting and import status one coherent household workflow. Add Lidarr as a separate music capability. Keep native online-media acquisition, and consider native movie/TV acquisition only as a later, separately qualified option. Do not make replacing the entire *arr stack a prerequisite for excellent playback.

“Supports all integration features” should mean a documented capability matrix covering the user workflows each connector can actually perform. It should not mean reproducing every upstream administration screen or forwarding arbitrary API calls.

## Evidence and limits

- SoulSync source inspected at commit [`9e35006055a5161cbe26f3b7af010ab5f5f2b093`](https://github.com/Nezreka/SoulSync/tree/9e35006055a5161cbe26f3b7af010ab5f5f2b093), checked out independently in a temporary directory. README claims were checked against source and existing tests; neither application was deployed for a comparative listening/viewing benchmark.
- Lumina baseline HEAD: `7bf92eaf31a2f2e864b5879130ecce4f41e7ca20`, plus the existing working tree. Many application files were already modified when research began. Their presence establishes current implementation evidence, not a released-version claim. Research changes only add documentation.
- Three parallel investigations cover [SoulSync playback](soulsync-playback-2026-10-16.md), [SoulSync acquisition](soulsync-acquisition-2026-10-16.md), and [Lumina capabilities](lumina-capabilities-2026-10-16.md). These contain source locations and distinctions between implementation, test coverage and uncertainty.
- Static inspection can establish an implementation path and identify limitations. It cannot establish production reliability, browser compatibility, listening quality or performance superiority. Proposed acceptance thresholds below are future qualification criteria.

## What “replaces *arr” actually means

SoulSync owns a substantial acquisition pipeline: wanted/monitored media, metadata and release identity, search/RSS, quality ranking/cutoffs, external download-client dispatch, completed-download import, naming, upgrades, rejection/review and history. For video this substitutes for much of the job Sonarr/Radarr do; its native subtitle subsystem overlaps Bazarr. For music it offers native acquisition, matching/tagging and quality workflows, with Lidarr as an optional alternative executor. These are overlapping workflows, not demonstrated API or configuration compatibility. See the [source-backed acquisition matrix](soulsync-acquisition-2026-10-16.md#native-acquisition-coverage).

| Component | SoulSync's relationship | Implication for Lumina |
| --- | --- | --- |
| Sonarr/Radarr | Native video acquisition substitutes for many responsibilities; no direct Sonarr/Radarr connector found | Broad coexistence with existing managers would distinguish Lumina |
| Lidarr | Native music pipeline plus an optional Lidarr download executor | Offer music integration without requiring a complete native music downloader |
| Prowlarr | External indexer search service used by torrent/Usenet paths | Native release acquisition can still depend on Prowlarr |
| Torrent/Usenet clients | External engines, with direct adapters and management controls | “One app” does not mean one process replaces every dependency |
| Bazarr | Native subtitle acquisition overlaps it; no Bazarr connector found | An optional Bazarr integration could complement Lumina playback; its API needs separate qualification |
| Plex/Jellyfin/Navidrome | Library/playlist integrations with differing capabilities | External-server integration differs from Lumina exposing its own Jellyfin-compatible playback API |

The feature breadth is meaningful. Production equivalence to mature release managers remains unproven by this static review; no migration compatibility or reliable round-trip of existing arr policies was established.

## Playback: what to match and where to outperform

Do not reset the music roadmap to zero. Lumina already implements queue editing, playlists/mixes/track radio, synced lyrics, ReplayGain-based normalization, bounded decoded-buffer gapless playback, owner-scoped offline music and Sonos output. It also has local direct/remux/transcode decisions, subtitles/audio selection, shared resume state and a Jellyfin-compatible client surface. These are code/test findings, not comparative device benchmarks; see the [Lumina audit](lumina-capabilities-2026-10-16.md).

SoulSync's music strength is the connection between discovery, a player queue, library ownership and acquisition. Its broader playlist/listening-service ecosystem also matters more than cosmetic Now Playing effects. Distinguish previews from owned full tracks, a crossfade from true gapless, a browser-persisted queue from a cross-device session, and a direct-play route from a universal video playback engine. The [playback audit](soulsync-playback-2026-10-16.md) records the exact implementation limits.

Specific findings from that audit:

- Its experimental crossfade uses two HTML audio elements and timed volume changes for sequential library tracks. The inspected path is not decoder-level gapless and excludes streamed/non-library transitions. Lumina already has a stronger architectural foundation for bounded gapless album playback, but audible/device superiority still needs measurement.
- Its saved browser queue is not a server-owned playback session: reload resets the active index. Missing tracks can resolve to a 30-second Deezer preview. Its service worker does not establish offline media playback.
- The inspected code did not establish Chromecast, AirPlay, Sonos or cross-device output handoff. Lumina's existing Sonos flow is already a concrete output feature to build on; physical-device qualification remains necessary before claiming reliability.
- The inspected podcast UI keeps progress in React state; the audiobook playback surface selects Audible samples. Catalogue/acquisition support should not be confused with a complete durable long-form player.
- Its watch-together flow supplies a real shared timeline, correcting drift every five seconds, with a two-second correction threshold. The inspected stream path uses direct files/upstream range delivery without a transcode branch. It is a useful social feature, not evidence of universal video compatibility.

These make continuity, honest availability and reliable output compatibility better differentiation targets than copying every decorative player feature. Social watch-together is an optional later experiment, once output/session behavior is dependable.

| Opportunity | Present foundation / gap | Concrete next behavior | Proposed acceptance criteria |
| --- | --- | --- | --- |
| Acquisition-aware queue | Lumina can play owned music and acquire online sources, but these are not a single durable mixed-availability queue | Clearly distinguish playable, preview, requested, downloading and failed tracks; allow explicit acquisition without treating a preview as a library copy | Refresh/restart never loses queue intent or double-grabs; unavailable entries can be skipped or waited for by an explicit policy; requesting respects member approval/quota |
| Queue and output continuity | Lumina shares progress, but music queue/index persistence is browser-local; Sonos has a dedicated control flow | Restore a member's queue on another browser, then add explicit transfer to a supported output | Two-device edits have a defined conflict rule; transfer preserves ordering/item/position and stops the old output when acknowledged; logout/revocation removes control |
| Music transitions | Lumina has gapless within its decode memory budget; crossfade is absent | Optional crossfade for mixes, preserving exact album transitions by default; visible streaming fallback for large files | Synthetic PCM verifies transition timing and gain envelope; track boundaries do not clip; memory stays bounded; browser/large-file failures fall back visibly |
| Everyday listening controls | Existing lyrics/normalization/offline are useful; sleep timer and outbound scrobbling are gaps | Sleep timer first; opt-in member-owned listening-service export later | Timer works across track changes; play events deduplicate on retry; seeking/previews do not become false full-track scrobbles |
| Local remote-viewing resilience | Lumina has direct/remux/transcode plus manual conversion quality; upstream remote DASH/HLS already adapts | Qualify a small adaptive ladder for locally converted video only if viewing data justifies it | Shaped-network trials measure startup, stalls, recovery and CPU/GPU cost; preserve chosen audio/subtitles and resume across a switch; enforce total session budgets |
| “Why can't I play this?” | Lumina's pure decisions and acquisition status are existing foundations | One explanation across missing release, download failure, import mismatch, unsupported codec, subtitle burn and resource limits | A member sees a useful redacted reason and allowed recovery; arrival becomes Play only after a visible registered file exists |
| Interoperable video output | Lumina already serves API clients through its Jellyfin-compatible surface; first-party casting/handoff is absent | Start with one demanded target and a truthful output capability list | Verify a real target end to end, including seek/subtitles/resume/revocation; do not infer compatibility merely from a correctly shaped API response |

Preserve the existing performance distinction: local conversion currently has a single-rendition HLS master, while remote upstream streams have adaptive playback. Lumina already has [qualified remote recovery evidence](../performance/remote-streaming-2026-10-11.md) and dedicated direct-start/resume checks (`frontend/e2e/playperf/direct.spec.ts`). Keep those baselines; do not claim SoulSync is slower without an equivalent benchmark.

## Integration scope beyond requests

The official [Sonarr API](https://github.com/Sonarr/Sonarr/blob/a2b6054a8f3746d149a811bddea2a8d063efc35e/src/Sonarr.Api.V3/openapi.json) and [Radarr API](https://github.com/Radarr/Radarr/blob/df2faa64c75e6081556d11232dc46523e7264ea1/src/Radarr.Api.V3/openapi.json) expose release search/grab, calendars, history, queue operations, missing/cutoff listings, manual imports, profiles, custom formats, rename previews, health and administrative resources. These allow integration substantially beyond adding a requested title. API presence alone does not guarantee a particular installed version or safe write semantics.

| Workflow | Proposed Lumina experience | Ownership and limits | Priority |
| --- | --- | --- | --- |
| Existing managed library | Associate imported movies/shows with external identities even if nobody requested them in Lumina | Match provider IDs and episode identity, then scoped file visibility; ambiguous matches require review | First |
| Availability and upcoming releases | Title/season/episode state, calendar and missing episodes, alongside actual playable copies | The external manager owns monitoring; Lumina's visible, registered files establish playability | First |
| Queue and problems | One activity view for all managed acquisitions: searching, downloading, stalled, rejected, importing, playable | Redact paths and provider diagnostics before member display; complete queue pagination | First |
| Acquisition history | Explain the chosen release, failures, imports and upgrades from title pages | Persist external event identity; reconcile after downtime | First |
| Monitoring and preferences | Follow/unfollow titles or seasons; select configured quality profile and root; expose anime preferences clearly | Admin or delegated acquisition permission; preserve upstream edits | Next |
| Interactive release search | Show candidates with quality, language and rejection reasons; explicit select-and-grab | Search and grab through the selected Sonarr/Radarr authority, preserving its policy checks | Next |
| Recovery | Retry, remove or block a failed release with explicit consequences; trigger rescan/import | “Cancel request” remains different from “cancel transfer,” “unmonitor,” and “delete file” | Next |
| Quality upgrades | Display current quality, target/cutoff and upgrade candidates | The external manager executes replacement and file naming | Next |
| Music acquisition | Artist/album monitoring, missing releases, quality profiles, queue/history and import status | Dedicated Lidarr adapter and music identity model; do not force albums into movie requests | Next |
| Indexers | Read health/search diagnostics; choose among user-approved, already-configured indexers for a native search | Prowlarr retains indexer credentials/configuration. In managed mode, use the arr's release search; do not silently bypass its rules | Later/native mode |
| Download clients | Transfer details and deliberate controls for downloads owned by Lumina | Prefer arr queue actions for arr-owned transfers; direct controls only with correlated ownership | Later/native mode |
| Import/rename maintenance | Preview an import or rename and invoke the owning service | Admin-only, explicit file consequences; no competing writers | Later |
| Advanced configuration | Read health, profiles and selected settings; offer upstream deep links | Full indexer/client/notification configuration and backup restoration can remain upstream until a real Lumina workflow needs them | Selective |

The [Lidarr API](https://github.com/Lidarr/Lidarr/blob/aec88028d036707619baacbbb840b30c2d716318/src/Lidarr.Api.V1/openapi.json) exposes artist/album-related acquisition resources alongside release, queue, wanted, history, import and quality APIs. Its identity and completeness semantics need a music-specific contract. The [Prowlarr API](https://github.com/Prowlarr/Prowlarr/blob/b8d04898207b16070c010c8d74b4cc218ff84b8f/src/Prowlarr.Api.V1/openapi.json) exposes indexer configuration/status, search-related indexer access, history, clients and health; Prowlarr is an indexer service, not an alternative playback engine.

## Architecture for native acquisition and external managers

Use one acquisition authority per managed title or explicitly separated edition/root. In externally managed mode, Sonarr/Radarr/Lidarr select releases, import, name and upgrade files. In native mode, Lumina would own those jobs and could use Prowlarr and a downloader. Both modes converge on Lumina's normal scanner and playback library. A request, playlist gap, followed artist or calendar item should express acquisition intent without deciding which engine fulfills it.

Do not let both engines monitor the same edition in the same root. That creates duplicate transfers, conflicting upgrades, rename races and ambiguous cancellation. An ownership transfer should pause the old monitor, reconcile active transfers/files and preserve history before enabling the new owner. User watch state and ratings stay in Lumina; download completion must never overwrite them.

Incrementally extend the existing bounded arr client rather than first building a generic plugin framework. Broader connections must eventually work independently of the requests feature switch: today's synchronizer reads active request rows and exits when requests are disabled (`backend/app/services/requests/sync.py:114`). Keep connection configuration, credentials and network limits consistent with the existing client; add explicit per-connector capabilities and remote ID mappings where the new workflows need them.

Start with polling and full reconciliation; add authenticated event ingestion to reduce import latency when its event contract is verified. Events are wakeups, not unquestioned authority: deduplicate, re-read the owning service, scope imports to configured roots and keep a polling recovery path. Mutation retries need operation-specific reconciliation. An uncertain timeout after a grab must not blindly dispatch another transfer.

Preserve member visibility and distinguish play, request, approve, manage acquisitions and administer integrations. An upstream API key often permits more than an ordinary member should do. Request availability must still mean that a member can access the imported title, as ADR 0018 requires.

## Gates before offering native movie/TV replacement

Native replacement changes the boundary accepted in [ADR 0018](../adr/0018-requests-fulfil-through-household-sonarr-radarr.md); it needs a new decision and an implementation program, not a connector checkbox. Existing online-media acquisition already has an idempotent outbox under [ADR 0004](../adr/0004-use-a-durable-idempotent-acquisition-outbox.md), which is a foundation rather than a complete release manager.

Required qualification should cover:

1. Exact movie, season, episode, daily-show and anime identity; incorrect files and sample releases never become playable matches.
2. Persisted release decisions, custom-format scoring, cutoff upgrades and rejection explanations; a measured downgrade cannot replace a valid copy.
3. Search/RSS deduplication, season-pack handling, client restarts, uncertain dispatch, stalls, blocklisting and bounded retry.
4. Completed-download handling with archive safety, verified media, path mapping, permissions, hardlinks/seeding and crash-safe publication. A validated copy remains available until its replacement is fully published.
5. Import review, repair, history, backup/recovery and migration from an existing manager without a duplicate grab.
6. Disk pressure and fair resource use alongside playback. Existing household resource work is in the dirty working tree and must be qualified before treating it as a released foundation.
7. Explicit lifecycle and deletion semantics. [ADR 0005](../adr/0005-defer-destructive-source-automation-retention.md) deliberately defers destructive retention; SoulSync's cleanup feature list does not justify skipping that prerequisite.

A realistic sequence is broader external-manager visibility first, safe interactive acquisition controls second, and a narrowly scoped native pilot third. Keep integration available even if a native mode is eventually qualified.

## Suggested delivery order

1. **Visibility across the whole managed library:** external identity mapping, health, queue/history/wanted/calendar, import status and title-level explanations. Include media added outside Lumina. Prove downtime recovery, pagination, hidden-title isolation and path mapping.
2. **Listening improvements:** owner-scoped durable queue restoration, acquisition-aware entries, sleep timer and optional crossfade. Keep existing gapless, lyrics, normalization and offline contracts. Then qualify cross-device queue and output transfer.
3. **Safe acquisition operations:** monitoring, profile selection, interactive release decisions and recovery through Sonarr/Radarr. Add a music-specific Lidarr integration. Investigate Bazarr status/search as a playback-focused connector rather than implementing a whole subtitle ecosystem first.
4. **Output and local streaming qualification:** one first-party casting target selected by actual demand, subtitle/audio content defaults, and adaptive local conversion if measured network failures justify its cost. Continue supporting compatible Jellyfin clients.
5. **Native release acquisition decision:** only after the previous capabilities demonstrate a remaining user need. Pilot one media kind in a separate root, with one owner and the qualification gates above.

This ordering captures the value of an integrated household application while keeping release-management replacement an explicit product investment.
