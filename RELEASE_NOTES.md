# Release notes

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
