# SoulSync acquisition and integration assessment — 2026-10-16

## Scope and evidence

This is a static review of SoulSync at upstream commit
[`9e35006055a5161cbe26f3b7af010ab5f5f2b093`](https://github.com/Nezreka/SoulSync/tree/9e35006055a5161cbe26f3b7af010ab5f5f2b093), plus the current official OpenAPI
specifications for [Sonarr](https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/Sonarr.Api.V3/openapi.json),
[Radarr](https://raw.githubusercontent.com/Radarr/Radarr/develop/src/Radarr.Api.V3/openapi.json),
[Lidarr](https://raw.githubusercontent.com/Lidarr/Lidarr/develop/src/Lidarr.Api.V1/openapi.json), and
[Prowlarr](https://raw.githubusercontent.com/Prowlarr/Prowlarr/develop/src/Prowlarr.Api.V1/openapi.json).

**Evidence labels:** *implemented* means the pinned source contains the behavior;
*covered* means the pinned tree includes focused automated tests; *documented* means
the README claims it; *unverified* means this review did not run it. The focused
pytest selection could not start because this checkout has no installed Flask
dependency (`ModuleNotFoundError: flask`), so this note makes no runtime-success
claims.

## Answer: does SoulSync replace the *arr apps?

**For video acquisition, substantially but incompletely.** SoulSync contains its
own movie/TV wanted state, monitor policies, release parsing and ranking, Prowlarr
search/RSS, downloader hand-off and monitoring, import/rename, upgrades, blocklist,
season-pack handling, and subtitle workflow. That is a functional Radarr + Sonarr +
part of Bazarr-shaped pipeline, rather than a request UI layered over them. Its
own README explicitly positions those features as native video behavior and says
Prowlarr plus a torrent or Usenet client are still required for those sources
([README](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/README.md#L232-L253),
[setup](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/README.md#L420-L431)).

**For music, it can acquire natively or delegate to Lidarr.** Its orchestrator
registers native sources including Soulseek, YouTube, Tidal, Qobuz, HiFi, Deezer,
SoundCloud, and Prowlarr-backed torrent/Usenet, as well as Lidarr
([registry](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/download_plugins/registry.py#L168-L197),
[music source docs](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/README.md#L116-L132)).
That gives it a native Lidarr-shaped workflow for many sources. `LidarrDownloadClient`
is instead an optional delegation path: it looks up an album, ensures the
artist/album exists, asks Lidarr to search/grab, polls Lidarr's queue, then imports
only wanted tracks; that path needs an independently configured Lidarr, its indexers,
and its downloader ([client overview](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/lidarr_download_client.py#L1-L13),
[implementation](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/lidarr_download_client.py#L98-L119)).
No Radarr or Sonarr client appears in the pinned tree. `Bazarr` occurs only in native
subtitle code/comments and schema migration labels, not as an integration.

**It does not replace Prowlarr or download clients.** Its Prowlarr client describes
itself as an indexer aggregator and explicitly says it only searches; SoulSync's
torrent/Usenet plugins add jobs and poll external clients
([Prowlarr boundary](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/prowlarr_client.py#L1-L23)).
The native video configuration exposes only Soulseek, torrent, Usenet, EXT.to, and
an ordered hybrid chain ([configuration](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/video/download_config.py#L1-L47)).
Its adapters support qBittorrent, Transmission, Deluge, aria2, SABnzbd, and NZBGet
([torrent registry](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/torrent_clients/__init__.py),
[Usenet registry](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/usenet_clients/__init__.py#L1-L49)).

This makes SoulSync a new **acquisition authority** for video, while Prowlarr and
download clients remain infrastructure authorities. That is different from replacing
the complete *arr ecosystem.

## Native acquisition coverage

| Capability | SoulSync evidence and depth | Parity conclusion |
| --- | --- | --- |
| Wanted state, monitoring, calendar, requests | Implemented video watchlist/wishlist and monitor policy; documented calendar, iCal, and admin-approved requests. The review found focused video calendar/request tests, but did not execute them. [Monitor policy](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/video/monitor_policy.py), [calendar API](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/api/video/calendar.py), [README](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/README.md#L232-L245) | Covers the core Sonarr/Radarr loop, though not proven production-parity. |
| Search and release identity | Implemented structured `tvsearch`/`movie` plus scene-text query ladder; uses IMDb/TMDB/TVDB IDs, daily and anime/absolute variants, filters/ranks results, and keeps source/indexer identity. [Search](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/video/prowlarr_search.py#L1-L220), [release parser](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/video/release_parse.py), [quality/scope gate](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/video/quality_eval.py#L200-L430) | A serious native alternative to *arr manual/automatic search. |
| RSS and monitoring | Implemented Prowlarr aggregate latest-release sweep, matched against wanted items and routed through the same ranker/queue; its module explains why this complements periodic search. [RSS](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/video/rss_sync.py#L1-L83) | Matches a key Sonarr/Radarr acquisition pattern, provided Prowlarr is present. |
| Downloader hand-off and queue | Implemented direct adapters and a monitor that scopes a completed job to its own content path, handles a season pack separately, then invokes import. [Client monitor](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/video/client_download.py#L1-L120) | Reimplements an important *arr responsibility, but downloader configuration remains external. |
| Quality, custom formats, cutoff, upgrades | Implemented unified video profile, a 16-tier documented ladder, release-name scoring, codec/HDR/audio preferences, minimum seeds and upgrade-until-cutoff. The custom-format module is finite name-pattern scoring rather than proof of Radarr's full extensible custom-format schema. [Profile](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/video/quality_profile.py), [custom formats](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/video/custom_formats.py), [README](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/README.md#L245-L253) | Good product coverage; do not call it configuration/API parity. |
| Import, validation, rename, sidecars, recycle | Implemented parser → sample/wrong-episode checks → ffprobe validation → organization/templated destination, plus video recycle and mass rename modules. [Importer](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/video/importer.py), [library paths](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/video/library_paths.py), [README](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/README.md#L250-L264) | Strong replacement claim, but file-system and real client behavior remain unverified here. |
| Release/source blocklist and retry | Implemented per-release `(uploader, filename)` and uploader-wide blocks, applied in ranking and retry; committed tests cover those paths. [Implementation and tests](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/tests/video/test_video_blocklist.py#L1-L180) | Comparable feature, with a different identity model from *arr. |
| Seasons/anime/daily | Implemented alternate TVDB numbering, daily air-date and anime absolute-number query forms; season-pack module exists and is explicitly protected in the monitor. [Query ladder](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/video/prowlarr_search.py#L112-L220), [pack handling](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/video/client_download.py#L105-L112) | Feature present; broad edge-case parity is unknown. |
| Import lists and history | Implemented import-list module; documented TMDB, IMDb, and Plex Watchlist. Download audit/history is documented and represented by downloads/history modules. [Import lists](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/video/import_lists.py), [README](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/README.md#L240-L253) | Useful subset. No evidence of importing or round-tripping *arr history. |
| Notifications | Implemented Discord, generic webhook, and Telegram for grab/import/upgrade/failure/watchlist events; broader automation docs additionally name Pushbullet, ntfy, and Gotify. [Video notifications](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/video/notifications.py#L1-L177), [automation](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/automation_engine.py#L1720-L2080) | Comparable outgoing-notification intent; individual integrations were not exercised. |
| Subtitles / Bazarr | Native subsystem registers OpenSubtitles, SubDL, SubSource, embedded subtitles, and local Whisper, with wanted queues, provider order, quota/history and per-title overrides in the schema. [Providers](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/video/subtitles/providers/__init__.py#L1-L116), [schema](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/database/video_schema.sql#L903-L990), [contract tests](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/tests/test_subtitle_providers.py#L1-L130) | It aims to replace Bazarr; no Bazarr API integration was found. |

## What the *arr APIs establish—and what they do not

The official specs expose managed resources and operational APIs for calendar, history, wanted
missing/cutoff, manual import, release lookup/grab, queue operations, commands,
root folders, profiles/custom formats, import lists, download clients, notifications,
remote path mappings, and health. See, for example, Sonarr's
[calendar and command endpoints](https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/Sonarr.Api.V3/openapi.json),
[manual import](https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/Sonarr.Api.V3/openapi.json),
[release endpoint](https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/Sonarr.Api.V3/openapi.json),
Radarr's [custom-format](https://raw.githubusercontent.com/Radarr/Radarr/develop/src/Radarr.Api.V3/openapi.json)
and [queue](https://raw.githubusercontent.com/Radarr/Radarr/develop/src/Radarr.Api.V3/openapi.json) resources,
and Lidarr's [quality-profile/download-client](https://raw.githubusercontent.com/Lidarr/Lidarr/develop/src/Lidarr.Api.V1/openapi.json) resources.
Prowlarr itself has first-class indexer management and category APIs
([spec](https://raw.githubusercontent.com/Prowlarr/Prowlarr/develop/src/Prowlarr.Api.V1/openapi.json)).

This demonstrates a mature configuration/lifecycle model; it does **not** by itself
prove that *arr is deeper than SoulSync in every product behavior, or that SoulSync
lacks every corresponding UI/workflow. The source inventory does establish a narrower
claim: SoulSync consumes Prowlarr search and talks directly to selected downloader
APIs, while no Radarr/Sonarr client, configuration import, monitored-state sync, or
*arr-compatible API facade was found. A migration would therefore require translating
or re-entering policies and choosing one system to mutate each library root.

## Playback and media-server integrations

SoulSync treats media servers as library and playlist targets, not a universal
player compatibility layer. Its registry includes Plex, Jellyfin, Navidrome, and a
filesystem-backed SoulSync Library; it explicitly notes that adding Emby/Subsonic
would require a client class ([registry](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/media_server/registry.py#L1-L17),
[registered servers](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/media_server/registry.py#L128-L142)).
The common contract is deliberately narrow—connection plus artist/album inventory—so
playlist, history, metadata, and scan support is server-specific
([contract](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/media_server/contract.py#L1-L17),
[capability gaps](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/media_server/contract.py#L79-L114)).

Implemented/documented integration depth:

* **Plex, Jellyfin, Navidrome:** library matching/scans and music playlist syncing;
  documented playlist Replace/Reconcile/Append, compare/reorder, server-to-server
  export, and per-profile identities. This is a strong music-library companion
  feature, not proof of a common playback-control protocol.
  [Playlist docs](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/README.md#L91-L103)
* **Plex and Jellyfin:** the documented video servers. SoulSync reads/scans their
  libraries and puts acquired media in shared paths; Docker documentation says those
  paths still need to be mounted and indexed by Plex/Jellyfin.
  [Docker path boundary](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/docker-compose.yml#L108-L117)
* **Playlist/listening ecosystem:** documented source and export support covers
  Spotify, Tidal, Qobuz, Deezer, YouTube/YouTube Music, Apple Music links,
  ListenBrainz, Last.fm, Beatport, files, and SoulSync mixes; it also documents
  Last.fm/ListenBrainz scrobbling and history import. These are product claims;
  no live accounts were used in this assessment.
  [Source list](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/README.md#L66-L70),
  [playlist behavior](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/README.md#L91-L103),
  [stats/scrobbling](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/README.md#L188-L194)

## Implications for Lumina

SoulSync's opportunity is not that Lumina should clone all of *arr. Its stronger
lesson is to connect acquisition status, quality rationale, subtitles, and media
server state to the moment a household member is choosing or playing something.
Lumina should keep its current integration-first direction and extend it beyond
request submission.

1. **Extend Lumina's existing playback decision work into the differentiator.**
   Preserve the existing pure playback-decision boundary, then add only the missing
   cross-server capabilities: direct-play/transcode rationale, audio/subtitle choice
   and language preferences, resumable progress/watch state, concurrent-stream limits,
   and remote/local hand-off. Surface them consistently even when the bytes/playback
   session belong to Jellyfin, Plex, or another connected server. SoulSync's server
   contract shows why a narrow “one API for all servers” abstraction is insufficient.

2. **Offer full read integration with *arr, Prowlarr, and downloaders—not just a
   request webhook.** A connected-app panel should surface health, queue,
   grabbed/imported/failed history, wanted missing/cutoff, release choices,
   calendar, profiles/quality/custom-format explanation, monitored state,
   root-folder/path mapping, import lists, subtitles wanted state, and relevant
   notifications. A user should be able to understand “why this is not playable”
   without opening five admin apps.

   **Proposed, separately scoped:** add Bazarr as a connected app after separate
   API/authorization/capability discovery. This research verified SoulSync's native
   subtitle subsystem, not Bazarr's API contract; it does not establish which Bazarr
   state or writes Lumina can safely support.

3. **Allow carefully scoped write-through controls.** Start with approve/request,
   monitor or unmonitor, manual search/grab, retry/clear failure, command launch,
   and, only after Bazarr discovery, subtitle search. Read the authoritative system
   after each write and show the external job ID/history link. Do not mirror every
   configuration object into Lumina's database.

4. **Make authority explicit.** Per library/root, choose `external-arr-authoritative`
   or `lumina-native-acquisition`; never let both auto-grab, import, rename, upgrade,
   recycle, or manage retention in the same root. A connected Prowlarr should remain
   owned by Prowlarr; a connected downloader remains owned by the downloader. This
   prevents duplicate grabs, different quality/cutoff decisions, rename races,
   conflicting retention, and misleading history.

5. **If Lumina later offers native acquisition, start as an isolated vertical
   slice.** Give it separate roots, a durable release identity, an audit log,
   idempotent job ownership, dry-run/migration tooling, quality/cutoff explanation,
   import quarantine, and rollback/recycle handling before claiming replacement.
   Native subtitle acquisition can be an earlier, lower-blast-radius slice because it
   improves playback directly; when Bazarr is connected, choose it or Lumina as the
   subtitle authority only after its independently verified API contract is known.

6. **Use a capability matrix, not a giant generic *arr adapter.** Sonarr/Radarr,
   Lidarr, Prowlarr, Bazarr, and download clients overlap but do not have identical
   concepts. Define Lumina actions by capabilities—`calendar`, `wanted`,
   `release-search-grab`, `queue`, `manual-import`, `subtitle-wanted`,
   `quality-explanation`, `history`, `health`—and have each connector declare which
   are read-only or writable. That provides broad integration without pretending
   every endpoint or semantic is universal.

## Limits

No SoulSync container, external media server, tracker, downloader, subtitle provider,
or third-party account was run. README claims may exceed current behavior. The upstream
commit is a large merge commit, and static source plus unexecuted tests establish
design intent and code presence, not operational reliability, compatibility with all
supported server versions, or security of third-party integrations.
