# Lumina Docker operator guide

Lumina ships as one container: the FastAPI backend serves the built React app on port 8765. Docker Compose is the supported install path. Read `RELEASE_NOTES.md`, `SECURITY.md` and `LICENSE` before exposing an installation.

## Books formats

Books imports accept EPUB and PDF ebooks and CBZ and CBR comics, up to 256 MiB per file.
Imports create private managed copies under `app-data/books/`; back up that directory as well as the database.
PDF uses raster page navigation rather than reflowable text. CBR reads RAR members without extraction.
Encrypted files, linked archive members, oversized archives, and files exceeding native parser limits are rejected.
The Docker image includes PyMuPDF and libarchive. Source installs need the libarchive shared library
(`brew install libarchive` on macOS, `apt-get install libarchive13t64` on Debian trixie).
Rollback to an older image preserves files and database rows but cannot read PDF/CBR entries; retain the updated image
until those books are no longer needed. No schema migration is required.

## Install

The release Compose file defaults to the pinned multi-platform image `ghcr.io/shiftedx/lumina:0.3.4` for amd64 and arm64. It pulls the published image; it does not build on the host.

```bash
mkdir lumina && cd lumina
curl -fsSLO https://github.com/shiftedx/lumina/releases/download/v0.3.4/docker-compose.yml
curl -fsSLO https://github.com/shiftedx/lumina/releases/download/v0.3.4/SHA256SUMS
grep 'docker-compose.yml$' SHA256SUMS | shasum -a 256 -c -
cat > .env <<EOF
LUMINA_RUNTIME_UID=$(id -u)
LUMINA_RUNTIME_GID=$(id -g)
EOF
docker compose pull lumina
docker compose up -d --no-build lumina
docker compose ps          # lumina must report "healthy"
```

Open <http://127.0.0.1:8765>. Stop with `docker compose down`; logs with `docker compose logs --tail=100 lumina`.

To keep a release-tag checkout for Compose overrides, clone the same release instead: `git clone --branch v0.3.4 --depth 1 https://github.com/shiftedx/lumina lumina`. Create the same `.env`, then run the same `pull` and `up -d --no-build` commands. The optional hardware and HTTPS sections below download their companion release files.

### Build from source

Local developers can opt into a source build without changing the published-image install path:

```bash
printf '\nLUMINA_DOCKER_IMAGE=lumina:local\n' >> .env
docker compose build lumina
docker compose up -d lumina
```

The image is multi-stage (the frontend is built in a Node stage; only `dist/`, the Node binary yt-dlp needs for YouTube, ffmpeg and the hash-locked Python runtime reach the final image). The container runs read-only with all capabilities dropped except those the entrypoint needs to `chown` the data directory, then drops to the numeric `LUMINA_RUNTIME_UID:LUMINA_RUNTIME_GID` account (never 0) with `setpriv`, clearing every supplementary group except the optional host render group (`LUMINA_RENDER_GID`, see "Hardware transcoding"). The image healthcheck polls `GET /api/health`.

ffmpeg/ffprobe are [jellyfin-ffmpeg](https://github.com/jellyfin/jellyfin-ffmpeg) 8.1.2-5 (GPL; QSV/VAAPI, OpenCL tone-mapping and chromaprint), installed from the release's `trixie_amd64`/`trixie_arm64` `.deb`. Each is pinned by sha256 in the `Dockerfile` (`ADD --checksum`) and symlinked from `/usr/lib/jellyfin-ffmpeg/` into `/usr/local/bin/`. On amd64 the image also installs Intel's OpenCL runtime (`intel-opencl-icd` 26.31.39395.13, `intel-igc-core-2`/`intel-igc-opencl-2` 2.40.13, `libigdgmm12` 22.10.0) from Intel's GitHub releases, again pinned by sha256. It is not packaged for Debian trixie, and the versions match Jellyfin's own trixie image. The media toolchain adds ~150 MB over the previous static build. Both ffmpeg binaries are GPL-licensed; [`CREDITS.md`](../CREDITS.md) links their source and lists every bundled component. To upgrade, read the new digests with `gh api repos/jellyfin/jellyfin-ffmpeg/releases/latest --jq '.assets[] | select(.name | test("trixie_(amd64|arm64).deb$")) | .name + " " + .digest'` (Intel: `gh api repos/intel/compute-runtime/releases/tags/<ver>` and `repos/intel/intel-graphics-compiler/releases/tags/v<ver>`), update the `ADD` lines, and rebuild.

## Configure

All settings are `LUMINA_*` environment variables (full list: `backend/app/config.py`). `docker-compose.yml` passes these from `.env` or the shell:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LUMINA_RUNTIME_UID` / `_GID` | `1000` | Host account that owns `./app-data` and managed media mounts. |
| `LUMINA_PLAYBACK_TEMP_DIR` | `/playback` in Docker; unset natively | Temporary local-playback storage. The base Compose file fixes this to its dedicated tmpfs. Native installs may set another explicit path. |
| `LUMINA_PLAYBACK_TMPFS_SIZE` | `1g` | Maximum RAM-backed space for local playback segments and Roku derivatives. It counts toward `LUMINA_MEM_LIMIT`. |
| `LUMINA_STORAGE_MOUNT_PARENTS` | `/media` | Comma-separated container paths under which admins may register storage roots. |
| `LUMINA_AI_BASE_URL` / `_AI_MODEL` | empty (off) | OpenAI-compatible chat endpoint for summaries, moments and idea graphs, e.g. `http://192.0.2.10:8080/v1`. |
| `LUMINA_ASR_BASE_URL` / `_ASR_MODEL` | empty (off) | OpenAI-compatible speech-to-text endpoint for transcripts. |
| `LUMINA_TMDB_API_KEY` | empty (off) | TMDB v3 API key or v4 read token for movie and TV metadata and artwork. An admin can change or clear it under **Manage Server → Connections → Metadata** (write-only). |
| `LUMINA_ARTWORK_PASS` | `on` | `off` stops the background pass that prepares small copies of artwork; they are then made only when first requested. |
| `LUMINA_MUSIC_METADATA_ENABLED` | `true` | Background MusicBrainz identity, release, artwork and lyric enrichment. Add this variable to Compose's `environment:` block to change it. |
| `LUMINA_MUSIC_YOUTUBE_DISCOVERY` | `true` | Cached public performance discovery for owned music. Saved local performances remain available when discovery is off. |
| `LUMINA_PEOPLE_DIR` | unset | Container path of a read-only folder of cast photos in the `metadata/People` layout, such as a copy of Jellyfin's. Cast photos for titles whose NFO files name them. `LUMINA_JELLYFIN_PEOPLE_DIR`, the name before 2.11, is still read. |
| `LUMINA_JELLYFIN_TRACE` | `0` | `1` logs each Jellyfin request's method, route template and query parameter names (never values or headers). Use it only while capturing client traffic. |
| `LUMINA_LAN_HTTP` | `false` | `true` serves plain http on a private LAN IP without a proxy. Set it only through the override in "LAN HTTP mode". |
| `TZ` | `UTC` | Server time zone, e.g. `America/Chicago`. Library scans set to **Nightly at** use it. See "Automatic scans and folder watching". |
| `LUMINA_DOCKER_IMAGE` | `ghcr.io/shiftedx/lumina:0.3.4` | Pinned published image. Set `lumina:local` only when intentionally building the checked-out source. |

AI and ASR values are only defaults: an admin can change or clear them under **Manage Server → AI features** (the API key is write-only there). Tunables such as `LUMINA_QUARANTINE_RETENTION_HOURS`, `LUMINA_REMOTE_STREAM_*`, `LUMINA_RELAY_*` and `LUMINA_LIVE_RECORDING_*` keep safe defaults; add them to the `environment:` block only when you need to.

## First owner and members

A fresh install has no accounts and no default password. Open the loopback URL (or, in LAN HTTP mode, the LAN URL right after the first start) and create the first administrator on the setup screen (`POST /api/bootstrap/admin`; it refuses once any account exists). Passwords need 12–128 characters, three of lowercase/uppercase/number/symbol, and must not contain the username.

Add people under **Settings → Members**: issue an invitation link (shown once; revocable) or a password-reset link. Sessions are opaque server-side records in the database, so restarts keep people signed in.

Lumina stores no provider credentials. yt-dlp runs with a closed allowlist of public options (no cookies, credentials, browser auth or exec), so sources that need sign-in are unavailable. Acquisition accepts public HTTP(S) destinations only; private and LAN addresses are refused.

From 1.9.0 the recommender also runs background yt-dlp searches and channel listings for each member who has watched or followed something. The whole household stays within 40 calls an hour and 300 a day, one at a time, and no search text is stored or logged. An admin can turn it off under **Manage Server → AI features → Personalised recommendations**, and a member can erase their recommendation history under **Settings → You**.

## Settings navigation

Open the header navigation button to choose **My Settings** or **Manage Server**.
Navigation opens in a modal and closes after choosing a destination, leaving the full
page width available for media. On phones, **More** opens the same menu.

**My Settings** (`/settings`) contains six categories for your profile: Profile & security,
Playback, Display, Discovery & streaming, Downloads, and Privacy & connected apps.
**Manage Server** (`/manage`) is available to vault owners and contains Overview & activity,
Household, Library & storage, Connections, AI features, and Maintenance.

Each category has focused subpages. Request approval and limits are under Household →
Request rules; Sonarr, Radarr, Integrations and Email are separate pages under Connections. AI features,
local models, assistant setup and speech setup each have their own page. Server addresses
are under Connections → Addresses; backups, tasks, diagnostics and video conversion are
under Maintenance. Advanced tuning expands within the relevant page, without a global switch.

Assistant setup loads available models automatically from the endpoint’s `/models` catalog
when you open the page or edit its address or key. Choose a model from the dropdown, then
save. Model discovery does not save configuration. Refresh models retries the lookup;
manual entry remains available when the catalog is unavailable. A saved key is used only
for its saved endpoint, never forwarded to a different draft address.

Search covers every setting you can access and opens its page, including advanced options.

### Arr observations

Configure Sonarr and Radarr under **Manage Server → Connections**, using the existing
write-only API keys and path mappings. Enabled integrations are polled independently
of **Household → Request rules → Allow requests**. Turning Requests off stops new
member requests; it does not stop observation. Disabling an individual integration
stops its observation and request dispatch.

**Connections → Integrations** shows upstream-added inventory and paginated queue,
history, health, calendar, missing and quality-cutoff observations. A complete poll
replaces the saved snapshot; an outage or incomplete reply retains the previous
snapshot with a stale label. Refresh performs another read without issuing an
acquisition command. Concurrent refreshes for the same server are rejected. A
timeout in the browser may leave the read running; review the next snapshot before
trying again. Failed reads never establish a new successful observation.

Each paginated collection is limited to 100,000 records and 64 MiB across its
pages, with a 16 MiB limit per response. Exceeding a limit or detecting changing
pagination fails the poll and retains the stale snapshot; records are never
silently truncated. A later complete poll reconciles missed changes.

An unmapped folder needs a matching remote/local path mapping. A mapped folder
outside an enabled external storage root needs that root configured. A root awaiting
its first import needs an owner's initial import under **Library & storage → Imports**;
subsequent observations may request the existing throttled scoped import. An upstream
file or title does not make media playable: the normal import must register a file
visible to the member. Upstream-only inventory is owner-only; member title explanations
contain no upstream paths, identifiers, keys or diagnostic bodies.

The automated connector fixtures exercise Sonarr/Radarr v3 contracts. Installed
versions and real-device/provider behavior require separate qualification; discovery
of other connectors does not enable their controls.

### Acquisition controls and supporting services

An owner can open **Manage** on a Sonarr/Radarr inventory item to change monitoring
(including a Sonarr season), choose an existing quality profile, search releases,
explicitly grab an approved release, or request search/rescan recovery. Each operation
uses a fresh revision and a durable intent ID. Reloading or retrying an uncertain
operation does not resend it. **Reconcile** only reads upstream evidence; an outcome
that cannot be proved remains uncertain and blocks competing work. Resolve that work
upstream before changing connections or rolling back. Root moves, bulk rename,
file deletion, transfer cancellation and retention are outside these controls.

Under **Connections → Integrations → Infrastructure connectors**, add Lidarr,
Prowlarr, qBittorrent or NZBGet. Saving disables observation until **Test connection**
completes the minimal read contract; enabled connections are then polled every minute.
Credentials are write-only. Changing the endpoint requires new credentials; removing
credentials disables the connector. Backups omit these credentials, so re-enter and
test them after restoring. A failed refresh keeps the previous snapshot stale.

Lidarr uses `/api/v1` and an API key. Choose a root folder, quality profile and
metadata profile for new artists, plus remote/local path mappings. Members request
albums by MusicBrainz artist and release-group identity; every acquisition requires
owner approval. Edition/release IDs are separate from release-group IDs. A mapped
artist folder with upstream files may trigger the existing throttled scoped import,
after the root's first complete owner import. A request becomes playable only when
its release group has visible linked tracks in an enabled root. Uncertain add/search
outcomes are retained and never automatically dispatched again.

Prowlarr observation is read-only; it retains indexer configuration and credentials.
qBittorrent uses WebUI cookie login and a complete `sync/maindata` snapshot; NZBGet uses
Basic-authenticated JSON-RPC reads. To correlate transfers, bind an arr server and
its exact configured download-client name, then require matching native download IDs.
Unmatched and ambiguous jobs stay labeled and owner-only. These views expose reduced
state/progress diagnostics and an upstream administration link, without transfer,
seeding, cancellation or retention controls. File-list browsing is not implemented.

The [connector matrix](../docs/research/arr-connector-contracts-2026-10-17.md)
separates source-contract evidence from real installed-version qualification. Bazarr
is conditional: no instance/authentication/identity mapping has been qualified, so
subtitle search/download controls are absent. Existing local subtitles remain usable.

### Listening continuity and defaults

The member's queue, cursor, repeat and shuffle settings restore across browsers without
starting audio. Older local queues migrate by IDs and are revalidated. Offline edits
retain their base revision; reconnect can preserve an intentional rewind when that
revision still matches. Concurrent edits offer **Use server** or **Keep this device**;
another race returns another conflict. Hidden/removed entries are pruned safely.
Acquisition entries remain distinct from playable tracks, with an explicit Skip/Wait
policy. Wait stops playback and prefetch before an unresolved entry. In the queue,
**Refresh requested albums** checks for imported visible tracks; choose Play after
they appear. Restoring, refreshing or reconnecting does not start audio automatically.

For a constrained connection, **My Settings → Playback → Streaming quality** can
lower the starting ceiling while a quality chosen in the player still takes priority.
On an untouched browser start, Lumina may use a finite Network Information downlink
hint as a transient conservative bitrate cap. Missing, invalid, and Chromium's
saturated 10 Mbps-or-higher value are treated as unknown. The hint is never saved,
does not alter audio, subtitles, or resume position, and never creates an adaptive
bitrate ladder; saved and explicit member quality choices remain authoritative.
In the [synthetic 2.5 Mbps experiment](../docs/research/local-conversion-network-2026-10-17.md),
480p software conversion substantially reduced stalls compared with an 8 Mbps
1080p remux. That result is specific to the measured source/link; local conversion
keeps the existing resource limits and does not launch an adaptive rendition ladder.

Browser decoded-audio crossfade defaults to zero and supports up to 12 seconds.
Large-file native streaming and remote outputs keep their own capabilities; crossfade
is unavailable there. The sleep timer pauses browser output and does not follow a
speaker. Sonos transfer retains the recoverable browser queue until the speaker
acknowledges the admitted queue, position and exact index mapping. Return-to-browser
acknowledges browser playback before stopping the speaker. Output tickets are still
revoked by account/policy changes; a saved descriptor never grants playback access.

Under **My Settings → Playback → Subtitles**, choose ordered audio/subtitle languages,
subtitle enabled/forced/SDH defaults and caption appearance. Browser playback and
compatible Jellyfin playback resolve defaults before making their playback decisions;
explicit client or per-play audio choices and subtitle Off take priority.

**My Settings → Playback → Listening history export** optionally connects a member's
ListenBrainz user token or Last.fm application API key/secret followed by Last.fm
account approval. These are separate from media-player credentials. Export uses
structured track tags/manual metadata, elapsed browser listening, tracks longer than
30 seconds and half the track or four minutes heard. Seeking and previews do not
count. Offline replay keeps the same logical play ID. Outcomes are sent, ignored,
pending, failed, revoked or uncertain; **Check provider history** reconciles uncertain
outcomes without sending them again. Disconnecting stops future pending exports.
Sonos and Chromecast listening export is not advertised by the browser-only tracker.

### Chromecast preview

The video watch page offers a first-party Web Sender based on Google's current
[Web Sender integration](https://developers.google.com/cast/docs/web_sender/integrate)
and Default Media Receiver. Open Lumina over HTTPS in a Cast-supported browser and
configure a public/local server address reachable by the receiver. **Enable Chromecast**
loads Google's SDK in that tab; then select a receiver. Browser audio pauses only
after the receiver acknowledges the media and position. Pause, seek, resume and
**Return playback here** retain a recoverable source on failure.

The initial profile is direct MP4 H.264, 8-bit SDR up to 1080p with stereo AAC and
optional selected text/VTT subtitles. Non-default audio, image subtitles and files
needing conversion stay in browser playback; arbitrary remote media URLs are never
sent to the receiver. Each receiver URL is an item-scoped, revocable 15-minute ticket,
renewed every minute by the signed-in sender. Keep the page open; after disconnection
the ticket expires without renewal. Each Range/VTT request rechecks current access
and playback policy. Logout/member changes revoke receiver tickets. Media already
buffered on a disconnected receiver may finish playing.

The household has no Chromecast device, so hardware discovery/start/control/subtitle
interoperability is explicitly unqualified. Contract fixtures and browser SDK mocks
establish implementation behavior, not physical-device compatibility. Apple TV's
existing Infuse/Jellyfin-compatible route remains available.

### Household resources

**Manage Server → Household → Resources** sets household defaults. Open a member's
**Resources** tab to override an allowance; clear a field to inherit it again.
Zero means unlimited for that member, within household capacity. Owners obey the
same resource ceilings. Rates shown in Mbps use decimal megabits (1 Mbps =
1,000,000 bits/second); the API stores integer bits/second.

The remote upload budget starts **Unconfigured**, including after upgrade. Existing
playback compatibility is retained until an owner supplies a budget; Lumina cannot
claim upload protection while it is unconfigured. Set it below your measured upload
capacity to leave room for gaming and other household traffic. Enabling it offers
8 Mbps per remote stream, 10 Mbps across a member's remote connections, two playback
sessions and one video encode per member. Existing household video and model worker
capacities remain hard ceilings. Web and Jellyfin clients share allowances across
their authenticated devices. A compatible high-bitrate original is converted when
needed; selecting Original cannot bypass the ceiling. Unknown-bitrate audio or media
that cannot be converted receives a clear refusal instead of an unrestricted original.

Remote provider streams and live relays copy upstream media and cannot guarantee a
suitable rendition. Capped remote playback refuses these paths: add the media to
the library for bandwidth-aware playback. Library audio compatibility formats are
checked against the actual outgoing file. Member file downloads share the upload
cap but do not consume playback slots.

LAN exemption is explicit: enter the CIDRs you trust, for example `192.168.1.0/24`
and `fd12::/64`. Empty means every connection receives remote policy. Direct peers
must match an exempted network without forwarding ambiguity. Behind the supported
single proxy, only the canonical client address supplied by that trusted proxy may
qualify. Configure the proxy to **replace** `X-Forwarded-For` with the real client,
strip client-supplied `Forwarded`/`CF-Connecting-IP`, and keep Uvicorn's
`--no-proxy-headers`. Private proxy peers, Host headers, proxy chains, tunnel visitor
headers and ambiguous traffic never establish locality. VPN clients are remote unless
their verified network is explicitly exempted. Do not exempt a proxy's bridge network.

Actual authenticated remote response bodies share process-wide household and member
token buckets. The envelope is `bytes <= bits_per_second / 8 * elapsed_seconds + 32768`,
including the initial 32 KiB burst. Opening parallel/range/segment requests does not
renew that allowance. Pacing uses 16 KiB or smaller chunks and cancellable async waits;
HEAD, conditional responses and byte ranges retain their normal semantics. Protected
responses disable proxy buffering and caching (`X-Accel-Buffering: no`,
`Cache-Control: private, no-store`). Disable caching and response buffering for
authenticated media at your proxy/CDN too; a proxy that serves bytes independently
cannot enforce Lumina's delivery budget. Caddy's `flush_interval -1` is supported.
Application-byte accounting excludes TLS/IP overhead. Lumina controls its own traffic;
router SQM is still useful for latency across the whole household.

Upload/member changes reach active transfers within the 250 ms policy refresh/wait
interval without replenishing bursts. Existing playback allocations stay stable;
new starts must fit the remaining capacity. Stop, failed startup, replacement and
cancellation release slots; paused/abandoned streams expire after ten minutes without
activity. Restart removes ephemeral playback sessions and reservations together.

Work defaults are one running acquisition plus 25 waiting acquisitions and one running
AI job plus ten waiting AI jobs per member. Downloads and live recordings share the
acquisition allowance; recording and download workers keep their existing global
capacities. The legacy download setting `max_active_jobs_per_user` remains an additional
queued-plus-running ceiling for DownloadJobs, distinct from the new waiting-only cap.
Summary, recap, idea graph, ASR, subtitle generation/translation/sync and media-analysis
jobs share AI member accounting, with existing model/process concurrency still in force.
Dispatch rotates between eligible members, FIFO within each member; running work is
not preempted. Lower limits retain queued and running work and prevent further starts
above the new allowance. Members can cancel their own AI jobs from My Settings;
owners retain Tasks recovery controls at capacity. Synchronous collection drafting
refuses immediately if its member/model slot is busy. Owner-attributed automations
use that owner's allowance. Unattributed caption/embedding/metadata maintenance keeps
its existing bounded lane and background yielding; cached results create no new slot.

`mise exec -- backend/.venv/bin/python scripts/qualify_household_resources.py` runs an
isolated real Caddy TLS proxy, Lumina, synthetic media and concurrent downloads. It
records client delivery and server-send samples, checks the envelope and verifies
forced conversion. It requires Caddy, OpenSSL and FFmpeg and uses temporary data only.
For an existing deployment, `scripts/measure_bandwidth.py` measures an authenticated
media URL through its configured proxy. Never paste real credentials into reports.
Existing `/settings/<section>` and `/admin/<section>` bookmarks still work; owner sections
open in Manage Server. Subpage bookmarks use `?page=`, for example `/manage/requests?page=email`.
Forms with Save buttons retain their save/discard behavior and warn before leaving unsaved edits.

## New-member setup

An invited member creates their own account, then enters the visual welcome. **Personalize my Home** leads through interests and optional channel follows; **Explore without setup** goes straight to Home. A member can return to interests from channel discovery, search for creators, or paste a channel address. Following a channel does not download its media. Completion and skipping are saved per member; an unfinished account returns to the welcome on its next visit.

## Film and episode playback

Local films and episodes have a dedicated playback layout: the player comes first, followed by title, synopsis, playback actions, and continuation. Episodes show their series and episode order with autoplay controls; films show their poster and collection context. Playback quality, subtitles, resume, theater mode, fullscreen, and picture-in-picture retain their existing controls.

## Outgoing email

Configure SMTP under **Manage Server → Connections → Email**, then use **Send test email** to check delivery. Invitations, request updates, security notices, and the test message share the app’s media-vault styling. Invitations show their expiry and shared libraries; request messages show the title, status, and the relevant action; security notices show the affected account and pause duration.

Email artwork uses versioned public assets from the configured public address. Keep that address reachable over HTTPS so remote mail clients can load the logo, illustrations, and fonts. Request posters use their existing HTTPS artwork URLs. Status, account details, links, and actions remain live text when images are blocked, and every message includes a plain-text alternative. Clients without web fonts use system fonts; classic Outlook uses a VML button fallback. Preview the variants without sending mail with `mise exec -- backend/.venv/bin/python scripts/email_preview.py /tmp/lumina-email-preview.html`.

## Storage

- `./app-data` holds the database, backups, caches and the built-in Library (`app-data/library/<member-id>/`). Keep it app-exclusive: no household-writable share.
- Local playback segments and Roku derivatives use `/playback/playback-sessions` on the dedicated `/playback` tmpfs. Its `LUMINA_PLAYBACK_TMPFS_SIZE` limit defaults to 1 GiB, counts toward the container's 5 GiB memory limit, and has no swap allowance because `memswap_limit` equals `mem_limit`. A full tmpfs refuses new playback conversion instead of falling back to `./app-data`. Native installs can set `LUMINA_PLAYBACK_TEMP_DIR` to an explicit temporary mount; Lumina cleans only its `playback-sessions` child. When unset, playback sessions remain under the data directory for compatibility. Provision the mount as RAM-backed storage on native hosts; choosing a path alone does not make it RAM-backed.
- Seekable Jellyfin/Roku conversions retain about 60 seconds of completed history and pause the producer about 120 seconds ahead, checked every second even while the player is paused. Seeking into removed history regenerates it. Growing web playlists retain their advertised history, so size the tmpfs for long web conversions and concurrent viewers; a full mount can interrupt a running conversion. The disk headroom setting applies to persistent playback storage, not to the smaller RAM mount.
- Never open the live `app.db` from another process while the container runs (on macOS bind mounts a second writer can poison the running app with `disk I/O error`). Use the API or stop the container first.
- Extra media: bind-mount it under a mount parent, then register it under **Settings → Library & storage**:

  The first-owner walkthrough lists configured mount parents and connects and scans folders immediately after account creation. Setup later keeps the same controls available under **Manage Server → Library & storage**.

  ```yaml
  volumes:
    - ./app-data:/app/backend/.data
    - /srv/plex/movies:/media/movies:ro     # external: read-only, never written, moved or deleted
    - /srv/lumina-downloads:/media/downloads # managed: Lumina may write here
  ```

  Mount `external` roots `:ro` so the OS enforces it. Roots outside the mount parents, overlapping another root, inside `app-data`, or reached through a symlink are refused. An external root is recognised by its filesystem, or for a network share by its server and export, so a remount or reboot keeps it available. A missing or swapped mount shows as `offline` / `identity_mismatch`, never as an empty library.
- **Settings → Library & storage → Imports** indexes media already on an external root into the Library without copying or modifying it. Adding an external root offers **Import now**; imported media is shared with the household unless you choose **Private**.
  After an import, Lumina fills codec and stream facts for every new or changed file first. The lower-priority full-file loudness pass continues one file at a time, pauses for video transcodes and survives restarts, so loudness normalization may become available later without delaying codec-ready playback.
- **Music:** bind-mount the music folder read-only under a mount parent (`- /srv/music:/media/music:ro`), register it as an **external** root and import it (shared with the household). Lumina reads the `Artist/Album/01 Track.flac` layout, with disc folders such as `CD1`; embedded tags win over folder names, a `cover.jpg` or `folder.jpg` in the album folder wins over embedded art, and a compilation is one "Various Artists" album. Tags are read once per file and again only when the file changes. `theme.mp3` files and `theme-music` folders stay out of the Library. The first import reads every track's tags and art and does not pause for video transcodes, so run it off-hours on a large library over a network share. If a compose override replaces `volumes:` with `!override`, add this mount (and the cast photos mount below) there.
- Deleting a Library file moves managed media to `.lumina-quarantine/` on its root; it is purged after `LUMINA_QUARANTINE_RETENTION_HOURS` (168). External files are never touched.
- `app-data/artwork-cache/` and `app-data/stream-cache/` are disposable, bounded caches (20 GiB global stream-cache ceiling by default).
- `app-data/metadata-art/` keeps TMDB posters, backdrops and photos once fetched. Past 4 GiB, Lumina deletes the oldest images no title or person uses any more, about once an hour; images in use are always kept, and a deleted one is fetched again when next asked for. Animated WebP uploads are refused ("Animated images are not supported"): upload a still JPEG, PNG or WebP.
- `app-data/artwork-cache/renditions/` holds artwork renditions: small copies of posters, backdrops, logos and episode stills. Lumina prepares them in the background with one low-priority ffmpeg at a time, and pauses while any video is being converted. The web app and Jellyfin apps load them instead of the originals. The folder is capped at 5 GiB, with the least recently used copies removed first; a removed copy is prepared again in the background. It is safe to delete, and missing copies are made again on demand. Progress shows under **Settings → Library & storage → Artwork preparation**. `app-data/art-secret` signs the artwork URLs. To rotate it, delete the file **and** restart the container (the running app keeps the key in memory); this only changes every artwork URL. Set `LUMINA_ARTWORK_PASS=off` to stop the background pass entirely (renditions are still made on demand).
- YouTube channel pages are cached in memory only (at most 256 headers, 256 tab pages and 512 resolved handle addresses); a restart empties the cache and needs no volume or setting.

## Backups and restore

**Settings → Backups → Back up now** writes a consistent SQLite copy plus a manifest to `app-data/backups/` while Lumina runs; a daily automatic backup keeps the newest 7, and the daily pass also keeps only the newest 3 `pre-upgrade` backups. **Verify** re-checks the checksum, integrity and schema version. Backups contain password/session hashes (never the saved AI API key; re-enter it in Manage Server → AI features after a restore): they are admin-only and `0600`, and **Download** asks for your password again. Copy `app-data/backups/` off the host, together with `app-data/totp-key`: backups hold the database only, and without that key file every two-step verification authenticator in them is unreadable (see Two-step verification below).

Media is not in these backups. Neither are on-device models (`app-data/models/`); they re-download from Settings if lost. Back up `app-data/library/` and managed roots with your usual file tooling, and mount every root at the same container path before restoring.

Restore is offline:

```bash
docker compose down
docker compose run --rm --no-deps lumina python -m app.restore /app/backend/.data/backups/lumina-<timestamp>-manual.db
docker compose up -d
```

The command refuses while a server holds the data directory, verifies the backup against its manifest, revokes every session and unused invitation/reset link, and keeps the previous database as `app.db.pre-restore-<timestamp>` (rename it back to roll back).

## Upgrades

To update, set or replace the image line in `.env` with the exact release you want, then pull and restart only Lumina. These commands leave `app-data/` and every media mount in place:

Set this line in `.env` (replace an existing `LUMINA_DOCKER_IMAGE` line):

```dotenv
LUMINA_DOCKER_IMAGE=ghcr.io/shiftedx/lumina:0.3.4
```

```bash
docker compose pull lumina
docker compose up -d --no-build lumina
```

For a checkout-based install, first update the Compose file with `git fetch origin tag v0.3.4 && git checkout --detach v0.3.4`. When a release raises the schema version, Lumina first writes an automatic `pre-upgrade` backup (listed under Settings → Backups), then applies its additive upgrade steps in one transaction. It refuses to open a database from a newer release; to roll back, see "Rollback after a schema upgrade" below (the pre-upgrade backup is the last resort).

### Rollback after a schema upgrade

Schema steps 6 to 18 only add tables, columns and indexes that older releases ignore, so rolling back keeps everything written since the upgrade, except step 8's album and artist titles (below). Stamp the database with the schema version of the release you roll back to:

| Rolling back to | Stamp |
| --- | --- |
| Before acquisition-aware music queues (schema 28) | `PRAGMA user_version = 28` (keep the additive `queue_entries`, `entry_index` and `unavailable_policy` columns; older releases use only the resolved playable queue) |
| Before Chromecast media tickets (schema 27) | `DELETE FROM media_cast_tickets; PRAGMA user_version = 27` (revoke receiver access; keep the additive table) |
| Before listening history exports (schema 26) | `PRAGMA user_version = 26` (keep `listening_export_connections` and `listening_play_events`; preserve uncertain delivery records to prevent duplicate exports) |
| Before infrastructure connectors and music acquisitions (schema 25) | `PRAGMA user_version = 25` (keep `service_connectors` and `music_acquisitions`; reconcile uncertain Lidarr work before issuing another acquisition in an older release) |
| Before shared music listening sessions (schema 24) | `PRAGMA user_version = 24` (keep `music_listening_sessions`; older releases ignore shared queues and continue per-item playback progress and browser-local restoration) |
| Before arr operation history (schema 23) | `PRAGMA user_version = 23` (keep `arr_operations` as the acquisition audit, especially uncertain outcomes; verify unresolved work upstream before submitting another command through an older release) |
| Before independent arr observations (schema 22) | `PRAGMA user_version = 22` (keep `arr_observation_states`, `arr_observed_items` and `arr_observed_records`; older releases ignore these disposable snapshots. Requests and saved connections remain.) |
| Before the dedicated music experience (schema 17, step 18) | `PRAGMA user_version = 17` (the older release ignores `music_lookups`, `music_track_overrides`, `music_edit_batches`, `music_cast_tickets`, `music_file_writebacks`, and `music_playlists`. Track overrides and new navigation are unavailable, and its scans may regroup tracks from their source tags. Provider portraits need this release to render. Keep `music-file-backups` and all six tables; upgrading again restores their controls. Explicit audio-file writeback remains in the files across rollback; restore chosen files before rolling back if desired.) |
| Before first-owner media onboarding (schema 19) | `PRAGMA user_version = 19` (`users.vault_setup_pending` remains and is ignored by the older release) |
| Before household resource budgets (schema 21) | `ALTER TABLE app_settings DROP COLUMN resource_policy; ALTER TABLE users DROP COLUMN resource_overrides; PRAGMA user_version = 21` (removes resource settings and overrides; existing media and work remain) |
| Before Books format bundling (schema 20) | `UPDATE book_items SET bundle_parent_id = NULL, deleted_at = NULL; DROP INDEX IF EXISTS ix_book_items_bundle_parent_id; DROP INDEX IF EXISTS ix_book_items_deleted_at; ALTER TABLE book_items DROP COLUMN filename; ALTER TABLE book_items DROP COLUMN bundle_parent_id; ALTER TABLE book_items DROP COLUMN deleted_at; PRAGMA user_version = 20` (every `book_items` row, file, state and shelf membership remains; this only unlinks bundles and makes trashed rows visible to the older Books release) |
| Before Books (schema 18) | `DROP TABLE book_shelf_memberships; DROP TABLE book_shelves; DROP TABLE book_states; DROP TABLE book_items; PRAGMA user_version = 18` (uploaded files under `app-data/books/` remain on disk) |
| Before the local address (schema 16, step 17) | `PRAGMA user_version = 16` (step 17 only adds `app_settings.local_address`; the older release ignores it, so the local name is refused as an untrusted host until you upgrade again) |
| Before the people editor (schema 15, step 16) | `PRAGMA user_version = 15` (step 16 only adds `person_overrides`, which the older release ignores: people renames and photos show their source names and photos until you upgrade again. Its scans also put moved episodes back in their folder's season, and its TMDB refresh reads every series in aired order) |
| 2.9.0 (schema 14, before the dedicated two-step key, step 15) | `PRAGMA user_version = 14` (step 15 changes no table; at startup it re-encrypts each authenticator secret under `app-data/totp-key`, which 2.9.0 cannot read, so after rolling back **authenticator codes fail** for members who had two-step verification on: they sign in with a recovery code, or an owner resets them. Upgrading again reads them as before; keep `app-data/totp-key`) |
| 2.8.1 (schema 13, before two-step verification, step 14) | `DELETE FROM device_tokens WHERE kind = 'app_password'`, then `PRAGMA user_version = 13` (step 14 only adds the two-step columns; 2.8.1 ignores them, so **two-step verification is off** for everyone until you upgrade again, when it is back as it was. The delete removes app passwords, which 2.8.1 cannot list; apps that already signed in with one stay signed in) |
| 2.7.2 (schema 12, before member access, step 13) | `PRAGMA user_version = 12` (step 13 only adds `member_access`, `screen_time`, title ratings, invite and public-address columns; an older release ignores them, so every member is unrestricted again) |
| 2.5.1 (schema 11, before Requests, step 12) | `PRAGMA user_version = 11` (step 12 only adds the Requests tables and columns; Sonarr/Radarr keys and the SMTP password stay in them) |
| 2.1.0 (schema 10, before the admin Activity page, step 11) | `DROP TABLE playback_history`, then `PRAGMA user_version = 10` (see "2.2.0 rollback" below) |
| 2.0.1 (schema 9, before library automation and the metadata editor, step 10) | the three statements below, then `PRAGMA user_version = 9` |
| 1.8.0 (schema 8, before recommendations, step 9) | `PRAGMA user_version=8` |
| 1.5.1 (schema 7, before the library gallery, step 8) | the album and artist removal below, which also stamps 7 |
| a schema-6 release (before "Recently added" by file arrival, step 7) | `PRAGMA user_version=6` |
| 1.3.1 (schema 5, before the gallery, step 6) | `PRAGMA user_version=5` |

```bash
docker compose down
sqlite3 app-data/app.db 'PRAGMA user_version=6'
```

**2.2.0 rollback (to 2.1.0, schema 10).** Step 11 only adds the `playback_history` table (finished Activity sessions, kept 90 days). 2.1.0 ignores it, so you may leave it; to remove it, run the two statements and start the 2.1.0 image. Upgrading again recreates the table empty.

```bash
docker compose down
sqlite3 app-data/app.db 'DROP TABLE IF EXISTS playback_history; PRAGMA user_version = 10;'
```

Rolling back to 2.0.1 (from 2.1.0, step 10) needs one extra step. Scans that covered only some folders (from the watcher or Scan now on a folder) mean nothing to 2.0.1: resuming or confirming one there would mark every file outside its folders missing. Delete them before stamping the version:

```bash
docker compose down
sqlite3 app-data/app.db "
DELETE FROM import_entries WHERE run_id IN (SELECT id FROM import_runs WHERE scope IS NOT NULL);
DELETE FROM import_runs WHERE scope IS NOT NULL;
PRAGMA user_version = 9;"
```

While on 2.0.1, edited fields keep their edits (2.0.1 treats them as your own values and does not overwrite them). Item locks, extra backdrops and uploaded artwork are ignored: an uploaded image shows the typographic card, and 2.0.1's Jellyfin API serves none. Artwork you removed in the editor shows as missing art on 2.0.1 (it has no notion of a removed image) until you upgrade again. Schedules and folder watching stop. A Watch-only folder (no schedule) is not re-scanned after an outage by itself: run a full scan once the share is back. Upgrading again re-applies step 10 safely, and every setting, lock, kept source value, edit history row and upload reappears.

Rolling back to 1.8.0 keeps everything the recommender wrote, and 1.8.0 ignores it. While rolled back, Show fewer has no effect (1.8.0 reads only the item and channel hides), no recommendation events are recorded, and watch depth stops updating. Upgrading again re-applies step 9, which never overwrites counts 1.9.0 already wrote.

Releases before step 8 cannot read album and artist titles, so rolling back from 1.6.0 or later removes them first. Their tracks keep their progress, and the next Music import recreates the albums and artists (with new ids). Favourites on albums are lost; everything else survives. To go further back, run this and then stamp the older version:

```bash
docker compose down
sqlite3 app-data/app.db <<'SQL'
BEGIN;
UPDATE library_items SET title_id = NULL WHERE title_id IN (SELECT id FROM media_titles WHERE type IN ('album','artist'));
DELETE FROM member_favorites WHERE target_id IN (SELECT id FROM media_titles WHERE type IN ('album','artist'));
DELETE FROM media_titles WHERE type IN ('album','artist');
PRAGMA user_version=7;
COMMIT;
SQL
```

Artwork renditions of the removed albums are orphans that the daily cache sweep deletes. Rolling back also drops cast-photo renditions; upgrading again prepares them again in the background. The Music volume can stay mounted: 1.5.1 lists its files as plain tracks.

Version 0.3.0 starts a new release baseline; earlier release tags and downloads are no longer published. Keep a backup of your current data before changing deployments. The schema rollback commands above remain available for source-based recovery. Restoring a `pre-upgrade` backup loses progress and every other change made since that backup.

## Automatic scans and folder watching

The **Refresh** button beside the Library tabs reloads the current view. For vault owners, it also starts a full background rescan of eligible imported folders; the notification reports scans started or queued and folders unavailable. To scan one folder or review missing-file confirmation, use **Manage Server → Library & storage → Imports**. Other members can refresh their view without starting a server-wide scan.

The header activity indicator combines downloads with library-wide scan work. Open it to see the current phase and progress. Scans first discover file paths, then check and index them in bounded batches; indexing shows completed files against the discovered total. Work completed by one folder stays in the overall total while other folders continue. Totals can adjust if the files change during a scan. A file still copying or settling is shown separately until it is ready to index. Idle means no scan is running; it does not certify that every folder has been scanned recently. Members see aggregate progress without storage paths, folder names, or private media details; scan controls remain owner-only.

Lumina 2.1.0 can keep an **external** storage root up to date by itself. Both options are **off** for every root after an upgrade, so nothing changes until you turn them on under **Settings → Library & storage**, on each root's row. Managed roots do not use them. We recommend **Watch on (every 5 minutes) plus Nightly** for TV and Movies.

- **Scan schedule** runs a full scan of the root: Off, every 15 minutes, hourly, every 6 hours or **Nightly**. A scan never overlaps another on the same root, and only one automatic scan runs at a time (a scan stuck on an unresponsive share does not count; see below). After an outage, a nightly root scans once, not once per missed night. The row shows when the next scan is due; on a big root, check the "full scan takes about N min" hint before choosing 15 minutes.
- **Nightly at** (Settings → Library & storage) sets the hour nightly scans start. It uses the **server's** time zone, shown next to the hour. Set `TZ` in `.env` (or the compose `environment:` block), for example `TZ=America/Chicago`, then recreate the container. Without it the server is on UTC. The image includes `tzdata`.
- **Watch for new media** polls the folders' modification times and scans only the folders that changed. Interval: 1, 5 or 15 minutes for one full pass (5 is the default). It never reads file contents. A new file is indexed only after its size and time stop changing for about 90 seconds, so a copy in progress is not picked up half-written.

What the watcher sees: new, deleted and renamed files and folders. It does **not** see a file edited in place, such as an NFO rewritten by another tool, because that changes no folder time. The nightly scan catches those, which is why Watch plus Nightly is the recommended pairing.

NAS notes:

- inotify does not work on SMB or NFS, which is why Lumina polls. Folder times change over NFS, but the client's attribute cache can delay them by a few seconds up to about a minute; this only delays detection.
- A hard-mounted NFS share that stops answering cannot hang playback or the rest of Lumina. Between scans, after 30 seconds the root shows **unresponsive** and its automation is skipped (each skip is recorded with its reason); it resumes when the share answers again.
- If the share stops answering **during** a scan, that scan waits for it and continues when the share answers (a Cancel also takes effect only then). After about 30 seconds without progress the root shows **unresponsive**. From then on automatic scans (scheduled, and the ones the watcher starts) carry on for the other roots beside it; the stuck scan stays listed as **unresponsive** and continues when the share answers, so for a while two scans can run at once. Confirming a held import on that root answers "busy" instead of waiting. Other roots can also be scanned from **Imports**, or with **Scan now** unless the stuck scan was itself a Scan now.
- A root that is offline or swapped (`identity_mismatch`) is skipped the same way.
- A watcher scan that ends **partial** (a folder could not be read, or the share dropped at the end) is not retried by the watcher. On a Watch-only root (no schedule), press **Scan now** after an outage.
- On a union mount such as mergerfs, folder times may not reflect every branch; prefer hourly scans there.
- The mass-missing guard still applies to automatic scans. If a scan would mark a large share of a root's files missing (an unplugged drive, a wrong mount), it stops as **needs confirmation**, and automation for that root pauses. Lumina never confirms by itself: an admin reviews the run under **Settings → Library & storage → Imports** and confirms it or rescans.
- Diagnostics shows whether the poller is healthy and when each root was last scanned or watched.

## Music in Audio

**Audio** (`/audio`) has its own artist soundstage, artist and release pages, playlists, smart mixes and listening room. **Video** (`/video`) holds film, TV and saved video. `/library` bookmarks still resolve to the appropriate section; `/books` is the reading section's integration route.

Register the music folder under **Manage Server → Library & storage** and scan it. Lumina reads embedded album/track/disc tags, MusicBrainz identities, composer and performer credits, edition and label details, lyrics and ReplayGain. FLAC, MP3, AAC, Opus, Vorbis, WAV, AIFF, APE, WavPack, DSD and other registered audio formats are indexed. Playback uses the original source where the browser can decode it, with a lossless PCM compatibility conversion when needed. Conversion requires ffmpeg/ffprobe, included in the Docker image; it cannot restore quality absent from a lossy original.

MusicBrainz, Cover Art Archive, Wikimedia and LRCLIB provide useful metadata without accounts or paid keys. Automatic enrichment accepts a unique confident identity; ambiguous results appear under **Identify music** for review. Explicit identities are recorded in edit history. Saved edits and locks remain protected. YouTube discovery uses Lumina's existing public-source search and caches related performances, labeling official, live, cover and remix results separately. Provider outages retain useful cached results. Metadata workers are capped at two concurrent jobs and 128 pending requests; public lookup cache retains 10,000 answers, artwork and converted audio each have a 2 GiB disk cap. Set the music variables above to disable public lookup work.

**Manage music** previews edits or a shared artwork replacement for up to 500 selected titles. Album **Manage tracks** edits titles, artists, genres, disc/track order, lyrics and loudness, or groups selected tracks into a distinct release. These database edits survive rescans and have conflict-aware undo. Owners can separately preview **Write tags to files** for up to 100 tracks. Supported writeback formats are MP3, FLAC, Vorbis, Opus and MP4 audio. Originals are backed up under `music-file-backups`; restore refuses to overwrite a file changed since writeback. Keep this directory with database backups. Writeback needs a writable media mount and backup space; read-only mounts remain fully usable for database curation. Backups are capped at 10 GiB, and each apply requires free space for three times the selected original file size.

The browser keeps a persistent queue, supports repeat/shuffle and uses ReplayGain or existing server loudness analysis for optional normalization. The gapless engine schedules the current and next decoded track on the audio clock. Very large tracks and tracks whose duration, sample rate or channel count have not been probed use **Browser streaming** before buffered decoding. Native streaming does not promise gapless transitions or decoded-audio crossfade; known short tracks retain those capabilities. The same conservative choice applies to saved offline tracks. The player reports source codec/rate/depth separately from browser output and compatibility conversion.

### Desktop offline listening

Save a track, album or queue from the listening room while online, then open **Offline listening** (`/music-offline.html`) once before disconnecting. The standalone page caches its static application shell and fonts, plus saved audio, artwork and lyrics in browser storage scoped to the current member. It does not cache authenticated API responses or Sonos capability URLs. Use HTTPS or loopback for service workers; private LAN plain HTTP does not support them. Each transfer is capped at 100 uncached tracks, each audio file at 128 MiB and each artwork at 2 MiB, subject to the browser's storage quota. Unsupported browser codecs are stored as a truthful PCM compatibility copy; long saved tracks stream from a local Blob. Downloads respect the member's download permission. Switching members hides the previous member's offline collection; signing out removes that member's saved music. Browsers may evict local storage, so retain the original library.

### Sonos output

Choose **Output → Sonos** in the listening room. The server must discover the speakers by SSDP on their LAN, and **Manage Server → Connections → Addresses** must contain a local or public address the speaker can reach. Container networking must allow multicast discovery; routed networks and Docker bridges can require host networking or multicast forwarding. No speaker addresses or browser credentials are shared with another member.

The selected speaker's group coordinator receives up to the next 100 tracks. Output is lossless FLAC with supported source sample rates preserved up to 48 kHz and stereo; use **Compatibility** for 16-bit Sonos S1 output. The browser follows the speaker's track, position and volume. Browser loudness normalization applies only to browser output. Cast capabilities expire after 24 hours and are revoked on Stop, account changes or a denied member listening policy while status is polled. Speakers may finish a buffered track when disconnected from the server. Hardware verification requires speakers reachable from the running server.

## Editing details

Open a movie, show, season or episode and choose **Edit details** (or use the command palette) to correct titles, overviews, ratings, genres, cast and artwork in the Lumina web app. Edits are saved in Lumina's database only: nothing is written to NFO files, and Jellyfin apps (Infuse, Swiftfin) show the edits read-only; they cannot edit.

- **Who can edit:** vault owners. **Manage Server → Connections → Metadata → Let household members edit details** (off by default) lets members edit too. Only an owner can change a title's TMDB id (re-match).
- **Locks:** saving a field locks it, so refreshes, scans and Identify no longer change it, and Lumina keeps the original source value. **Revert to source** returns a field to that kept value. A field can also be locked without changing it, and **Lock this item** stops all automatic changes to the title.
- **History and undo:** every save, bulk edit, revert and lock is a batch in the title's history. Undo restores each field that has not changed since and names the ones that have. Anyone who can edit can undo, except a member cannot undo an owner's TMDB id change. Each title keeps its newest 200 history rows, and rows older than 365 days are dropped.
- **Bulk edit:** up to 500 titles and 40 changes per title in one request.
- **Artwork:** Poster (or episode still), up to 5 Backdrops (reorderable) and Logo, each chosen from TMDB candidates (needs a TMDB key), uploaded or removed.
- **Uploads:** JPEG, PNG or WebP only, judged by content (not file name or type). Up to 15 MiB, each side 64 to 8000 px and at most 40 megapixels; 30 uploads per hour per member. Lumina re-encodes every upload and strips its metadata, so GPS and camera data never survive. Uploads are stored **in the database**, so backups and the pre-upgrade backup include them (about 4 MiB at most each). Uploads no title or recent history row uses are removed by hourly maintenance.

## On-device models

Semantic search and subtitles from speech can run on this server's own CPU. In **Manage Server → AI features**, an admin turns a feature on and accepts **Download and turn on**. Lumina downloads the model in the background, verifies it, and starts the model only while a feature uses it. After 5 minutes without use it stops again.

- **What is in the image:** `llama-server` (llama.cpp, built from a pinned commit for amd64 and arm64 in a portable CPU configuration) for search. A small Lumina speech server using faster-whisper in its own Python environment (`/opt/lumina-asr`) for speech. There is no sidecar and no Docker socket. Both listen only on `127.0.0.1` inside the container, behind a secret that changes on every start.
- **Where models live:** `app-data/models/<model>/`. Downloads resume after a restart, and every file is checked against the checksum in Lumina's catalog before use. Models come only from that catalog (pinned Hugging Face revisions). Backups never include `app-data/models/` (a backup is the database only). After a restore, installed models stay as they are. Deleting the folder is safe; the model shows as not installed.
- **Resources:** the default search model is about 115 MB on disk and under 1.2 GiB of RAM while it runs. The default speech model is about 490 MB on disk and under 1.2 GiB of RAM (the accurate one is 1.6 GB on disk and under 2.2 GiB). Lumina uses the CPUs available to the container minus one (at most 12); an admin can lower this. A model does not start unless free memory covers it plus 512 MiB.
- **Precedence:** a local model that is installed and ready wins over the external endpoints below. Speech transcription and search indexing take turns, never at once.

Upgrading a runtime (maintainers): change `LLAMA_CPP_COMMIT` in the `Dockerfile` to the full commit of a llama.cpp release tag (`git ls-remote https://github.com/ggml-org/llama.cpp refs/tags/<tag>`). For the speech server, edit `docker/lumina-asr/requirements.in` and re-run the `uv pip compile` command at the top of `docker/lumina-asr/requirements.lock`. Then run `make models-smoke`.

## External speech-to-text server (optional)

If you prefer a separate transcription server to the on-device speech model, ASR transcripts can use any OpenAI-compatible `/v1/audio/transcriptions` endpoint; the household inference
server usually only serves chat completions. `docker-compose.yml` includes an optional `asr` service (CPU-only
[speaches](https://speaches.ai), pinned `ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cpu`) for this, off by default and
not part of the Lumina image, so it never starts with a plain `docker compose up`.

```bash
docker compose --profile asr up -d asr
# One-time per model (or after clearing app-data/asr-models/): download it into the model cache.
docker compose exec asr curl -sX POST http://localhost:8000/v1/models/Systran%2Ffaster-whisper-small
```

Then set `LUMINA_ASR_BASE_URL=http://asr:8000/v1` and `LUMINA_ASR_MODEL=Systran/faster-whisper-small` in `.env`
(or the same fields under **Manage Server → AI features**) and restart `lumina`. The `asr` service has no host port; it is reachable
only from other containers on the compose project's network, and its model cache (a few hundred MB, downloaded once)
lives in `./app-data/asr-models`. Transcribing is CPU-only and roughly real-time on a modern core with the `small`
model; a multi-hour video takes about as long to transcribe.

Any other OpenAI-compatible transcription server works too: whisper.cpp's server, LocalAI, or a
`/v1/audio/transcriptions` endpoint on another host on your network. Point `LUMINA_ASR_BASE_URL`
at it instead of running the `asr` service.

## Hardware transcoding (Intel QSV/VAAPI)

On a Linux host with an Intel GPU, add the override so the container gets `/dev/dri` and the host's render group:

```bash
curl -fsSLO https://github.com/shiftedx/lumina/releases/download/v0.3.4/docker-compose.qsv.yml
echo "LUMINA_RENDER_GID=$(stat -c %g /dev/dri/renderD128)" >> .env
docker compose -f docker-compose.yml -f docker-compose.qsv.yml up -d --no-build
```

The entrypoint grants only that one supplementary group. Without the override Lumina transcodes in software. On Docker Desktop for Mac there is no `/dev/dri`, so it always runs in software.

After the container is up, open **Settings → Playback & transcoding**, keep hardware acceleration on **Auto** and run **Diagnostics**. A working Intel setup reports `active: qsv`, `probe_ok: true` and tone-mapping `opencl`. `EACCES` on `/dev/dri` means `LUMINA_RENDER_GID` is not the group that owns `/dev/dri/renderD128`. A failing hardware session retries once in software. After three hardware failures Lumina stays in software until restart, and Diagnostics shows the fallback count. Set **Max concurrent transcodes** from a load test (see the host-move checklist in the tracking issue), not from a guess. The cap counts video encodes only; remux and audio-only sessions share a fixed limit of 16.

## Remote HTTPS

Direct LAN HTTP responses compress text assets and JSON when the client accepts gzip. Media bodies, byte ranges and event streams keep their original representation. The shipped Caddyfile sets `header_up Accept-Encoding identity` so Caddy continues choosing zstd or gzip for HTTPS clients; add that line to an existing Caddy reverse-proxy block when updating.

Keep port 8765 on loopback. The only supported remote topology is one TLS-terminating Caddy proxy in front of Lumina (`docker-compose.https.example.yml` + `docker/Caddyfile.example`). Create the first administrator over loopback first: remote mode refuses to start on an empty database.

```bash
curl -fsSLO https://github.com/shiftedx/lumina/releases/download/v0.3.4/docker-compose.https.example.yml
mkdir -p docker
curl -fsSL https://github.com/shiftedx/lumina/releases/download/v0.3.4/Caddyfile.example -o docker/Caddyfile.example
echo LUMINA_HOSTNAME=vault.example.com >> .env
docker compose -f docker-compose.yml -f docker-compose.https.example.yml pull
docker compose -f docker-compose.yml -f docker-compose.https.example.yml up -d --no-build
curl --fail https://vault.example.com/api/health
```

Point the DNS name at the host and allow inbound 80/443. Startup enforces the contract: `LUMINA_APP_PUBLIC_URL` = `LUMINA_FRONTEND_PUBLIC_URL` = the only `LUMINA_ALLOWED_ORIGINS` entry (a lowercase `https://` bare hostname), `LUMINA_SESSION_COOKIE_SECURE=true`, and `LUMINA_TRUSTED_PROXY_IPS` naming exactly one proxy IP (`/32` or `/128`). Lumina accepts one `X-Forwarded-For` address from that peer only and ignores forwarding headers from anyone else. A partial change is a startup error, not an insecure deployment. To go back to local-only, `down` the combined stack and `up -d` the base file.

## LAN HTTP mode

For a household LAN like a typical Jellyfin install: plain `http://<lan-ip>:8765` in browsers and in Infuse or Swiftfin, no DNS, certificate or proxy. It is an explicit opt-in (ADR 0001 amendment) with real costs: passwords, session cookies and Jellyfin tokens cross the LAN in plaintext, and anyone on the network who opens the setup screen first on an empty database becomes the administrator. Use it only on a network you trust, create the first administrator immediately after the first start (or over loopback before enabling it), and never forward port 8765 from the router.

Add to `.env`, using the host's fixed LAN address:

```bash
LUMINA_LAN_IP=192.168.1.20
```

Create `docker-compose.lan.yml` next to `docker-compose.yml`:

```yaml
services:
  lumina:
    ports:
      - "${LUMINA_LAN_IP:?set LUMINA_LAN_IP in .env}:8765:8765"   # added to the loopback mapping, which keeps working on the host
    environment:
      LUMINA_LAN_HTTP: "true"
      LUMINA_APP_PUBLIC_URL: http://${LUMINA_LAN_IP}:8765
```

```bash
docker compose -f docker-compose.yml -f docker-compose.lan.yml pull lumina
docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d --no-build lumina
curl --fail http://192.168.1.20:8765/api/health
```

Startup refuses the mode unless the public URL is `http://` on a private IPv4 literal (10/8, 172.16/12 or 192.168/16; no DNS names, IPv6, public, link-local or loopback addresses), `LUMINA_SESSION_COOKIE_SECURE` is false, `LUMINA_FRONTEND_PUBLIC_URL` is unset or equal, and `LUMINA_ALLOWED_ORIGINS` names nothing else. Browsers must use exactly that URL (a hostname like `nas.local` pointing at it is refused as an untrusted host, unless it is the local address below). Remote HTTPS and LAN HTTP mode are alternatives; run one or the other. To go back to local-only, `down` and `up -d` the base file.

**A public address beside LAN mode.** To serve the public address (for invited members) through a reverse proxy such as Traefik while keeping LAN HTTP, set `LUMINA_TRUSTED_PROXY_IPS` to the proxy's docker network, e.g. `172.18.0.0/16`: a range is accepted because a container's bridge IP is not fixed. Every host in that range may set `X-Forwarded-For` and `X-Forwarded-Proto`, so name only the proxy's own bridge network. Startup logs a warning when a trusted range is not a private IPv4 range of `/16` or narrower, or overlaps the LAN address's `/24`; fix the setting rather than ignore it.

**A local address behind the reverse proxy.** To reach Lumina at home by name, such as `http://lumina.home.arpa`, point that name at the proxy host in your home DNS, add a router for it on the proxy's plain-http entrypoint, and save it under **Settings → Members → Local address** (a vault owner in a browser; it applies at once, no restart). It needs LAN HTTP mode and the trusted proxy above. The name must be a home-network DNS name ending in `.local`, `.lan`, `.home.arpa` or `.internal` (no IP, wildcard or path; a port is allowed), and differ from the public address. Lumina then trusts it as a host and a browser origin, sends plain-http cookies for it, keys throttles on the LAN device from `X-Forwarded-For`, and, for an `http://` local address, never pauses sign-in on it or sends HSTS for it. Connected apps and the Media server page show it as the address at home, beside the public address for away; invite emails keep the public address. A Traefik example, a separate router on the `web` entrypoint only and without the https-redirect middleware the public router uses:

```yaml
services:
  lumina:
    labels:
      traefik.http.routers.lumina-lan.rule: Host(`lumina.home.arpa`)
      traefik.http.routers.lumina-lan.entrypoints: web
      traefik.http.routers.lumina-lan.service: lumina
      traefik.http.services.lumina.loadbalancer.server.port: "8765"
```

Leave Traefik's `forwardedHeaders.trustedIPs` unset on that entrypoint so it replaces any `X-Forwarded-*` a LAN device sends. Apple devices resolve `.local` over multicast DNS first; if a `.local` name is slow to resolve there, use `.home.arpa` or `.lan`. Lumina refuses other suffixes because a name that also resolves on the internet would carry passwords and session cookies in plaintext wherever it leads. If LAN HTTP mode is turned off later, a saved local address is ignored with a startup warning.

Behind Cloudflare Tunnel the proxy's `X-Forwarded-For` is the tunnel host for every visitor, so for requests to the public address's host from a trusted proxy peer Lumina keys its per-client throttles on Cloudflare's `CF-Connecting-IP` instead (one address; anything else is a 400). A host that can reach the proxy directly can forge that header, which only moves its own throttle bucket: stream and image grants are keyed on the visitor header together with the forwarded hop that actually reached the proxy, so a forged header cannot borrow another device's grant.

**Shared links and invitation previews.** Lumina serves fixed media-vault artwork and Open Graph / X card metadata from the public app shell. Configure the public address under **Settings → Members** so preview images use an address recipients can reach. New invitation links use `/invite#invite=…`; the secret stays in the browser-only fragment and never appears in preview metadata. Existing `/#invite=…` links still work. Previews contain no member names, library titles or private artwork. Social services may cache a preview after its first fetch.

**Sign-in throttling on the public address.** Ten wrong passwords for one username within 15 minutes (from any mix of addresses, for real and unknown usernames alike) pause that username's sign-in through the public address (web, HTTP Basic and Jellyfin apps) until the window drains (at most 15 minutes). Sign-in on the LAN address and loopback is never paused, a successful sign-in clears the count, and a vault owner can lift it under Settings → Members → the member → Unlock sign-in. Separately, ten failures from one client address in 5 minutes block that address. Sign-ins, failures (account id or `unknown`, never the typed name or password), pauses and unlocks are logged on the `lumina.audit` logger with the client address.

**Two-step verification (optional).** Any member can turn it on under **Settings → You → Profile → Two-step verification**: scan the QR code (drawn locally; the secret never leaves Lumina) with an authenticator app, confirm a code, and save the ten single-use recovery codes. Sign-in then asks for the 6-digit code after the password, on the public address and the LAN alike; "Don't ask again on this device for 30 days" sets a `{session cookie name}_trust` cookie scoped to `/api/session` that a password change, reset or re-enrollment voids. Wrong codes count toward the same throttles as wrong passwords, and each code is accepted once. It is off by default and never forced on members; a vault owner can require it for vault owners only (**Settings → Members**), after which an owner without it can use Lumina but not owner settings until they turn it on. Turning it off needs the password and a code. A vault owner can reset a member's two-step verification (a lost phone) from the member's page; the reset is audit-logged.

The secret is encrypted with its own key, `app-data/totp-key` (32 random bytes, `0600`, made at the first enrollment), and bound to its account. **Back that file up with your backups and never delete it**: without it (or after restoring a backup into a data directory without it) every enrolled authenticator is unreadable, and startup logs an error saying so. Members then sign in with a recovery code, or are reset. Lumina never replaces an existing key file; a damaged one is reported rather than overwritten. Upgrading from 2.9.0, whose key came from `app-data/art-secret`, re-encrypts every secret under the new key at the first start (all or nothing; a crash part-way leaves them as they were and the next start retries). Leave `art-secret` in place until that start has run: a secret it can no longer open stays as it was, with an error in the log, and moves once the file is restored and Lumina restarted. Members' "Don't ask again on this device" choices end once at that upgrade. Afterwards deleting `art-secret` only changes artwork URLs and ends trusted devices. If the only vault owner has lost both their phone and their recovery codes, reset them from the host:

```sh
docker compose exec lumina python -m app.services.two_factor reset <username>
```

**App passwords.** Jellyfin apps (Infuse, Swiftfin) and HTTP Basic clients cannot ask for a code, so an account with two-step verification refuses its account password there ("Use an app password from Lumina → Settings → Connected apps") and accepts an **app password** instead: **Settings → Connected apps → Add an app** makes one per device, shows the server address, username and password once, and stores only a digest. Revoking it signs out the app that used it; changing or resetting the account password revokes all of them. Accounts without two-step verification keep signing in to apps with their normal password, and turning it on does not sign out apps that already signed in (**Sign out all apps** does, keeping app passwords). An app password works only for app and HTTP Basic sign-in, never on the web sign-in form. Over HTTP Basic it only browses and plays media (reads, playback progress, watched): it is never a vault owner and cannot manage accounts, settings, connected apps or two-step verification. Password reset links, two-step resets, role and active changes, invitations, the public address and backup downloads need a signed-in browser (never an agent token or HTTP Basic), and an owner turns their own two-step verification off under Settings → You, not with the member reset.

**Code lockout.** Ten wrong codes in a row for one account, from any address (LAN included), pause its authenticator codes for 15 minutes, doubling with each further miss up to 24 hours (`two_factor.locked` on `lumina.audit`). A recovery code still signs in and clears the count, as does a right code after the pause or an owner's two-step reset. When outgoing email is set up (the Requests mail server), the first pause also emails every active vault owner who saved a notification email for Requests (even with request emails turned off), naming the account; further misses after a pause do not email again.

**Who's watching? ring cookie.** When a member opts in to "Show me in Who's watching on this device", Lumina also sets a ring cookie named `{session cookie name}_ring`, scoped to `/api/session`, with `Secure` and `SameSite` taken from the session cookie. In LAN HTTP mode it crosses the LAN in plaintext like the session cookie, but it holds up to six members' sessions, so a household using that mode should leave the opt-in off on shared or untrusted machines. Anyone with access to an opted-in browser can act as its remembered non-owner members (vault owners always need their password); see `SECURITY.md`. Rolling back to an older image leaves an unused cookie that the older image ignores.

## Jellyfin apps (Infuse, Swiftfin)

The in-repository [native Apple proof](../apple/README.md) uses this same opt-in API for iPhone, iPad, and Apple TV. It needs no server schema or deployment change; enable the API and use the same address and member credentials described below. It is a development build, with a [hardware test checklist](../apple/docs/device-validation.md), rather than an App Store release.

Lumina answers the Jellyfin API well enough for Infuse (tvOS, iOS, macOS) and Swiftfin to browse the vault, play files and sync watch state with Lumina's web UI. It is off by default and needs the Remote HTTPS setup or LAN HTTP mode above. With Remote HTTPS, apps connect through Caddy on the LAN and remotely alike, never to port 8765. In LAN HTTP mode the address is `http://<lan-ip>:8765` and works on the LAN only.

1. **Manage Server → Connections → Metadata → Apps like Infuse**: turn it on and copy the server address it shows: the same address you open Lumina at, `https://<host>` (or `http://<lan-ip>:8765` in LAN HTTP mode), with nothing added. The API is served at the server root only; `/jellyfin` and `/emby` addresses do not work.
2. In Infuse: **Settings → Shares → Add Share → Jellyfin** (labels vary slightly by version). Enter the address with HTTPS on port 443 (LAN HTTP mode: HTTP on port 8765), plus the member's own Lumina username and password. Save, then keep **Library Mode** on so Movies, Shows and Channels fill in with artwork.
3. In Swiftfin: **Connect to Server** → the same address → sign in as the member.

Anime series appear in their own **Anime** library; if Infuse does not show it after an upgrade, enable it in the share's library settings. Item ids do not change, so watch state and favourites carry over.

Roku and Android TV also connect through the Jellyfin API. TV playback compatibility fixes include the file-source fields Roku needs, the primary source identity Android TV's external player needs, and an HLS fallback when a client disables direct play. After updating, close and reopen the TV app so it fetches fresh item details before retrying playback.

Audio conversion honors the client's AAC-specific channel limit even when the device supports more channels with other codecs. For Roku profiles that limit AAC to stereo, unsupported surround audio is converted to stereo AAC; directly playable surround tracks are retained.

Roku HLS playback delivers separate fMP4 video and audio renditions. Copies of each completed segment are cached in the playback session directory, preserving its timestamps and avoiding a second encode. This works around Roku devices that play combined fMP4 video without sound.

The Roku HLS fallback always uses stereo AAC, including when the app advertises Dolby passthrough support. This avoids unreadable EAC3 initialization headers and Dolby rendition playback errors. Compatible direct playback keeps its original audio, and HLS still copies supported video.

Members with two-step verification type an **app password** instead of their account password (see "App passwords" above). Each sign-in appears under **Settings → Connected apps** with its device, client and last-seen time. **Revoke** signs that app out immediately and stops its streams. Changing or resetting a password revokes all of that member's apps. Every member signs in as themselves: apps see only what that member can see in Lumina.

Backups keep the Jellyfin server key, so a restored vault keeps its `ServerId` and apps reconnect without being re-added. Treat a backup file like a credential: whoever holds it can also forge image tags. Backups are already admin-only and password-gated.

**Keep stream URLs out of logs.** Media players cannot send headers on video segment requests, so Jellyfin stream and playlist URLs carry the app's token as `ApiKey=`. Lumina never logs it. The shipped Caddyfile has no `log` directive, so Caddy writes no access log; keep it that way. If you need access logs, strip query strings and Jellyfin auth headers:

```caddyfile
	log {
		format filter {
			request>uri query {
				delete ApiKey
				delete api_key
				delete apikey
			}
			request>headers>X-Emby-Authorization delete
			request>headers>X-Emby-Token delete
			request>headers>X-Mediabrowser-Token delete
		}
	}
```

To diagnose a client, set `LUMINA_JELLYFIN_TRACE=1` in `.env`, restart, reproduce, then read `docker compose logs lumina | grep jellyfin.` (method, route and parameter names only). `jellyfin.unhandled` lines name requests Lumina does not serve yet. Turn the flag off afterwards.

### Moving over from Jellyfin

Members can bring their Jellyfin watch history into Lumina: what they watched, where they stopped, and their favorites.

1. In **Manage Server → Connections → Metadata → Jellyfin server to import from**, enter the address Lumina's container can reach, such as `http://192.168.1.20:8096`.
   - The address must be reachable from inside the Lumina container. `localhost` there is the container, not the host: if Jellyfin runs on the same machine, use that machine's LAN IP.
   - Put no username or password in it.
   - Lumina refuses an address that redirects, so enter the final one.
2. Bring everyone over at once, or let each member do it:
   - **Everyone:** a vault owner opens **Settings → Members → Bring members over from Jellyfin** and signs in with a Jellyfin administrator account.
     - The preview pairs each Jellyfin user with the Lumina member of the same username, or a new household member.
     - Untick anyone to leave out, then choose **Bring members over**. It can take a few minutes for large histories; keep the page open.
     - Each new member gets a one-time password link, shown only in the result and valid for 24 hours. Send it to them. **Create reset link** on their row replaces a lost one.
   - **One member:** the member opens **Settings → Privacy & data → Watch history from Jellyfin**, signs in with their own Jellyfin account, checks the preview and imports.

Matching works best when both servers see the same files. Lumina compares the end of each file path, so different mount points (`/data/movies/…` in Jellyfin, `/media/movies/…` in Lumina) are fine. Otherwise it uses TMDB, IMDb and TVDB ids.

Passwords are never stored, and newer Lumina progress is never overwritten, so running it again is harmless. Clear the address when everyone has moved.

**Cast photos.** When Jellyfin wrote your NFO files, they name a photo for most cast members in Jellyfin's `metadata/People` folder. Lumina can show those photos without TMDB:

1. Copy that folder somewhere Lumina keeps its own data and mount it read-only, for example `- /srv/lumina/people:/media/people:ro`. Jellyfin can then be removed.
2. Set `LUMINA_PEOPLE_DIR=/media/people`. Do not register it as a storage root.
3. Rescan each video root once (**Imports → Import a folder**). This records every credit's photo and role.

Lumina reads only files inside that folder, never through a symlink, and only JPEG, PNG or WebP up to 2 MiB. It makes small portraits in the background after all other artwork. While the folder is unmounted, people show their initials. The files must be readable by `LUMINA_RUNTIME_UID`.

## TV and movie metadata (TMDB)

Local NFO files and folder names are always read. For titles without an NFO, Lumina can fetch posters, overviews, cast and ratings from [TMDB](https://www.themoviedb.org/). Create a free TMDB account, request an API key under **Settings → API**, and paste either the v3 key or the v4 read access token into **Manage Server → Connections → Metadata** (write-only; **Test** checks it), or set `LUMINA_TMDB_API_KEY`. **Metadata language** sets one language for the whole library. TMDB never overwrites NFO values or your own edits. Titles it cannot match confidently are listed under **Unmatched** for **Fix match**. Like the AI key, the TMDB key is left out of backups; enter it again after a restore. Lumina uses the TMDB API but is not endorsed or certified by TMDB (attribution is shown in About).

## AI features

AI features use only the OpenAI-compatible endpoints configured under **Manage Server → AI features** (`LUMINA_AI_*`, `LUMINA_ASR_*`). **Manage Server → AI features** lists each feature as On, Off, "Needs an AI endpoint" or "Needs a speech server". Without them every feature hides or falls back:

| Feature | Needs | Without it |
| --- | --- | --- |
| Subtitles from speech | speech server | Hidden |
| Sync subtitles to audio | nothing (speech-server words improve it) | Works on detected speech |
| Translate subtitles | AI endpoint | Hidden |
| "Previously on" recaps | AI endpoint and episode summaries | Previous episodes' overviews |
| Smart-collection builder | AI endpoint | Manual rule builder |
| Semantic search | optional embedding model (Manage Server → AI features) | Built-in local ranking |
| Match tie-breaker | AI endpoint | Low-confidence titles go to Unmatched |
| Intro/credits detection | nothing | Always local |

**TheIntroDB** lookups (Manage Server → Connections → Metadata) are off by default. Each lookup tells a third party which episodes the household owns.

On the first v3 boot after an upgrade, restart recovery re-queues any `queued` or `running` speech-to-text (ASR) rows left over from before the restart, so a transcription in progress at shutdown resumes instead of being lost.

### Optional loudness and caption backfills

Automatic loudness analysis and caption ingestion are quiet by default. Existing automatically queued caption jobs remain pending; they are not deleted or marked failed. Requested-item loudness work and explicitly requested caption bulk work run once the app is quiet. Interactive caption generation and playback subtitle extraction remain available on demand.

To enable automatic backfills during a daily maintenance window, add both variables to the Compose `environment:` block (or the native service environment) and set both `LUMINA_BACKGROUND_BACKFILL_START_HOUR` and `LUMINA_BACKGROUND_BACKFILL_END_HOUR` to different integers from 0 through 23. For example, `2` and `5` allow work from 02:00 inclusive to 05:00 exclusive in the server's local timezone (`TZ` in Docker). A start later than the end crosses midnight. Leaving both unset disables automatic backfills; incomplete or empty windows are rejected at startup.

Loudness and background caption extraction share one running media-child slot; interactive subtitle extraction keeps its own path. Artwork preparation and unrelated AI workers are outside this slot. Authenticated app activity pauses background media children immediately. They remain paused until requests finish and the app has been quiet for 30 seconds; maintenance work also pauses when its window closes. Authenticated polling counts conservatively as app use; passive server-sent event connections alone do not keep the server busy indefinitely. Paused time does not consume extraction/analysis timeouts. Scheduled recordings and backups continue independently.

## Protected Twitch broadcasts

Some Twitch broadcasts require DRM and permit viewing only on the Twitch website, including through restrictions on Twitch's own embedded player. When Twitch returns `no_cdm_specified`, Lumina explains the restriction and offers **Watch on Twitch** in the player. This opens Twitch in a new tab and preserves the member's place in Lumina. The guarded HLS relay cannot play, record or save these broadcasts. Updating extraction settings or retrying the stream does not enable DRM playback.

## Chat emoji and emotes

Live and replay chat preserve standard Unicode emoji and display provider-supplied YouTube and Twitch custom emotes. Custom images use a separate RAM-only cache (16 MiB total, 128 KiB per image), with bounded registrations and fetch concurrency; they do not compete with poster artwork. Repeated emotes share cached images. Unavailable images and images beyond a response's budget keep their readable text labels.

Existing replay assets flattened before emote support retain their saved text. Refresh replay chat to rebuild those assets when the provider still offers them. Emote source addresses are internal JSON metadata; API responses contain authenticated Lumina image paths. No database migration or operator setting is required.
