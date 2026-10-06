# Release notes

## Lumina 2.10.2

- Selected filter chips (Requests, Explore, Streaming and elsewhere) show only the gold underline, without a filled pill behind them. Request chip rows line up with the page edge.

## Lumina 2.10.1

**Upgrade steps** (once):

1. `docker compose down`. The Compose service and container are now named `lumina`, so the old container must stop first.
2. Rename settings in `.env` and any compose override: `sed -i 's/^YTDLP_UI_/LUMINA_/' .env`. Old names are ignored. If an override names the service, rename it to `lumina`.
3. `docker compose up --build -d`.

- **Anime requests work again.** AniList's Cloudflare front had started answering Lumina with a challenge page, so the anime season, schedule and list views failed. Lumina now identifies itself to AniList, and a refused request logs its HTTP status.
- **One name.** Settings use the `LUMINA_` prefix, the Compose service is `lumina`, and the app reports its real version in Diagnostics.
- Lumina is now open source at [github.com/shiftedx/lumina](https://github.com/shiftedx/lumina). [CREDITS.md](CREDITS.md) and Settings → You → About credit yt-dlp, FFmpeg and every other project and service Lumina is built on.

## Lumina 2.10.0

- **Emails that look like Lumina.** Invites, request updates, the two-step lockout alert and the test email share one dark, gold-accented design with the Lumina mark. Request emails show the poster and an *Open in Lumina* button that goes straight to the request or title.
- **Metadata editor.**
  - Move an episode to another season; rescans keep your choice.
  - Choose a series' episode order (aired, DVD or absolute) and, optionally, a TMDB episode group.
  - Rename a person or set their photo once, and it changes on every title.
- **Members.**
  - Owners can add and remove followed channels for a member limited to followed channels (Settings → Members → member → Streaming).
  - Members with library limits now see new titles appear without reloading.
- **Infuse and other Jellyfin apps** can browse by genre, studio and person.
- **Playback keeps going** if the device's audio output stops responding: Lumina turns loudness levelling off for that video and says so.
- **Library.** A network share that hangs mid-scan no longer holds up automatic scans of your other folders. The cache of chosen TMDB artwork is capped at 4 GB. Animated images get a clear "not supported" message.
- **Two-step verification.** Authenticator secrets now use their own key file (`app-data/totp-key`); back it up with your other backups and never delete it. Owners get an email when a member's codes are paused. After upgrading, members are asked for a code once more on devices they had trusted.
- Explore shows *Live now* and *For you* above Browse by category again, without the page jumping.
- Many smaller fixes from the 2.0 and 2.1 reviews: editor saves, bulk undo, history, menus in Safari, ⌘K, Diagnostics wording, touch targets on the live watch page.

## Lumina 2.9.0

- **Sign-in page.**
  - New and upcoming releases fill the screen: films arriving in cinemas, shows with new episodes, and this season's anime. Each comes with its title art and a short caption, and the cycle refreshes daily.
  - The sign-in form sits on a dark glass panel over the art.
  - Only public catalogue art is shown, never anything from your vault, and images come through Lumina so visitors' addresses never reach TMDB. With reduced motion you get a single still.
- **Two-step verification (optional).**
  - Turn it on in Settings → You → Profile with any authenticator app. You get ten single-use recovery codes, and can choose not to be asked again on a trusted device for 30 days.
  - Owners can require it for owners; members are never forced.
  - Infuse and other Jellyfin apps keep working. Without two-step, nothing changes. With it, create an app password in Settings → Connected apps; the guided screen shows the server address, your username and the password to type.
- **Picture-in-picture carries on** to the next episode when autoplay moves on. Your Mac's or phone's media controls can skip to the next episode.
- **Hidden posters stay hidden.** A poster link given to a member with limits stops working once that title is hidden from them.
- **Extra source ports** (Settings → Server → Dashboard, advanced): allow, for example, an Icecast stream on port 8000. Dangerous ports are always refused.
- Home no longer jumps when a shelf turns out to be empty.
- **Security.**
  - App passwords only ever unlock watching and browsing.
  - Account-changing owner actions (resetting passwords or two-step, roles, invites, public address, backup download) need a signed-in browser.
  - After 10 wrong two-step codes in a row, codes are paused (15 minutes, doubling) and a recovery code still works.
  - Posters for members with limits are no longer kept in shared caches.

## Lumina 2.8.1

Hardening for public access, and better TV playback.

- **Up next.** While watching an episode, Up next lists the following episodes in order, across seasons. A film shows its collection, and a film outside a collection shows no Up next at all. When an episode reaches its credits, a *Next episode* card counts down and plays the next one, and fullscreen stays on. Films are offered but never start on their own. An empty queue is no longer shown.
- **Sign-in protection on a public address.**
  - Each visitor is throttled separately, so one bad actor can no longer block sign-in for everyone.
  - Ten wrong passwords pause that account's sign-in from outside your home. It never pauses at home, so you can't be locked out of your own server, and owners can unlock it from Settings.
  - Passwords are stored more strongly, upgraded at the next sign-in, and sign-ins are logged.
  - Signing out of Infuse revokes that device.
- **Safer server-side fetching.**
  - Members' links can only make Lumina fetch ordinary web ports.
  - A crafted playlist can no longer make the download converters read another member's files or reach your home network.
  - AI jobs are capped per member.
- **Stricter browser security headers** on the public address (HSTS, tighter content rules), and your home network address is no longer shown to the internet.
- If the server's access answer is ever incomplete, the app now shows everything and lets the server decide, instead of hiding Streaming or stopping playback.

## Lumina 2.8.0

Per-member library sharing, parental controls and email invitations.

- **Choose which libraries each member sees.** Libraries are Movies, Shows, Anime, Music, and Videos & downloads (plus any extra storage folder). A member sees only the libraries you share with them, everywhere: Home, search, title pages, Requests, and Infuse or any other Jellyfin app.
- **Parental controls.** These are set per member under Settings → Members → member.
  - **Rating limits:** a highest film rating (G to NC-17) and TV rating (TV-Y to TV-MA), and whether titles with no rating are shown. Ratings come from TMDB and your NFO files; *Fill in ratings* adds them to titles you already have.
  - **Streaming limits:** turn off YouTube, Twitch, Kick, live broadcasts or searching the open internet, or allow followed channels only.
  - **Viewing hours and a daily limit**, counted across every device. Members see a "back at 7:00" or "that's today's watching time" message. Give someone *+30 min today* in one click.
  - **Permissions:** saving to the vault, and making requests.
  - **Presets:** Kids, Teen and Guest give you a starting point.
- **Invite people by email.** Enter an address, pick the libraries and limits, and Lumina sends a branded invitation through your Requests email (SMTP) settings, or gives you a link to copy. Invitations last 7 days and can be resent or revoked. Invited people get only what you chose.
- **Public address.** Lumina can also serve a public https address; invitation emails link there.
- Hidden titles never show up anywhere for that member: not in lists, search, live updates, collections, requests or Infuse.

## Lumina 2.7.3

- **Resuming or seeking far into a converted film plays in Firefox again** instead of loading forever. Converted video now carries its real timestamps in every piece, so Firefox no longer places it at twice the resume point.

## Lumina 2.7.2

- **HEVC films no longer spin forever in browsers that only claim to play HEVC** (Firefox on Windows, for example). Lumina now asks the browser's streaming engine, not just its file player, before it sends HEVC. If a browser still fails to show HEVC video, Lumina switches to an H.264 version at the same spot after about 8 seconds at most, with no error shown. It remembers this for that browser, so later plays start straight away.

## Lumina 2.7.1

- Edit home, Add link and the arrows beside every row lose their boxes: they read as quiet controls with a soft wash on hover, like the rest of 2.7.

## Lumina 2.7.0

Visual refinements across the app.

- **No more boxes around text fields.** Every field, select and search box is now a value on a fine rule that turns gold while you type. Empty states, error messages, chips and the Streaming mode switches lost their boxes too.
- **Text is a step smaller** across the app; the big headings keep their size.
- **Settings, cleaned up.**
  - Every control sits in one right-hand column, and switches line up exactly.
  - Labels are no longer repeated next to their switch.
  - Inputs are a sensible width, and the save bar only rises when you have unsaved changes.
  - Captions settings show again.
- **Show advanced settings.** Settings now shows what most people need. A switch under the search box reveals the power-user controls everywhere: transcoding, task schedules, diagnostics, AI tuning, path mappings, anime language profiles, metadata language and more. It's remembered for your account. Search always finds advanced settings and offers to show them.
- **Home's hero is a carousel.** It slides between what you're watching and what's recommended for you, with pips, auto-advance that pauses while you look, swipe and keyboard. Portrait-only artwork now sits over a blurred version of itself instead of between black bars.
- Alignment fixes everywhere:
  - Downloads lines up with every other page.
  - Tabs share one style, and first chips and tabs start on the content edge.
  - The phone player's time no longer collides with the speed control.
  - Several notes that were set in capitals now read as sentences.

## Lumina 2.6.2

- The Requests tab's anime pages load warm after a restart: AniList is asked at most 30 times a minute (its current limit), and a catalog warm-up that AniList refuses is retried 12 minutes later instead of an hour or more later.

## Lumina 2.6.1

- **A whole household browsing YouTube no longer gets the home IP rate-limited.** Every request Lumina makes to YouTube now shares one household budget. What you click (play, search, a trailer) always goes through. Background work gives way and keeps showing its last good results: Live and Popular rows, followed channels, recommendations and hover prefetch. If YouTube refuses a request, all background work pauses and backs off together.
- **Shared instead of repeated.**
  - A channel several members follow is checked once per cycle for the whole household.
  - A video or live stream several members open shares one lookup, whatever quality each member prefers.
  - Opening Subscriptions no longer re-checks every channel if the last check was under 10 minutes ago.
- **Fewer background requests.**
  - Popular rows refresh every ~2 hours instead of every 30 minutes.
  - "Is this channel live?" checks run every 5 minutes instead of every 2.
  - Hover prefetch waits 600 ms, is capped at three for the whole household, and no longer pre-reads video indexes.
  - Recommendations refresh only for members active in the last 3 days.

## Lumina 2.6.0

A new **Requests** tab: discover films, series and anime, watch trailers in place, and ask for them to be added to the vault through the household's Sonarr and Radarr.

- **Discover** opens on a full-width hero of what's trending with its trailer one click away, then this season's anime, trending, upcoming films, popular series, anime airing today, next season's anime and top-rated films.
- **Anime is a kind of its own**, not a TV genre. Its page has a season switcher (Summer · **Fall 2026** · Winter), the season's best as large cards, a week of airing times with countdowns, and everything airing that season. Anime has its own request policy. Series go to Sonarr's anime folder, films to Radarr. Each request asks for **English dub** or **Japanese with subtitles**.
- **Title pages** show the backdrop, logo, cast (voice actors for anime), seasons, studio and next episode. The trailer plays right there in the hero through Lumina's own player.
- **Requests** let you pick seasons and show whether a request goes straight to Sonarr/Radarr or needs an admin. They follow the download (Requested → Approved → Downloading 42% → In your vault), and the title opens in the Library once it lands. If someone has already asked for a title, you're added to that request instead of creating a duplicate.
- **Admins** approve or decline in Manage. Settings → Server → **Requests** holds the Sonarr and Radarr connections with folder mapping, a one-click "Create anime language profiles" for Sonarr, email (SMTP) settings, and per-member policies: who may request films, series or anime, what is approved automatically, and how many per week.
- **Email** goes to admins for new requests, and to the members who asked when a request is approved, declined, ready or fails. Members can turn it off in Settings → You.
- Requests are off until an admin turns them on. Discovery uses TMDB (needs a TMDB key) and AniList.

## Lumina 2.5.1

- **YouTube plays in the video's original language.** Videos with auto-dubbed audio (German, Spanish, Hindi …) could start in a dub; Lumina now always picks the original track.
- **No more mid-play quality drops on YouTube.** 2.5.0 read video pieces four at a time, which YouTube answered with refusals that forced a quality change and buffering; reads are one at a time again, and a refused quick lookup goes straight to the full one.

## Lumina 2.5.0

Playback performance work across all playback types.

- **Library files no longer hang after a resume or a quality change.** A remuxed file (most MKVs, and HEVC in Chrome) that restarted between keyframes used to stall for 10–20 seconds or keep a frozen picture; it now plays from the right spot in about a second.
- **Quality switches in about half a second.** Switching a library file to 720p/480p resumes in ~0.6 s instead of ~2.6 s, without the 1 s freeze after it, and never lands ahead of where you were.
- **Video first.** A Play no longer waits behind the watch page's side panels or the page's own code; resuming a long file starts in about 0.1 s on the LAN.
- **Infuse and other Jellyfin apps** get the whole file's timeline from the start, as Jellyfin does: resuming and seeking in a converted file now land where asked in about a second instead of sometimes never.
- **Live** sits about 7 s behind the broadcast on Twitch and Kick (was 18–19 s), starts faster (Twitch ~1.4 s, under 1 s after hovering a card), keeps YouTube live at its top quality, and has a quality menu; a picked quality shows at once.
- **YouTube** clicks skip a 1–2 s player check and start reading the video while the player loads; YouTube's throttled segments download in parallel.
- **Your home IP stays welcome.** The Live tab's background searches share a per-provider budget (YouTube 300 requests an hour, Twitch 600) and pause on a rate-limit answer; one open Live tab used to ask YouTube for ~2,300 pages an hour.
- Intel QSV encodes cut segments exactly where the player expects them (forced IDR frames), and the hardware check runs at startup instead of delaying the first encode.

## Lumina 2.4.0

- **Live at real scale.** Every live category holds 60 streams (was 8) and shows how many channels are live (for example "59,856 live" in Gaming) from Twitch's directory totals plus the YouTube streams Lumina found. See all keeps loading as you scroll (thousands for Gaming). Live lists refresh every 3 minutes instead of 15.
- **Full Popular rows.** All 25 rows have 28–40 videos instead of often one: each category fetches deeper, thin rows are topped up from related searches, and rows with fewer than 4 are hidden. See all walls keep loading.
- Everything is fetched in the background with back-off, so Streaming still opens instantly; if YouTube rate-limits Lumina, rows keep their last good list.

## Lumina 2.3.1

- Streaming opens on what's live: categories are one compact row of chips at the top instead of a wall of tiles, so Live now, From your channels and Recommended are on the first screen.
- Rows hold their place while they load, so the page no longer jumps as Live now and Recommended arrive.

## Lumina 2.3.0

- **Streaming** replaces Live, Explore and Subscriptions in the navigation (Home · Library · Streaming · Downloads · Settings). Browse shows categories, Live now, From your channels, Recommended for you and Popular rows; Live now and Your channels are views of the same page. Old /live, /explore and /subscriptions links still work.
- **Choose your providers** in Settings → You → Streaming: YouTube is always on, Twitch is on and Kick off by default. A provider you turn off disappears from Streaming, Home's live row and search.
- **Faster, sharper starts.** YouTube videos start at their top quality (4K where offered) instead of 360p, and climb back after a slowdown; hovering a video card for a moment prepares it, so a click usually plays within about 2 seconds (live 1–2 s). Closing a tab releases its stream at once.
- Reloading a Streaming page works; Escape and Back leave a See all wall; search results have a Clear link.

## Lumina 2.2.0

- **Activity** (Settings → Server → Activity, owners only): who is watching what right now, on which app and device, whether it plays directly, is remuxed or transcoded, and whether the transcode runs on Intel QSV, VAAPI or software, with speed and progress. Stop any stream. Server load (CPU, memory, load, transcoder CPU), downloads and recordings in progress, and 90 days of searchable playback history.
- **Live chat** beside live YouTube and Twitch streams (read-only; Twitch needs no account). Recordings save YouTube and Twitch chat again.
- **One ⋯ menu on all cover art**: hover (or focus, or tap) a poster or video card for Play, watchlist, watched, favorite, Edit details and Change artwork, Save to vault and Open channel, plus the reason and Not interested on recommendations. The reason line and the ⋯ under each card are gone.
- **Streams start at the highest quality** and only step down for real bandwidth or buffering trouble. YouTube live no longer plays audio seconds out of sync in Chrome, and live playlists refresh without stalling.
- Web videos show Up next beside the player again in the standard layout; long live titles and the side column fit the window; the Add a link box is tidy.
- Schema 11 adds the playback history table. Rollback: see docker/README.md "2.2.0 rollback".

## Lumina 2.1.1

- Pages use the width of big screens: title pages, channel pages, album and artist pages and the web-video watch page run up to 1680px, and Settings dashboards and tables fill their row.
- With a mouse or trackpad, buttons and text are a size smaller; touch screens and TV remotes keep the larger targets.
- A web video in the standard layout shows Up next beside the player again; theater mode keeps it underneath.

## Lumina 2.1.0

Library scans that run by themselves, folder watching, and a metadata editor in the web app.

- **Scheduled scans.** Each external folder root can be scanned automatically: every 15 minutes, hourly, every 6 hours or nightly. Set it under Settings → Library & storage. "Nightly at" uses the server's time zone, so set `TZ` (for example `America/Chicago`) in your compose environment; it was UTC before.
- **Folder watching.** Lumina checks folder times on a NAS-safe schedule and imports new episodes and movies soon after they finish copying. It sees new, removed and renamed files, not files edited in place; the nightly scan catches those. A share that stops answering is marked unresponsive and skipped without freezing Lumina.
- **Off by default.** Nothing changes on upgrade. We recommend turning on **Watch (5 minutes) plus Nightly** for TV and Movies. A scan that would mark a large part of a folder missing still stops and waits for an admin to confirm.
- **Metadata editor.** Edit titles, overviews, ratings, genres, cast and artwork for movies, shows, seasons and episodes, one at a time or in bulk. Edits are locked against refreshes and rescans, can be reverted to the original source value and undone from history, and Lock this item freezes a whole title. Artwork can come from TMDB or an upload (JPEG, PNG or WebP, up to 15 MiB, re-encoded). Vault owners can edit; Settings → Media server has a switch to let household members edit too. Edits live in Lumina only: nothing is written to NFO files, and Jellyfin apps see them read-only.
- **Backups.** Uploaded artwork is stored in the database, so backups include it.
- **Upgrade and rollback.** The database moves to schema 10 with an automatic `pre-upgrade` backup. Rolling back to 2.0.1 has one extra step; see `docker/README.md`. Edits survive a rollback; locks and uploads are ignored until you upgrade again.
- Not in 2.1.0: moving episodes between seasons, alternate episode orders, and editing a person across titles.

## Lumina 1.0

Lumina 1.0 is the first release: a browser-only app shipped as one Docker container. Identify a build by its source commit (the image's `org.opencontainers.image.revision` label) and Docker image ID.

## Installation

Docker Compose is the supported installation path; follow [`docker/README.md`](docker/README.md). Open `http://127.0.0.1:8765` and create the first administrator with your own password. Complete setup over loopback before enabling the documented HTTPS reverse-proxy configuration.

Persist and protect `app-data/` (database, backups and the built-in Library under `app-data/library/`) and any managed media roots you mount.

## Schema policy

On first start Lumina creates its database and stamps its schema version. A later release that raises the version takes an automatic `pre-upgrade` backup, then applies additive upgrade steps in one transaction; a database from a newer release is refused.

## Backup and restore

Admin → Backups takes verified online database backups; restore is offline with `python -m app.restore` in a one-off container, as described in the operator guide. Media files are backed up separately.

## Baseline

- No shared or default administrator credentials exist.
- Docker publishes only to loopback by default; remote access requires the documented HTTPS proxy contract.
- Sessions are opaque server-side records in SQLite.
- Lumina stores no provider credentials; sources that require sign-in are unavailable.
- Household output paths remain beneath the operator-controlled Library root.
- Media acquisition is limited to public HTTP and HTTPS destinations and guarded native transports.

## Known limits

- H.264/HEVC playback through remux/transcode sessions is verified server-side; seeking in a transcoded file is limited to the part already converted.
- Kick: public VOD and live playback work; recording and scheduling Kick broadcasts are not available. Live Twitch/Kick chat needs a provider account and is not captured.
- Speech-to-text needs an OpenAI-compatible transcription endpoint (Admin → AI); without one, transcripts come only from source captions.
- Publishing a download hard-links it into place, so managed storage roots must be on a filesystem that supports hard links.
- Deliberate size caps apply to exports, note lists, library groups (500), import entry logs and the collection title picker.
