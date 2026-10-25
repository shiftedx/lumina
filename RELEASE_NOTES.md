# Release notes

## Unreleased — security maintenance

- Bound request deadlines, remote metadata reads, and media-probe output; retain guarded streaming and provider credential isolation.
- Enforce shared book/photo upload admission and encoding-independent XML declaration rejection.
- Run one-operation photo and native book parsers behind a Linux Landlock/libseccomp boundary; preserve codec threads, deadlines and cancellation cleanup.
- Refresh bundled runtimes and locked dependencies, including maintained libarchive with RAR5 support and OpenCV model-output compatibility.
- Keep receiver playback capabilities in memory and prevent database restore from reviving revoked receiver links or mobile pairing codes.
- Preserve the current OAuth, single-use mobile QR pairing, durable photo previews and face-indexing features.

**Operator requirement:** photo processing and native PDF/CBR reads require Linux 6.2 or newer with Landlock ABI 3 and seccomp support. Unsupported environments fail closed. Retain the running image with `scripts/preserve-rollback.sh` before replacing it; see the [operator guide](docker/README.md#retaining-a-rollback-image).

## Lumina 0.4.0

- YouTube: opt-in personal sessions; encrypted per-user credentials; disconnect revocation; restricted-video fallback.
- Playback: paused-music activity cleanup; Up next preparation; protected-stream handoff; persistent player dismissal.
- Music: shared queues; listening export; artist/album identity; artwork recovery; responsive lyrics; floating mini player.
- Integrations: Sonarr/Radarr inventory and acquisition controls; Lidarr requests; collected-data views; Chromecast handoff.
- Library: bounded discovery and artwork I/O; accurate scan progress; faster Books/manga loading and page turns.
- Interface: compact settings workspaces; branded dropdowns; square gold switches; subject-aware hero framing and motion.
- Server: household/member resource budgets; GitHub release checks; owner update notifications.
- Apple: native iOS/tvOS playback proof; Duo layout and playback lifecycle fixes.
- Upgrade: schema 32; automatic pre-upgrade backup; retain `app-data/totp-key`; Linux amd64/arm64.

## Lumina 0.3.4

This release includes every change merged since 0.3.2 and the remaining music, Books, member-access, and connection-guidance fixes.

- **First-class music.** Dedicated music browsing and listening, improved artist identity matching and credited photographs, local background artwork, retryable artwork refreshes, and stable discographies across partial scans. Original audio and protected manual edits are retained.
- **Books and comics.** A standalone library and reader for EPUB, PDF, CBZ, and CBR, with private visibility, saved reading progress, alternate-format bundles, bounded native parsers, keyboard navigation, RTL manga controls, and opening covers for empty EPUB chapters.
- **Guided media-vault onboarding.** Owners and invited members receive setup guidance tailored to their media and interests.
- **Clear member library access.** Invitations and member settings group Watch, Listen, Read, and Other files, with separate Ebooks and Manga/comics grants. Existing Books grants retain access to both reading views.
- **Connection guidance.** Sonarr and Radarr show their own example addresses, default ports, and API-key instructions.
- **TV playback fixes retained.** Includes all Roku and Android TV fixes from 0.3.2.
- **Updated README screenshots.** Fresh captures show the current Home and Movies UI staged with actual Blender open-movie stills and official posters.

**Upgrade:** Back up your data, pull `ghcr.io/shiftedx/lumina:0.3.4` (Linux amd64/arm64), then restart Lumina. This release includes the additive music and Books schema upgrades introduced after 0.3.2; Lumina creates an automatic pre-upgrade backup. See the [operator guide](docker/README.md) for upgrade and rollback instructions.

## Lumina 0.3.2

TV playback compatibility and audio reliability fixes.

- **Roku:** Fix launch failures that return to the app home screen, silent playback, and Dolby-audio errors. HLS uses separate video and stereo AAC; compatible direct playback retains its original audio.
- **Android TV:** Fix primary media-source selection and HLS fallback when direct playback is disabled.

**Upgrade:** Pull `ghcr.io/shiftedx/lumina:0.3.2` (Linux amd64/arm64), then restart Lumina. Close and reopen the TV app to refresh playback details. No database schema changes. See the [operator guide](docker/README.md).

## Lumina 0.3.1

Lumina 0.3.1 brings the media vault into focus, from a new member’s first invitation to everyday playback and library activity.

- **Library-wide scan progress.** Header Activity now combines downloads with discovery, indexing, and copy stabilization. Indexing shows checked files against discovered totals across concurrent folders. Completed work stays in the total until the remaining scans finish. Members see aggregate counts; scan controls remain owner-only.
- **Dedicated film and TV playback.** Movies and episodes put the player first, with title, synopsis, metadata, and playback actions below. Episodes provide ordered continuation and autoplay; movies show poster and collection context. Resume, quality, subtitles, theater, fullscreen, picture-in-picture, and TV navigation remain available.
- **A new-member journey.** A visual welcome leads into interests and optional creator follows, with back, retry, and skip controls. Setup state remains durable, and following a creator does not download their media.
- **Bespoke media-vault artwork.** Gold-and-charcoal drawings depict a film archive, listening room, creator studio, and security keys. Invitation and welcome artwork use the vault entrance. Shared-link previews and email banners use the scene appropriate to the message.
- **Visually concise email.** Invitations, request updates, security notices, and delivery tests use illustrated banners, clear status, useful details, and one prominent action. Live text, plain-text alternatives, and mail-client fallbacks remain intact.
- **A consistent interface.** Rounded cards, dialogs, and menus share one treatment. Home has a smaller greeting with the date opposite it; Edit home is in the account menu. Theme options align with other actions and highlight the active row. Featured-image edge fades are removed.
- **Playback retry stability.** DASH recovery saves the playback position before teardown, preventing recovery from jumping back to the first few seconds.

The published image is `ghcr.io/shiftedx/lumina:0.3.1` for Linux amd64 and arm64. Installation, backups, and deployment instructions are in the [operator guide](docker/README.md).

This release does not change the database schema. Existing member and media data are retained when updating the running deployment.

## Lumina 0.3.0

Lumina 0.3.0 starts a fresh repository and release baseline, including the existing household media server, library imports and automation, local and remote playback, downloads, collections, recommendations, and Jellyfin-compatible clients.

- **Refresh from the Library.** A visible Refresh action appears beside the library tabs. Members can reload the current view; vault owners also start a full background scan of eligible imported folders. Busy, error, and scan-result feedback keep the action clear.
- **An open interface throughout the app.** Ordinary actions, cards, panels, notices, media frames, and dialogs no longer use decorative outline boxes. Settings fields use underlines; primary actions remain filled, and keyboard focus and selection states remain visible.
- **Quiet page headings.** Automatically focused headings no longer show a rectangular focus outline. Keyboard and television navigation continue to work, and interactive controls retain their focus indicators.
- **Existing playback reliability.** The baseline includes remote quality recovery, persistent HLS quality selections, corrected local seek timestamps, unavailable-source recovery, and subtitle stability.

The published image is `ghcr.io/shiftedx/lumina:0.3.0` for Linux amd64 and arm64. Installation and backup instructions are in the [operator guide](docker/README.md).

This release does not change the database schema. Earlier Git release tags and release downloads have been removed as part of the baseline reset.
