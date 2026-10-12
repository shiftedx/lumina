# ADR 0018: Media requests are fulfilled through the household's Sonarr and Radarr

- Status: Accepted
- Date: 2026-10-04

(The spec names this ADR 0015; that number was already taken by the recommendations ADR, so it is 0018.)

## Context

Household members want a Jellyseerr-class place to discover movies, shows and anime and ask for them. The household already runs Sonarr and Radarr, which download into the NAS folders Lumina scans as external storage roots. Lumina's own acquisition path (yt-dlp, ADR 0004) is for online media sources and is the wrong tool for release-group media.

## Decision

- A **Media request** is a durable row (`media_requests`) that Lumina hands to the household's Sonarr (shows, anime series) or Radarr (movies, anime films) over their v3 API. Lumina never downloads the media itself and never deletes files when a request is cancelled.
- Sonarr/Radarr are admin-configured LAN endpoints (`arr_servers`), called like the local AI and Jellyfin import endpoints: `local_ai.bounded_json_request`, no redirects, no proxy env vars, bounded time and bytes, content-free errors. API keys and the SMTP password are write-only over the API and nulled in backups.
- Policy is per member per kind (`request_policies`): the member's override, else the household default, else built in (may request, not auto-approved, 10 per 7 days). Admins bypass it.
- One request per title while it is open; a second member becomes a follower, and new seasons merge into the open request.
- A sync loop (60 s, and on demand after a dispatch, ADR 0006 single process) reads the arr queue for progress and its file stats, maps the arr's paths through the server's path mappings, and starts a **scoped import** of that folder through the existing import service (ADR 0017). A request becomes available only when a visible Media title (ADR 0009 predicate) with the matching TMDB or TVDB id exists.
- Anime language is a Sonarr quality profile choice. Lumina can create two custom formats and two profiles idempotently by name.
- Emails go through stdlib `smtplib` on a daemon thread after commit; a failure is logged by type only.

## Consequences

- Requests work only as well as the household's Sonarr/Radarr; an unreachable arr marks a request failed with a reason and an admin retries it. Nothing is lost.
- Availability lags the arr by up to a scan: the vault, not the arr, decides "available".
- The sync loop's rescan throttle and the arr queue page size are per-process ceilings, commented in `services/requests/sync.py` and `arr.py`.

## Alternatives considered

- Importing from or proxying Overseerr/Jellyseerr: rejected by the owner; one fewer service and Lumina's own members and policies.
- Downloading through Lumina's own acquisition outbox: rejected; Sonarr/Radarr already own release selection, upgrades and renaming for the NAS folders.
