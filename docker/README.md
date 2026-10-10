# Lumina Docker operator guide

Lumina ships as one container: the FastAPI backend serves the built React app on port 8765. Docker Compose is the supported install path. Read `RELEASE_NOTES.md`, `SECURITY.md` and `LICENSE` before exposing an installation.

## Install

```bash
git clone https://github.com/shiftedx/lumina lumina && cd lumina
cat > .env <<EOF
LUMINA_RUNTIME_UID=$(id -u)
LUMINA_RUNTIME_GID=$(id -g)
LUMINA_SOURCE_REVISION=$(git rev-parse HEAD)
EOF
docker compose up --build -d
docker compose ps          # lumina must report "healthy"
```

Open <http://127.0.0.1:8765>. Stop with `docker compose down`; logs with `docker compose logs --tail=100 lumina`.

The image is multi-stage (the frontend is built in a Node stage; only `dist/`, the Node binary yt-dlp needs for YouTube, ffmpeg and the hash-locked Python runtime reach the final image). The container runs read-only with all capabilities dropped except those the entrypoint needs to `chown` the data directory, then drops to the numeric `LUMINA_RUNTIME_UID:LUMINA_RUNTIME_GID` account (never 0) with `setpriv`, clearing every supplementary group except the optional host render group (`LUMINA_RENDER_GID`, see "Hardware transcoding"). The image healthcheck polls `GET /api/health`.

ffmpeg/ffprobe are [jellyfin-ffmpeg](https://github.com/jellyfin/jellyfin-ffmpeg) 8.1.2-5 (GPL; QSV/VAAPI, OpenCL tone-mapping and chromaprint), installed from the release's `trixie_amd64`/`trixie_arm64` `.deb`. Each is pinned by sha256 in the `Dockerfile` (`ADD --checksum`) and symlinked from `/usr/lib/jellyfin-ffmpeg/` into `/usr/local/bin/`. On amd64 the image also installs Intel's OpenCL runtime (`intel-opencl-icd` 26.31.39395.13, `intel-igc-core-2`/`intel-igc-opencl-2` 2.40.13, `libigdgmm12` 22.10.0) from Intel's GitHub releases, again pinned by sha256. It is not packaged for Debian trixie, and the versions match Jellyfin's own trixie image. The media toolchain adds ~150 MB over the previous static build. Both ffmpeg binaries are GPL-licensed; [`CREDITS.md`](../CREDITS.md) links their source and lists every bundled component. To upgrade, read the new digests with `gh api repos/jellyfin/jellyfin-ffmpeg/releases/latest --jq '.assets[] | select(.name | test("trixie_(amd64|arm64).deb$")) | .name + " " + .digest'` (Intel: `gh api repos/intel/compute-runtime/releases/tags/<ver>` and `repos/intel/intel-graphics-compiler/releases/tags/v<ver>`), update the `ADD` lines, and rebuild.

## Configure

All settings are `LUMINA_*` environment variables (full list: `backend/app/config.py`). `docker-compose.yml` passes these from `.env` or the shell:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LUMINA_RUNTIME_UID` / `_GID` | `1000` | Host account that owns `./app-data` and managed media mounts. |
| `LUMINA_STORAGE_MOUNT_PARENTS` | `/media` | Comma-separated container paths under which admins may register storage roots. |
| `LUMINA_AI_BASE_URL` / `_AI_MODEL` | empty (off) | OpenAI-compatible chat endpoint for summaries, moments and idea graphs, e.g. `http://192.0.2.10:8080/v1`. |
| `LUMINA_ASR_BASE_URL` / `_ASR_MODEL` | empty (off) | OpenAI-compatible speech-to-text endpoint for transcripts. |
| `LUMINA_TMDB_API_KEY` | empty (off) | TMDB v3 API key or v4 read token for movie and TV metadata and artwork. An admin can change or clear it under **Settings → Media server** (write-only). |
| `LUMINA_ARTWORK_PASS` | `on` | `off` stops the background pass that prepares small copies of artwork; they are then made only when first requested. |
| `LUMINA_PEOPLE_DIR` | unset | Container path of a read-only folder of cast photos in the `metadata/People` layout, such as a copy of Jellyfin's. Cast photos for titles whose NFO files name them. `LUMINA_JELLYFIN_PEOPLE_DIR`, the name before 2.11, is still read. |
| `LUMINA_JELLYFIN_TRACE` | `0` | `1` logs each Jellyfin request's method, route template and query parameter names (never values or headers). Use it only while capturing client traffic. |
| `LUMINA_LAN_HTTP` | `false` | `true` serves plain http on a private LAN IP without a proxy. Set it only through the override in "LAN HTTP mode". |
| `TZ` | `UTC` | Server time zone, e.g. `America/Chicago`. Library scans set to **Nightly at** use it. See "Automatic scans and folder watching". |
| `LUMINA_DOCKER_IMAGE` | `lumina:local` | Image tag Compose builds and runs. |

AI and ASR values are only defaults: an admin can change or clear them under **Settings → AI & models** (the API key is write-only there). Tunables such as `LUMINA_QUARANTINE_RETENTION_HOURS`, `LUMINA_REMOTE_STREAM_*`, `LUMINA_RELAY_*` and `LUMINA_LIVE_RECORDING_*` keep safe defaults; add them to the `environment:` block only when you need to.

## First owner and members

A fresh install has no accounts and no default password. Open the loopback URL (or, in LAN HTTP mode, the LAN URL right after the first start) and create the first administrator on the setup screen (`POST /api/bootstrap/admin`; it refuses once any account exists). Passwords need 12–128 characters, three of lowercase/uppercase/number/symbol, and must not contain the username.

Add people under **Settings → Members**: issue an invitation link (shown once; revocable) or a password-reset link. Sessions are opaque server-side records in the database, so restarts keep people signed in.

Lumina stores no provider credentials. yt-dlp runs with a closed allowlist of public options (no cookies, credentials, browser auth or exec), so sources that need sign-in are unavailable. Acquisition accepts public HTTP(S) destinations only; private and LAN addresses are refused.

From 1.9.0 the recommender also runs background yt-dlp searches and channel listings for each member who has watched or followed something. The whole household stays within 40 calls an hour and 300 a day, one at a time, and no search text is stored or logged. An admin can turn it off under **Settings → AI & models → Personalised recommendations**, and a member can erase their recommendation history under **Settings → You**.

## Storage

- `./app-data` holds the database, backups, caches and the built-in Library (`app-data/library/<member-id>/`). Keep it app-exclusive: no household-writable share.
- Never open the live `app.db` from another process while the container runs (on macOS bind mounts a second writer can poison the running app with `disk I/O error`). Use the API or stop the container first.
- Extra media: bind-mount it under a mount parent, then register it under **Settings → Library & storage**:

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

**Settings → Backups → Back up now** writes a consistent SQLite copy plus a manifest to `app-data/backups/` while Lumina runs; a daily automatic backup keeps the newest 7, and the daily pass also keeps only the newest 3 `pre-upgrade` backups. **Verify** re-checks the checksum, integrity and schema version. Backups contain password/session hashes (never the saved AI API key; re-enter it in Settings → AI & models after a restore): they are admin-only and `0600`, and **Download** asks for your password again. Copy `app-data/backups/` off the host, together with `app-data/totp-key`: backups hold the database only, and without that key file every two-step verification authenticator in them is unreadable (see Two-step verification below).

Media is not in these backups. Neither are on-device models (`app-data/models/`); they re-download from Settings if lost. Back up `app-data/library/` and managed roots with your usual file tooling, and mount every root at the same container path before restoring.

Restore is offline:

```bash
docker compose down
docker compose run --rm --no-deps lumina python -m app.restore /app/backend/.data/backups/lumina-<timestamp>-manual.db
docker compose up -d
```

The command refuses while a server holds the data directory, verifies the backup against its manifest, revokes every session and unused invitation/reset link, and keeps the previous database as `app.db.pre-restore-<timestamp>` (rename it back to roll back).

## Upgrades

To update: `git pull`, update `LUMINA_SOURCE_REVISION` in `.env`, `docker compose up --build -d`. When a release raises the schema version, Lumina first writes an automatic `pre-upgrade` backup (listed under Settings → Backups), then applies its additive upgrade steps in one transaction. It refuses to open a database from a newer release; to roll back, see "Rollback after a schema upgrade" below (the pre-upgrade backup is the last resort).

### Rollback after a schema upgrade

Schema steps 6 to 17 only add tables, columns and indexes that older releases ignore, so rolling back keeps everything written since the upgrade, except step 8's album and artist titles (below). Stamp the database with the schema version of the release you roll back to:

| Rolling back to | Stamp |
| --- | --- |
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

Then start the rollback image (set `LUMINA_SOURCE_REVISION` back, `docker compose up --build -d`). Upgrading again later re-applies the steps safely: they skip a column or index that is already there, step 7 re-dates every title from the file times the scans recorded (no file is read, so an unplugged root does not matter), and step 8 sorts every title into Movies, Shows or Anime again. Restoring the `pre-upgrade` backup is a last resort only: it loses progress and every other change made since the upgrade.

## Automatic scans and folder watching

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

## Editing details

Open a movie, show, season or episode and choose **Edit details** (or use the command palette) to correct titles, overviews, ratings, genres, cast and artwork in the Lumina web app. Edits are saved in Lumina's database only: nothing is written to NFO files, and Jellyfin apps (Infuse, Swiftfin) show the edits read-only; they cannot edit.

- **Who can edit:** vault owners. **Settings → Media server → Let household members edit details** (off by default) lets members edit too. Only an owner can change a title's TMDB id (re-match).
- **Locks:** saving a field locks it, so refreshes, scans and Identify no longer change it, and Lumina keeps the original source value. **Revert to source** returns a field to that kept value. A field can also be locked without changing it, and **Lock this item** stops all automatic changes to the title.
- **History and undo:** every save, bulk edit, revert and lock is a batch in the title's history. Undo restores each field that has not changed since and names the ones that have. Anyone who can edit can undo, except a member cannot undo an owner's TMDB id change. Each title keeps its newest 200 history rows, and rows older than 365 days are dropped.
- **Bulk edit:** up to 500 titles and 40 changes per title in one request.
- **Artwork:** Poster (or episode still), up to 5 Backdrops (reorderable) and Logo, each chosen from TMDB candidates (needs a TMDB key), uploaded or removed.
- **Uploads:** JPEG, PNG or WebP only, judged by content (not file name or type). Up to 15 MiB, each side 64 to 8000 px and at most 40 megapixels; 30 uploads per hour per member. Lumina re-encodes every upload and strips its metadata, so GPS and camera data never survive. Uploads are stored **in the database**, so backups and the pre-upgrade backup include them (about 4 MiB at most each). Uploads no title or recent history row uses are removed by hourly maintenance.

## On-device models

Semantic search and subtitles from speech can run on this server's own CPU. In **Settings → AI & models**, an admin turns a feature on and accepts **Download and turn on**. Lumina downloads the model in the background, verifies it, and starts the model only while a feature uses it. After 5 minutes without use it stops again.

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
(or the same fields under **Settings → AI & models**) and restart `lumina`. The `asr` service has no host port; it is reachable
only from other containers on the compose project's network, and its model cache (a few hundred MB, downloaded once)
lives in `./app-data/asr-models`. Transcribing is CPU-only and roughly real-time on a modern core with the `small`
model; a multi-hour video takes about as long to transcribe.

Any other OpenAI-compatible transcription server works too: whisper.cpp's server, LocalAI, or a
`/v1/audio/transcriptions` endpoint on another host on your network. Point `LUMINA_ASR_BASE_URL`
at it instead of running the `asr` service.

## Hardware transcoding (Intel QSV/VAAPI)

On a Linux host with an Intel GPU, add the override so the container gets `/dev/dri` and the host's render group:

```bash
echo "LUMINA_RENDER_GID=$(stat -c %g /dev/dri/renderD128)" >> .env
docker compose -f docker-compose.yml -f docker-compose.qsv.yml up -d
```

The entrypoint grants only that one supplementary group. Without the override Lumina transcodes in software. On Docker Desktop for Mac there is no `/dev/dri`, so it always runs in software.

After the container is up, open **Settings → Playback & transcoding**, keep hardware acceleration on **Auto** and run **Diagnostics**. A working Intel setup reports `active: qsv`, `probe_ok: true` and tone-mapping `opencl`. `EACCES` on `/dev/dri` means `LUMINA_RENDER_GID` is not the group that owns `/dev/dri/renderD128`. A failing hardware session retries once in software. After three hardware failures Lumina stays in software until restart, and Diagnostics shows the fallback count. Set **Max concurrent transcodes** from a load test (see the host-move checklist in the tracking issue), not from a guess. The cap counts video encodes only; remux and audio-only sessions share a fixed limit of 16.

## Remote HTTPS

Direct LAN HTTP responses compress text assets and JSON when the client accepts gzip. Media bodies, byte ranges and event streams keep their original representation. The shipped Caddyfile sets `header_up Accept-Encoding identity` so Caddy continues choosing zstd or gzip for HTTPS clients; add that line to an existing Caddy reverse-proxy block when updating.

Keep port 8765 on loopback. The only supported remote topology is one TLS-terminating Caddy proxy in front of Lumina (`docker-compose.https.example.yml` + `docker/Caddyfile.example`). Create the first administrator over loopback first: remote mode refuses to start on an empty database.

```bash
echo LUMINA_HOSTNAME=vault.example.com >> .env
docker compose -f docker-compose.yml -f docker-compose.https.example.yml up --build -d
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
docker compose -f docker-compose.yml -f docker-compose.lan.yml up --build -d
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

Lumina answers the Jellyfin API well enough for Infuse (tvOS, iOS, macOS) and Swiftfin to browse the vault, play files and sync watch state with Lumina's web UI. It is off by default and needs the Remote HTTPS setup or LAN HTTP mode above. With Remote HTTPS, apps connect through Caddy on the LAN and remotely alike, never to port 8765. In LAN HTTP mode the address is `http://<lan-ip>:8765` and works on the LAN only.

1. **Settings → Media server → Apps like Infuse**: turn it on and copy the server address it shows: the same address you open Lumina at, `https://<host>` (or `http://<lan-ip>:8765` in LAN HTTP mode), with nothing added. The API is served at the server root only; `/jellyfin` and `/emby` addresses do not work.
2. In Infuse: **Settings → Shares → Add Share → Jellyfin** (labels vary slightly by version). Enter the address with HTTPS on port 443 (LAN HTTP mode: HTTP on port 8765), plus the member's own Lumina username and password. Save, then keep **Library Mode** on so Movies, Shows and Channels fill in with artwork.
3. In Swiftfin: **Connect to Server** → the same address → sign in as the member.

Anime series appear in their own **Anime** library; if Infuse does not show it after an upgrade, enable it in the share's library settings. Item ids do not change, so watch state and favourites carry over.

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

1. In **Settings → Media server → Jellyfin server to import from**, enter the address Lumina's container can reach, such as `http://192.168.1.20:8096`.
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

Local NFO files and folder names are always read. For titles without an NFO, Lumina can fetch posters, overviews, cast and ratings from [TMDB](https://www.themoviedb.org/). Create a free TMDB account, request an API key under **Settings → API**, and paste either the v3 key or the v4 read access token into **Settings → Media server → TMDB** (write-only; **Test** checks it), or set `LUMINA_TMDB_API_KEY`. **Metadata language** sets one language for the whole library. TMDB never overwrites NFO values or your own edits. Titles it cannot match confidently are listed under **Unmatched** for **Fix match**. Like the AI key, the TMDB key is left out of backups; enter it again after a restore. Lumina uses the TMDB API but is not endorsed or certified by TMDB (attribution is shown in About).

## AI features

AI features use only the OpenAI-compatible endpoints configured under **Settings → AI & models** (`LUMINA_AI_*`, `LUMINA_ASR_*`). **Settings → AI & models** lists each feature as On, Off, "Needs an AI endpoint" or "Needs a speech server". Without them every feature hides or falls back:

| Feature | Needs | Without it |
| --- | --- | --- |
| Subtitles from speech | speech server | Hidden |
| Sync subtitles to audio | nothing (speech-server words improve it) | Works on detected speech |
| Translate subtitles | AI endpoint | Hidden |
| "Previously on" recaps | AI endpoint and episode summaries | Previous episodes' overviews |
| Smart-collection builder | AI endpoint | Manual rule builder |
| Semantic search | optional embedding model (Settings → AI & models) | Built-in local ranking |
| Match tie-breaker | AI endpoint | Low-confidence titles go to Unmatched |
| Intro/credits detection | nothing | Always local |

**TheIntroDB** lookups (Settings → Media server) are off by default. Each lookup tells a third party which episodes the household owns.

On the first v3 boot after an upgrade, restart recovery re-queues any `queued` or `running` speech-to-text (ASR) rows left over from before the restart, so a transcription in progress at shutdown resumes instead of being lost.
