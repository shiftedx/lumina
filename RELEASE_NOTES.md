# Release notes

## Lumina 0.3.0

Lumina 0.3.0 starts a fresh repository and release baseline, including the existing household media server, library imports and automation, local and remote playback, downloads, collections, recommendations, and Jellyfin-compatible clients.

- **Refresh from the Library.** A visible Refresh action appears beside the library tabs. Members can reload the current view; vault owners also start a full background scan of eligible imported folders. Busy, error, and scan-result feedback keep the action clear.
- **An open interface throughout the app.** Ordinary actions, cards, panels, notices, media frames, and dialogs no longer use decorative outline boxes. Settings fields use underlines; primary actions remain filled, and keyboard focus and selection states remain visible.
- **Quiet page headings.** Automatically focused headings no longer show a rectangular focus outline. Keyboard and television navigation continue to work, and interactive controls retain their focus indicators.
- **Existing playback reliability.** The baseline includes remote quality recovery, persistent HLS quality selections, corrected local seek timestamps, unavailable-source recovery, and subtitle stability.

The published image is `ghcr.io/shiftedx/lumina:0.3.0` for Linux amd64 and arm64. Installation and backup instructions are in the [operator guide](docker/README.md).

This release does not change the database schema. Earlier Git release tags and release downloads have been removed as part of the baseline reset.
